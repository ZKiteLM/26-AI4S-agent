import math
import re
from collections import Counter
from functools import lru_cache
from uuid import NAMESPACE_URL, uuid5

from qdrant_client import QdrantClient, models
from sqlalchemy import select
from sqlalchemy.orm import Session

from .ai import embed, rerank
from .config import settings
from .models import Block, Chunk, Paper, Version

COLLECTION = "paper_chunks"


def _evidence(block: Block, paper: Paper, version: Version, quote: str | None = None) -> dict:
    return {
        "evidence_id": block.id,
        "paper_id": paper.id,
        "paper_title": paper.title,
        "paper_version_id": version.id,
        "block_id": block.id,
        "page_index": block.page_index,
        "bbox": block.bbox,
        "quote": (quote or block.text)[:3600],
        "file_url": f"/api/v1/versions/{version.id}/file",
    }


def _author_numbers(blocks: list[Block]) -> dict[str, int]:
    title_index = next((i for i, block in enumerate(blocks) if block.type == "title"), None)
    if title_index is None:
        return {}
    heading_index = next(
        (i for i in range(title_index + 1, len(blocks)) if blocks[i].type == "heading"),
        min(title_index + 21, len(blocks)),
    )
    candidates = [
        block
        for block in blocks[title_index + 1 : heading_index]
        if block.type == "paragraph" and len(block.text) < 350
    ]
    if not 2 <= len(candidates) <= 20 or any(block.text.count("@") != 1 for block in candidates):
        return {}
    return {block.id: index for index, block in enumerate(candidates, 1)}


def _context_text(blocks: list[Block], numbers: dict[str, int], all_blocks: list[Block]) -> str:
    body = "\n".join(
        f"[Author {numbers[block.id]}] {block.text}" if block.id in numbers else block.text
        for block in blocks
    )
    if not any(block.id in numbers for block in blocks):
        return body
    footnotes = [
        block.text
        for block in all_blocks
        if block.page_index == 0 and block.type == "page_footnote"
    ]
    return body + ("\nRelated first-page footnotes:\n" + "\n".join(footnotes) if footnotes else "")


def first_page_evidence(db: Session, owner_id: str, paper_id: str) -> dict | None:
    paper = db.get(Paper, paper_id)
    if not paper or paper.owner_id != owner_id or not paper.preferred_version_id:
        return None
    version = db.get(Version, paper.preferred_version_id)
    if not version:
        return None
    blocks = db.scalars(
        select(Block)
        .where(Block.version_id == version.id, Block.page_index == 0, Block.type == "paragraph")
        .order_by(Block.order)
    )
    block = next((item for item in blocks if len(item.text.strip()) >= 100), None)
    return _evidence(block, paper, version) if block else None


def paper_outline(db: Session, owner_id: str, paper_ids: list[str]) -> list[dict]:
    outline = []
    for paper_id in paper_ids:
        paper = db.get(Paper, paper_id)
        if not paper or paper.owner_id != owner_id or not paper.preferred_version_id:
            continue
        blocks = db.scalars(
            select(Block)
            .where(Block.version_id == paper.preferred_version_id)
            .order_by(Block.page_index, Block.order)
        ).all()
        landmarks = [{"index": 0, "page": 1, "title": "Title and author information"}] + [
            {"index": index, "page": block.page_index + 1, "title": block.text[:120]}
            for index, block in enumerate(blocks)
            if block.type == "heading" or (block.type == "page_footnote" and block.page_index == 0)
        ]
        outline.append(
            {
                "paper_id": paper.id,
                "title": paper.title,
                "block_count": len(blocks),
                "sections": landmarks[:90],
            }
        )
    return outline


def read_paper_fragment(
    db: Session, owner_id: str, paper_id: str, start: int, length: int
) -> list[dict]:
    paper = db.get(Paper, paper_id)
    if not paper or paper.owner_id != owner_id or not paper.preferred_version_id:
        return []
    version = db.get(Version, paper.preferred_version_id)
    blocks = db.scalars(
        select(Block).where(Block.version_id == version.id).order_by(Block.page_index, Block.order)
    ).all()
    selected = blocks[start : start + length]
    if not selected:
        return []
    quote = _context_text(selected, _author_numbers(blocks), blocks)
    return [{**_evidence(selected[0], paper, version, quote), "source_type": "read"}]


@lru_cache
def vector_client() -> QdrantClient:
    url = settings().qdrant_url
    return (
        QdrantClient(path=url.removeprefix("local://"))
        if url.startswith("local://")
        else QdrantClient(url=url)
    )


def make_chunks(db: Session, version_id: str) -> list[Chunk]:
    blocks = db.scalars(
        select(Block).where(Block.version_id == version_id).order_by(Block.page_index, Block.order)
    ).all()
    chunks = []
    group: list[Block] = []
    length = 0
    for block in blocks:
        if not block.text.strip():
            continue
        if group and (length + len(block.text) > 2300 or block.type == "heading"):
            chunks.append(_chunk(version_id, group))
            group, length = [], 0
        group.append(block)
        length += len(block.text)
    if group:
        chunks.append(_chunk(version_id, group))
    db.add_all(chunks)
    db.commit()
    return chunks


def _chunk(version_id: str, blocks: list[Block]) -> Chunk:
    return Chunk(
        version_id=version_id,
        block_ids=[x.id for x in blocks],
        text="\n".join(x.text for x in blocks),
        page_start=blocks[0].page_index,
        page_end=blocks[-1].page_index,
    )


def _passages(blocks: list[Block]) -> list[tuple[str, Block, str]]:
    passages = []
    for block in blocks:
        words = block.text.split()
        if not words:
            continue
        parts = []
        current = []
        for word in words:
            if current and len(" ".join(current)) + len(word) > 520:
                parts.append(" ".join(current))
                current = []
            current.append(word)
        if current:
            parts.append(" ".join(current))
        for index, part in enumerate(parts):
            for start in range(0, len(part), 520):
                passages.append(
                    (
                        str(uuid5(NAMESPACE_URL, f"{block.id}:{index}:{start}")),
                        block,
                        part[start : start + 520],
                    )
                )
    return passages


def index_chunks(db: Session, chunks: list[Chunk], paper_id: str, owner_id: str) -> None:
    cfg = settings()
    if not cfg.qdrant_url or not chunks:
        return
    blocks = db.scalars(
        select(Block)
        .where(Block.version_id == chunks[0].version_id)
        .order_by(Block.page_index, Block.order)
    ).all()
    passages = _passages(blocks)
    client = vector_client()
    for start in range(0, len(passages), 24):
        batch = passages[start : start + 24]
        vectors = embed([text for _, _, text in batch])
        if vectors is None:
            return
        if not client.collection_exists(COLLECTION):
            client.create_collection(
                COLLECTION,
                vectors_config=models.VectorParams(
                    size=len(vectors[0]), distance=models.Distance.COSINE
                ),
            )
        client.upsert(
            COLLECTION,
            points=[
                models.PointStruct(
                    id=passage_id,
                    vector=vector,
                    payload={
                        "owner_id": owner_id,
                        "paper_id": paper_id,
                        "version_id": block.version_id,
                        "kind": "child",
                    },
                )
                for (passage_id, block, _), vector in zip(batch, vectors, strict=True)
            ],
        )


def remove_version_index(version_id: str) -> None:
    cfg = settings()
    if cfg.qdrant_url:
        client = vector_client()
        if client.collection_exists(COLLECTION):
            client.delete(
                COLLECTION,
                points_selector=models.FilterSelector(
                    filter=models.Filter(
                        must=[
                            models.FieldCondition(
                                key="version_id", match=models.MatchValue(value=version_id)
                            )
                        ]
                    )
                ),
            )


def search(
    db: Session, owner_id: str, paper_ids: list[str], query: str, limit: int = 8
) -> list[dict]:
    owned = db.scalars(
        select(Paper).where(Paper.owner_id == owner_id, Paper.id.in_(paper_ids))
    ).all()
    if not owned:
        return []
    ids = [x.preferred_version_id for x in owned if x.preferred_version_id]
    chunks = db.scalars(select(Chunk).where(Chunk.version_id.in_(ids))).all()
    blocks = db.scalars(
        select(Block).where(Block.version_id.in_(ids)).order_by(Block.page_index, Block.order)
    ).all()
    passages = _passages(blocks)
    if not passages:
        return []
    passage_by_id = {key: (block, text) for key, block, text in passages}
    tokenize = lambda value: re.findall(r"\w+", value.casefold())
    terms = set(tokenize(query))
    documents = [Counter(tokenize(text)) for _, _, text in passages]
    lengths = [sum(counts.values()) for counts in documents]
    average_length = sum(lengths) / len(lengths)
    frequency = Counter(term for counts in documents for term in counts)

    def bm25(index: int) -> float:
        return sum(
            math.log(1 + (len(documents) - frequency[term] + 0.5) / (frequency[term] + 0.5))
            * documents[index][term]
            * 2.2
            / (documents[index][term] + 1.2 * (0.25 + 0.75 * lengths[index] / average_length))
            for term in terms
            if documents[index][term]
        )

    lexical = sorted(range(len(passages)), key=bm25, reverse=True)
    lexical_ids = [passages[index][0] for index in lexical if bm25(index) > 0][: max(limit * 4, 24)]
    ranked_ids = {key: 1 / (60 + index) for index, key in enumerate(lexical_ids)}
    cfg = settings()
    if cfg.qdrant_url and (vectors := embed([query])):
        client = vector_client()
        if client.collection_exists(COLLECTION):
            hits = client.query_points(
                COLLECTION,
                query=vectors[0],
                query_filter=models.Filter(
                    must=[
                        models.FieldCondition(
                            key="owner_id", match=models.MatchValue(value=owner_id)
                        ),
                        models.FieldCondition(key="paper_id", match=models.MatchAny(any=paper_ids)),
                        models.FieldCondition(key="kind", match=models.MatchValue(value="child")),
                    ]
                ),
                limit=max(limit * 3, 20),
            ).points
            for i, hit in enumerate(hits):
                if str(hit.id) in passage_by_id:
                    ranked_ids[str(hit.id)] = ranked_ids.get(str(hit.id), 0) + 1 / (60 + i)
    ordered = sorted(ranked_ids, key=ranked_ids.get, reverse=True)[: max(limit * 4, 24)]
    order = rerank(query, [passage_by_id[key][1] for key in ordered])
    parent_by_block = {block_id: chunk for chunk in chunks for block_id in chunk.block_ids}
    block_by_id = {block.id: block for block in blocks}
    author_numbers = _author_numbers(blocks)
    results = []
    seen_parents = set()
    for index in order:
        if not 0 <= index < len(ordered):
            continue
        block = passage_by_id[ordered[index]][0]
        parent = parent_by_block.get(block.id)
        parent_id = parent.id if parent else block.id
        if parent_id in seen_parents:
            continue
        seen_parents.add(parent_id)
        siblings = (
            [block_by_id[key] for key in parent.block_ids if key in block_by_id]
            if parent
            else [block]
        )
        quote = _context_text(siblings, author_numbers, blocks)
        if len(quote) > 2400:
            position = max(0, quote.find(block.text[:80]))
            quote = quote[max(0, position - 700) : max(0, position - 700) + 2400]
        version = db.get(Version, block.version_id)
        paper = db.get(Paper, version.paper_id)
        results.append(
            {
                **_evidence(block, paper, version, quote),
                "matched_text": passage_by_id[ordered[index]][1],
            }
        )
        if len(results) == limit:
            break
    return results
