"""Idempotently link existing library papers and citation edges to scholarly works."""

import logging

from sqlalchemy import select

from services.api.app.citation_identity import link_paper_to_work, upsert_work
from services.api.app.db import SessionLocal
from services.api.app.models import CitationEdge, Paper, ScholarlyCitationEdge

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def backfill() -> None:
    with SessionLocal() as db:
        paper_ids = db.scalars(select(Paper.id).where(Paper.work_id.is_(None))).all()
        for paper_id in paper_ids:
            try:
                paper = db.get(Paper, paper_id)
                work = upsert_work(db, {
                    "title": paper.title, "authors": paper.authors, "year": paper.year,
                    "abstract": paper.abstract, "doi": paper.doi, "arxiv_id": paper.arxiv_id,
                    "openalex_id": paper.openalex_id, "local_paper_id": paper.id,
                }, "local")
                link_paper_to_work(db, paper, work)
                db.commit()
            except Exception:
                db.rollback()
                logger.exception("Could not link paper %s", paper_id)
        for edge in db.scalars(select(CitationEdge)):
            citing = db.get(Paper, edge.citing_paper_id)
            cited = db.get(Paper, edge.cited_paper_id)
            if not citing or not cited or not citing.work_id or not cited.work_id:
                continue
            exists = db.scalar(select(ScholarlyCitationEdge).where(
                ScholarlyCitationEdge.citing_work_id == citing.work_id,
                ScholarlyCitationEdge.cited_work_id == cited.work_id,
            ))
            if not exists:
                db.add(ScholarlyCitationEdge(
                    citing_work_id=citing.work_id, cited_work_id=cited.work_id,
                    sources=[edge.source],
                ))
                db.commit()
        logger.info("Linked %s papers; preserved all existing library records", len(paper_ids))


if __name__ == "__main__":
    backfill()
