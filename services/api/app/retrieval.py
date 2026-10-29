import re
from functools import lru_cache

from qdrant_client import QdrantClient, models
from sqlalchemy import select
from sqlalchemy.orm import Session

from .ai import embed, rerank
from .config import settings
from .models import Block, Chunk, Paper, Version

COLLECTION = "paper_chunks"


def _evidence(block: Block, paper: Paper, version: Version) -> dict:
    return {
        "evidence_id": block.id,
        "paper_id": paper.id,
        "paper_title": paper.title,
        "paper_version_id": version.id,
        "block_id": block.id,
        "page_index": block.page_index,
        "bbox": block.bbox,
        "quote": block.text[:1800],
        "file_url": f"/api/v1/versions/{version.id}/file",
    }


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


def index_chunks(chunks: list[Chunk], paper_id: str, owner_id: str) -> None:
    cfg = settings()
    if not cfg.qdrant_url or not chunks:
        return
    client = vector_client()
    for start in range(0, len(chunks), 24):
        batch = chunks[start : start + 24]
        vectors = embed([x.text for x in batch])
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
                    id=chunk.id,
                    vector=vector,
                    payload={
                        "owner_id": owner_id,
                        "paper_id": paper_id,
                        "version_id": chunk.version_id,
                    },
                )
                for chunk, vector in zip(batch, vectors, strict=True)
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
    terms = set(re.findall(r"[\w]+", query.lower()))
    lexical = sorted(
        chunks,
        key=lambda x: len(terms & set(re.findall(r"[\w]+", x.text.lower()))) / max(len(terms), 1),
        reverse=True,
    )[: max(limit * 3, 20)]
    ranked_ids = {x.id: 1 / (60 + i) for i, x in enumerate(lexical)}
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
                    ]
                ),
                limit=max(limit * 3, 20),
            ).points
            for i, hit in enumerate(hits):
                ranked_ids[str(hit.id)] = ranked_ids.get(str(hit.id), 0) + 1 / (60 + i)
    ordered = sorted(
        (x for x in chunks if x.id in ranked_ids), key=lambda x: ranked_ids[x.id], reverse=True
    )
    ordered = ordered[: max(limit * 3, 20)]
    order = rerank(query, [x.text for x in ordered])
    chosen = [ordered[i] for i in order[:limit] if 0 <= i < len(ordered)]
    results = []
    for chunk in chosen:
        candidates = [db.get(Block, block_id) for block_id in chunk.block_ids]
        candidates = [
            block
            for block in candidates
            if block
            and block.type not in ("title", "heading")
            and (len(block.text.strip()) >= 40 or block.type in ("equation", "table", "reference"))
        ]
        candidates.sort(
            key=lambda block: len(terms & set(re.findall(r"[\w]+", block.text.lower()))),
            reverse=True,
        )
        version = db.get(Version, chunk.version_id)
        paper = db.get(Paper, version.paper_id)
        for block in candidates[:2]:
            results.append(_evidence(block, paper, version))
    return results
