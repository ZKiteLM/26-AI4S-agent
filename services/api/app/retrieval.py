"""Persistent lexical and vector retrieval over ordered PDF blocks."""

from functools import lru_cache
from uuid import NAMESPACE_URL, uuid5

from qdrant_client import QdrantClient, models
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import lexical
from .ai import embed
from .config import settings
from .models import Block, Chunk, Paper, Version

COLLECTION = "paper_chunks"


def _selected(db: Session, owner_id: str, paper_ids: list[str]) -> dict[str, tuple[Paper, Version]]:
    papers = db.scalars(
        select(Paper).where(Paper.owner_id == owner_id, Paper.id.in_(paper_ids))
    ).all()
    selected = {}
    for paper in papers:
        version = (
            db.get(Version, paper.preferred_version_id) if paper.preferred_version_id else None
        )
        if version and version.parse_status == "completed":
            selected[paper.id] = (paper, version)
    return selected


def _evidence(
    block: Block,
    paper: Paper,
    version: Version,
    text: str,
    start: int,
    score: float | None = None,
    evidence_id: str | None = None,
) -> dict:
    return {
        "evidence_id": evidence_id or block.id,
        "paper_id": paper.id,
        "paper_title": paper.title,
        "paper_version_id": version.id,
        "block_id": block.id,
        "page_index": block.page_index,
        "bbox": block.bbox,
        "quote": text,
        "start": start,
        "length": 1,
        "score": score,
        "file_url": f"/api/v1/versions/{version.id}/file",
    }


def paper_outline(db: Session, owner_id: str, paper_ids: list[str]) -> list[dict]:
    outline = []
    for paper_id, (paper, version) in _selected(db, owner_id, paper_ids).items():
        blocks = db.scalars(
            select(Block)
            .where(Block.version_id == version.id)
            .order_by(Block.page_index, Block.order)
        ).all()
        sections = [{"index": 0, "page": 1, "title": "Title page"}]
        sections.extend(
            {"index": index, "page": block.page_index + 1, "title": block.text[:100]}
            for index, block in enumerate(blocks)
            if block.type == "heading"
        )
        outline.append(
            {
                "paper_id": paper_id,
                "version_id": version.id,
                "title": paper.title,
                "block_count": len(blocks),
                "sections": sections[:80],
            }
        )
    return outline


def read_paper_fragment(
    db: Session, owner_id: str, paper_id: str, start: int, length: int
) -> list[dict]:
    selected = _selected(db, owner_id, [paper_id])
    if paper_id not in selected or start < 0 or not 1 <= length <= 16:
        return []
    paper, version = selected[paper_id]
    blocks = db.scalars(
        select(Block).where(Block.version_id == version.id).order_by(Block.page_index, Block.order)
    ).all()
    result = []
    total_chars = 0
    for index in range(start, min(start + length, len(blocks))):
        block = blocks[index]
        text = block.markdown or block.text
        if result and total_chars + len(text) > 18000:
            break
        result.append(_evidence(block, paper, version, text, index))
        total_chars += len(text)
    return result


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
    chunks, group, size = [], [], 0
    for block in blocks:
        if not block.text.strip():
            continue
        if group and (size + len(block.text) > 2300 or block.type == "heading"):
            chunks.append(
                Chunk(
                    version_id=version_id,
                    block_ids=[x.id for x in group],
                    text="\n".join(x.text for x in group),
                    page_start=group[0].page_index,
                    page_end=group[-1].page_index,
                )
            )
            group, size = [], 0
        group.append(block)
        size += len(block.text)
    if group:
        chunks.append(
            Chunk(
                version_id=version_id,
                block_ids=[x.id for x in group],
                text="\n".join(x.text for x in group),
                page_start=group[0].page_index,
                page_end=group[-1].page_index,
            )
        )
    db.add_all(chunks)
    db.commit()
    return chunks


def _passages(blocks: list[Block], paper_id: str, owner_id: str) -> list[dict]:
    passages = []
    for index, block in enumerate(blocks):
        text = block.markdown or block.text
        if not text.strip():
            continue
        # Keep one Block as the navigation unit. Long blocks get several search passages.
        for offset in range(0, len(text), 800):
            part = text[offset : offset + 800]
            passages.append(
                {
                    "id": str(uuid5(NAMESPACE_URL, f"{block.id}:{offset}")),
                    "owner_id": owner_id,
                    "paper_id": paper_id,
                    "version_id": block.version_id,
                    "block_id": block.id,
                    "start": index,
                    "length": 1,
                    "page_index": block.page_index,
                    "bbox": block.bbox,
                    "text": part,
                }
            )
    return passages


def index_chunks(db: Session, chunks: list[Chunk], paper_id: str, owner_id: str) -> None:
    if not chunks:
        return
    blocks = db.scalars(
        select(Block)
        .where(Block.version_id == chunks[0].version_id)
        .order_by(Block.page_index, Block.order)
    ).all()
    passages = _passages(blocks, paper_id, owner_id)
    lexical.index_passages(passages)
    if not settings().qdrant_url:
        return
    client = vector_client()
    for offset in range(0, len(passages), 24):
        batch = passages[offset : offset + 24]
        vectors = embed([item["text"] for item in batch])
        if vectors is None:
            raise RuntimeError("Embedding endpoint is not configured")
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
                models.PointStruct(id=item["id"], vector=vector, payload=item)
                for item, vector in zip(batch, vectors, strict=True)
            ],
        )


def remove_version_index(version_id: str) -> None:
    lexical.remove_version(version_id)
    if settings().qdrant_url:
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


def _results(
    db: Session, selected: dict[str, tuple[Paper, Version]], hits: list[dict]
) -> list[dict]:
    result = []
    for hit in hits:
        pair = selected.get(hit.get("paper_id"))
        if not pair or hit.get("version_id") != pair[1].id:
            continue
        block = db.get(Block, hit.get("block_id"))
        if block and block.version_id == pair[1].id:
            result.append(
                _evidence(
                    block,
                    pair[0],
                    pair[1],
                    hit["text"],
                    hit["start"],
                    hit.get("score"),
                    hit.get("id"),
                )
            )
    return result


def search_bm25(
    db: Session, owner_id: str, paper_ids: list[str], query: str, limit: int = 6
) -> list[dict]:
    selected = _selected(db, owner_id, paper_ids)
    if not selected:
        return []
    hits = lexical.search(
        query, owner_id, list(selected), [version.id for _, version in selected.values()], limit
    )
    return _results(db, selected, hits)


def search_semantic(
    db: Session, owner_id: str, paper_ids: list[str], query: str, limit: int = 6
) -> list[dict]:
    selected = _selected(db, owner_id, paper_ids)
    if not selected or not settings().qdrant_url:
        return []
    client = vector_client()
    if not client.collection_exists(COLLECTION):
        return []
    vectors = embed([query])
    if not vectors:
        return []
    hits = client.query_points(
        COLLECTION,
        query=vectors[0],
        query_filter=models.Filter(
            must=[
                models.FieldCondition(key="owner_id", match=models.MatchValue(value=owner_id)),
                models.FieldCondition(key="paper_id", match=models.MatchAny(any=list(selected))),
                models.FieldCondition(
                    key="version_id",
                    match=models.MatchAny(any=[v.id for _, v in selected.values()]),
                ),
            ]
        ),
        limit=limit,
    ).points
    return _results(db, selected, [{**hit.payload, "score": hit.score} for hit in hits])
