"""Ownership checks and stable response serializers shared by API routes."""

from fastapi import HTTPException
from sqlalchemy.orm import Session

from ..models import BackgroundTask, Paper, Version


def owned_paper(db: Session, paper_id: str, user: str) -> Paper:
    """Check ownership before any route exposes or mutates a paper."""
    paper = db.get(Paper, paper_id)
    if not paper or paper.owner_id != user:
        raise HTTPException(404, "Paper not found")
    return paper


def owned_version(db: Session, version_id: str, user: str) -> Version:
    """Versions inherit access from their parent paper."""
    version = db.get(Version, version_id)
    if not version:
        raise HTTPException(404, "Version not found")
    owned_paper(db, version.paper_id, user)
    return version


def paper_json(paper: Paper) -> dict:
    return {
        key: getattr(paper, key)
        for key in (
            "id",
            "title",
            "authors",
            "abstract",
            "doi",
            "arxiv_id",
            "openalex_id",
            "year",
            "preferred_version_id",
            "source",
            "created_at",
        )
    }


def version_json(version: Version) -> dict:
    return {
        key: getattr(version, key)
        for key in (
            "id",
            "paper_id",
            "label",
            "filename",
            "page_count",
            "parse_status",
            "created_at",
        )
    }


def task_json(task: BackgroundTask) -> dict:
    return {
        key: getattr(task, key)
        for key in ("id", "type", "status", "progress", "result", "error", "retries", "created_at")
    }
