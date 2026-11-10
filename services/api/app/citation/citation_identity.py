"""Stable, conservative identity mapping for scholarly works."""

import re
from urllib.parse import unquote

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Paper, ScholarlyAliasConflict, ScholarlyWork, ScholarlyWorkAlias, now

FIELDS = {"doi": "doi", "arxiv": "arxiv_id", "openalex": "openalex_id", "s2": "s2_paper_id"}


def normalize_identifier(namespace: str, value: str | None) -> str | None:
    if not value:
        return None
    text = unquote(value.strip()).strip(" /.")
    if namespace == "doi":
        text = re.sub(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", "", text, flags=re.I)
        return text.lower() if text.startswith("10.") and "/" in text else None
    if namespace == "arxiv":
        text = re.sub(r"^(?:https?://arxiv\.org/(?:abs|pdf)/|arxiv:\s*)", "", text, flags=re.I)
        text = re.sub(r"\.pdf$", "", text, flags=re.I)
        text = re.sub(r"v\d+$", "", text, flags=re.I)
        return text.lower() if re.fullmatch(r"(?:\d{4}\.\d{4,5}|[\w.-]+/\d{7})", text) else None
    if namespace == "openalex":
        match = re.search(r"(?:^|/)(W\d+)$", text, flags=re.I)
        return match.group(1).upper() if match else None
    if namespace == "s2":
        return text.upper() if re.fullmatch(r"[0-9a-fA-F]{40}", text) else None
    raise ValueError(f"Unknown identifier namespace: {namespace}")


def identifiers(metadata: dict) -> dict[str, str]:
    return {
        namespace: normalized
        for namespace, field in FIELDS.items()
        if (normalized := normalize_identifier(namespace, metadata.get(field)))
    }


def resolve_existing_work(db: Session, ids: dict[str, str]) -> ScholarlyWork | None:
    matches = {
        alias.work_id
        for namespace, value in ids.items()
        for alias in db.scalars(
            select(ScholarlyWorkAlias).where(
                ScholarlyWorkAlias.namespace == namespace,
                ScholarlyWorkAlias.normalized_value == value,
            )
        )
    }
    if len(matches) > 1:
        raise ValueError("Conflicting scholarly identifiers point to different works")
    return db.get(ScholarlyWork, next(iter(matches))) if matches else None


def upsert_work(
    db: Session, metadata: dict, source: str, *, existing: ScholarlyWork | None = None
) -> ScholarlyWork:
    ids = identifiers(metadata)
    matched = resolve_existing_work(db, ids)
    if existing and matched and existing.id != matched.id:
        raise ValueError("Identifier conflict; work was not merged")
    work = existing or matched
    if work:
        for namespace, value in ids.items():
            old = normalize_identifier(namespace, getattr(work, FIELDS[namespace]))
            if old and old != value:
                raise ValueError(f"Conflicting {namespace} identifiers; work was not merged")
    else:
        if not ids and not metadata.get("local_paper_id"):
            raise ValueError("A reliable identifier is required for an external work")
        work = ScholarlyWork()
        db.add(work)
        db.flush()
    for field in ("title", "authors", "year", "abstract"):
        value = metadata.get(field)
        if value and (
            not getattr(work, field) or (work.metadata_source == "local" and source != "local")
        ):
            setattr(work, field, value)
    for namespace, value in ids.items():
        if not getattr(work, FIELDS[namespace]):
            setattr(work, FIELDS[namespace], value)
        alias = db.scalar(
            select(ScholarlyWorkAlias).where(
                ScholarlyWorkAlias.namespace == namespace,
                ScholarlyWorkAlias.normalized_value == value,
            )
        )
        if not alias:
            db.add(
                ScholarlyWorkAlias(
                    work_id=work.id,
                    namespace=namespace,
                    normalized_value=value,
                    source=source,
                )
            )
    if metadata.get("citation_count") is not None and (
        work.citation_count is None or source == work.citation_source
    ):
        work.citation_count = metadata["citation_count"]
        work.citation_source = source
    if source != "local":
        work.metadata_source = source
    work.updated_at = now()
    db.flush()
    return work


def link_paper_to_work(db: Session, paper: Paper, work: ScholarlyWork) -> None:
    if paper.work_id and paper.work_id != work.id:
        raise ValueError("Paper is already linked to a different work")
    paper.work_id = work.id
    if work.openalex_id and not paper.openalex_id:
        paper.openalex_id = f"https://openalex.org/{work.openalex_id}"
    db.flush()


def record_identity_conflicts(db: Session, metadata: dict, incoming_work_id: str) -> None:
    """Persist ambiguous aliases after rolling back the rejected identity update."""
    for namespace, value in identifiers(metadata).items():
        alias = db.scalar(
            select(ScholarlyWorkAlias).where(
                ScholarlyWorkAlias.namespace == namespace,
                ScholarlyWorkAlias.normalized_value == value,
            )
        )
        if alias and alias.work_id != incoming_work_id:
            db.add(
                ScholarlyAliasConflict(
                    namespace=namespace,
                    normalized_value=value,
                    existing_work_id=alias.work_id,
                    incoming_work_id=incoming_work_id,
                    reason="Different works claim the same identifier",
                )
            )
    db.commit()
