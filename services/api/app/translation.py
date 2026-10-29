import json
import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from .ai import generate
from .models import Block, Translation


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
        if translatable:
            source = [{"id": block.id, "text": block.text} for block in translatable]
            answer = generate(
                "Translate academic text faithfully. Preserve formulas, citations, and code. "
                "Return only a JSON object mapping each supplied id to its translation; no markdown.",
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
                text = block.text
            elif not isinstance(text, str) or not text.strip():
                text = generate(
                    "Translate academic text faithfully. Preserve formulas, citations, and code. "
                    "Return the translation only.",
                    f"Translate to {target_lang}:\n{block.text}",
                )
            item = Translation(block_id=block.id, target_lang=target_lang, text=text)
            db.add(item)
            cached[block.id] = item
        db.commit()
    return [
        {"block_id": block.id, "page_index": block.page_index, "text": cached[block.id].text}
        for block in blocks
    ]
