"""Verify citation links within a fixed set of library papers."""

import logging
import re
import unicodedata
from datetime import UTC, timedelta

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..models import Block, GraphView, GraphViewNode, Paper, ScholarlyWork, Version, now
from .citation_graph_service import _edge, _state, attach_identity
from .citation_identity import identifiers, normalize_identifier
from .citation_providers import OpenAlexProvider, SemanticScholarProvider

logger = logging.getLogger(__name__)
MAX_S2_REFERENCES = 5000


def build_internal_graph(db: Session, view: GraphView, progress=None) -> list[str]:
    """Only persist edges whose endpoints are selected graph seeds."""
    seeds = db.scalars(
        select(GraphViewNode).where(
            GraphViewNode.graph_id == view.id, GraphViewNode.is_seed.is_(True)
        )
    ).all()
    works = [db.get(ScholarlyWork, node.work_id) for node in seeds]
    pdf_verified = set()
    for work in works:
        if _local_pdf_references(db, view, work, works):
            pdf_verified.add(work.id)
    db.commit()
    for work in works:
        attach_identity(db, work)
    selected = {
        (namespace, value): work.id
        for work in works
        for namespace, value in identifiers(vars(work)).items()
    }
    errors = []
    for index, work in enumerate(works):
        provider = OpenAlexProvider() if work.openalex_id else SemanticScholarProvider()
        if not (work.openalex_id or provider._identifier(vars(work))):
            if work.id not in pdf_verified:
                errors.append(f"{work.title}: 缺少可核验的论文标识及 PDF 参考文献")
            continue
        source = provider.name + "_internal"
        state = _state(db, work.id, source, "references")
        expiry = state.expires_at
        if expiry and expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=UTC)
        if state.status == "complete" and expiry and expiry > now():
            if progress:
                progress((index + 1) / len(works))
            continue
        db.commit()
        try:
            try:
                matches, total, complete, cursor = _references(provider, work, selected)
            except (httpx.HTTPError, ValueError, TypeError, RuntimeError):
                backup = SemanticScholarProvider()
                if provider.name != "openalex" or not backup._identifier(vars(work)):
                    raise
                provider = backup
                source = "semantic_scholar_internal"
                state = _state(db, work.id, source, "references")
                matches, total, complete, cursor = _references(provider, work, selected)
            for target in matches - {None, work.id}:
                _edge(db, work.id, target, source)
            state.status = "complete" if complete else "partial"
            state.fetched_count = total
            state.total_count = total if complete else None
            state.next_cursor = None if complete else cursor
            state.fetched_at = now()
            state.expires_at = now() + timedelta(hours=settings().citation_references_ttl_hours)
            state.last_error = None
            if not complete:
                errors.append(f"{work.title}: 参考文献数量超出单次核验上限")
        except (httpx.HTTPError, ValueError, TypeError, RuntimeError) as exc:
            logger.warning("Internal citation lookup failed for %s: %s", work.id, exc)
            state.status = "failed"
            state.last_error = str(exc)[:500]
            if work.id not in pdf_verified:
                errors.append(f"{work.title}: 引用数据查询失败")
        db.commit()
        if progress:
            progress((index + 1) / len(works))
    return errors


def _plain(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", unicodedata.normalize("NFKD", text).casefold())


def _local_pdf_references(
    db: Session, view: GraphView, source: ScholarlyWork, selected: list[ScholarlyWork]
) -> bool:
    paper = db.scalar(
        select(Paper).where(Paper.owner_id == view.owner_id, Paper.work_id == source.id)
    )
    if not paper:
        return False
    version = db.scalar(
        select(Version)
        .where(Version.paper_id == paper.id, Version.parse_status == "completed")
        .order_by(Version.created_at.desc())
    )
    if not version:
        return False
    blocks = db.scalars(
        select(Block).where(Block.version_id == version.id).order_by(Block.page_index, Block.order)
    ).all()
    start = next(
        (
            i + 1
            for i, block in enumerate(blocks)
            if block.text.strip(" #*\\n").casefold() in {"references", "bibliography"}
        ),
        None,
    )
    if start is None:
        return False
    references = []
    for block in blocks[start:]:
        if re.match(r"^(?:appendix|supplementary material)\b", block.text.strip(), re.I):
            break
        references.append(block.text)
    for target in selected:
        if target.id == source.id:
            continue
        title = _plain(target.title)
        arxiv = normalize_identifier("arxiv", target.arxiv_id)
        for entry in references:
            plain = _plain(entry)
            id_match = bool(arxiv and arxiv.replace(".", "") in plain)
            title_match = len(title) >= 15 and title in plain
            if title_match and len(target.title.split()) < 4:
                # Short generic titles need corroboration from the target PDF's first page.
                target_paper = db.scalar(
                    select(Paper).where(Paper.owner_id == view.owner_id, Paper.work_id == target.id)
                )
                target_version = (
                    db.scalar(
                        select(Version)
                        .where(
                            Version.paper_id == target_paper.id, Version.parse_status == "completed"
                        )
                        .order_by(Version.created_at.desc())
                    )
                    if target_paper
                    else None
                )
                extra = (
                    set(re.findall(r"[a-z]{5,}", (target_version.sample_text or "")[:240].lower()))
                    if target_version
                    else set()
                )
                extra -= set(re.findall(r"[a-z]{5,}", target.title.lower()))
                title_match = (
                    len(extra.intersection(set(re.findall(r"[a-z]{5,}", entry.lower())))) >= 2
                )
            if id_match or title_match:
                _edge(db, source.id, target.id, "pdf_references")
                break
    state = _state(db, source.id, "local_pdf", "references")
    state.status = "complete"
    state.fetched_count = len(references)
    state.total_count = len(references)
    state.fetched_at = now()
    state.expires_at = now() + timedelta(hours=settings().citation_references_ttl_hours)
    state.last_error = None
    return True


def _references(provider, work: ScholarlyWork, selected: dict) -> tuple[set, int, bool, str | None]:
    if provider.name == "openalex":
        reference_ids = provider.get_reference_ids(work.openalex_id)
        return (
            {selected.get(("openalex", ref)) for ref in reference_ids},
            len(reference_ids),
            True,
            None,
        )
    paper_id = provider._identifier(vars(work))
    cursor = None
    seen_cursors = set()
    matches = set()
    total = 0
    while total < MAX_S2_REFERENCES:
        page = provider.get_references(paper_id, 100, cursor)
        for item in page.works:
            matches.update(selected.get(alias) for alias in identifiers(item).items())
        total += len(page.works)
        cursor = page.next_cursor
        if not cursor:
            return matches, total, True, None
        if cursor in seen_cursors:
            raise ValueError("Semantic Scholar returned a repeated page cursor")
        seen_cursors.add(cursor)
    return matches, total, False, cursor
