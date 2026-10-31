"""Rebuild child passage vectors from existing parsed paper blocks.

Run with: uv run python scripts/reindex_retrieval.py
"""

import sys
from pathlib import Path

from sqlalchemy import select

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.api.app.config import settings
from services.api.app.db import SessionLocal
from services.api.app.models import Chunk, Paper, Version
from services.api.app.retrieval import index_chunks, remove_version_index


def main() -> None:
    if not settings().qdrant_url:
        raise SystemExit("QDRANT_URL is required for vector reindexing")
    with SessionLocal() as db:
        versions = db.scalars(select(Version)).all()
        for version in versions:
            chunks = db.scalars(select(Chunk).where(Chunk.version_id == version.id)).all()
            if not chunks:
                continue
            paper = db.get(Paper, version.paper_id)
            remove_version_index(version.id)
            index_chunks(db, chunks, paper.id, paper.owner_id)
            print(f"indexed {paper.title} / {version.label}: {len(chunks)} parent chunks")


if __name__ == "__main__":
    main()
