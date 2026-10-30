import json
import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from .ai import generate
from .models import Block, Translation

MATH = re.compile(
    r"\$\$[\s\S]+?\$\$|\\\[[\s\S]+?\\\]|\\\([\s\S]+?\\\)|(?<!\\)\$(?!\$)[^$\n]+?(?<!\\)\$"
)


def protect_math(source: str) -> tuple[str, list[str]]:
    formulas: list[str] = []

    def replace(match: re.Match) -> str:
        token = f"__MATH_{len(formulas)}__"
        formulas.append(match.group())
        return token

    return MATH.sub(replace, source), formulas


def restore_math(text: str, formulas: list[str]) -> str:
    for index, formula in enumerate(formulas):
        token = f"__MATH_{index}__"
        if text.count(token) != 1:
            raise ValueError("Translation did not preserve a math placeholder")
        text = text.replace(token, formula)
    return text


def translate_blocks(db: Session, blocks: list[Block], target_lang: str) -> list[dict]:
    cached = {
        item.block_id: item
        for item in db.scalars(
            select(Translation).where(
                Translation.block_id.in_([block.id for block in blocks]),
                Translation.target_lang == target_lang,
            )
        )
    }
    pending = [block for block in blocks if block.id not in cached]
    for start in range(0, len(pending), 6):
        batch = pending[start : start + 6]
        translatable = [block for block in batch if block.type not in ("equation", "code")]
        translated = {}
        protected = {block.id: protect_math(block.markdown or block.text) for block in translatable}
        if translatable:
            source = [{"id": block.id, "text": protected[block.id][0]} for block in translatable]
            answer = generate(
                "Translate academic Markdown faithfully. Preserve Markdown structure, citations, "
                "code, and every __MATH_N__ token exactly once. "
                "Return only a JSON object mapping each supplied id to its translation.",
                f"Target language: {target_lang}\nSegments: {json.dumps(source, ensure_ascii=False)}",
                max_tokens=1800,
            )
            try:
                translated = json.loads(re.sub(r"^```(?:json)?\s*|\s*```$", "", answer.strip()))
                if not isinstance(translated, dict):
                    translated = {}
            except json.JSONDecodeError:
                translated = {}
        for block in batch:
            text = translated.get(block.id)
            if block.type in ("equation", "code"):
                text = block.markdown or block.text
            elif not isinstance(text, str) or not text.strip():
                text = generate(
                    "Translate academic Markdown faithfully. Keep citations, code, and every "
                    "__MATH_N__ token exactly once. Return only the translation.",
                    f"Translate to {target_lang}:\n{protected[block.id][0]}",
                )
            if block.type not in ("equation", "code"):
                try:
                    text = restore_math(text, protected[block.id][1])
                except ValueError:
                    retry = generate(
                        "Translate academic Markdown. Preserve every __MATH_N__ token exactly "
                        "once. Return only the translation.",
                        f"Translate to {target_lang}:\n{protected[block.id][0]}",
                    )
                    text = restore_math(retry, protected[block.id][1])
            item = Translation(block_id=block.id, target_lang=target_lang, text=text)
            db.add(item)
            cached[block.id] = item
        db.commit()
    return [
        {"block_id": block.id, "page_index": block.page_index, "text": cached[block.id].text}
        for block in blocks
    ]
