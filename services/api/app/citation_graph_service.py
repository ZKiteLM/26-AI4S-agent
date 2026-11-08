"""Persistent, bounded citation neighborhoods and user-owned graph views."""

import logging
from datetime import UTC, timedelta

import httpx
import networkx as nx
from sqlalchemy import select
from sqlalchemy.orm import Session

from .citation_identity import link_paper_to_work, record_identity_conflicts, upsert_work
from .citation_providers import (
    OpenAlexProvider,
    SemanticScholarProvider,
    provider_for,
    resolve_paper,
)
from .config import settings
from .graphs import _pagerank
from .models import (
    CitationFetchState,
    GraphView,
    GraphViewNode,
    Paper,
    ScholarlyCitationEdge,
    ScholarlyWork,
    now,
)

logger = logging.getLogger(__name__)
MAX_NODES = 500


def add_seed_paper(db: Session, view: GraphView, paper: Paper) -> ScholarlyWork:
    if paper.work_id:
        work = db.get(ScholarlyWork, paper.work_id)
    else:
        work = upsert_work(
            db,
            {
                "title": paper.title,
                "authors": paper.authors,
                "year": paper.year,
                "abstract": paper.abstract,
                "doi": paper.doi,
                "arxiv_id": paper.arxiv_id,
                "openalex_id": paper.openalex_id,
                "local_paper_id": paper.id,
            },
            "local",
        )
        link_paper_to_work(db, paper, work)
    if not db.scalar(
        select(GraphViewNode).where(
            GraphViewNode.graph_id == view.id,
            GraphViewNode.work_id == work.id,
        )
    ):
        db.add(GraphViewNode(graph_id=view.id, work_id=work.id, is_seed=True))
    db.flush()
    return work


def attach_identity(db: Session, work: ScholarlyWork) -> bool:
    if work.openalex_id:
        return True
    metadata = {
        "title": work.title,
        "authors": work.authors,
        "year": work.year,
        "doi": work.doi,
        "arxiv_id": work.arxiv_id,
    }
    db.commit()
    found = resolve_paper(metadata)
    if not found:
        return bool(work.s2_paper_id)
    record, provider = found
    if provider == "semantic_scholar":
        try:
            if openalex := OpenAlexProvider().resolve_paper(record):
                record = {
                    **record,
                    **{key: value for key, value in openalex.items() if value},
                    "s2_paper_id": record["s2_paper_id"],
                }
                provider = "openalex"
        except (httpx.HTTPError, ValueError, TypeError):
            logger.info("OpenAlex enrichment unavailable for work %s", work.id)
    try:
        # An arXiv preprint and its proceedings version may have different valid DOIs.
        if (
            work.doi
            and record.get("doi")
            and work.doi != record["doi"]
            and work.arxiv_id
            and work.arxiv_id == record.get("arxiv_id")
        ):
            record = {**record, "doi": work.doi}
        upsert_work(db, record, provider, existing=work)
        db.commit()
        return True
    except ValueError:
        db.rollback()
        record_identity_conflicts(db, record, work.id)
        logger.warning("Identity conflict for work %s", work.id)
        return False


def _state(db: Session, work_id: str, provider: str, direction: str) -> CitationFetchState:
    state = db.scalar(
        select(CitationFetchState).where(
            CitationFetchState.work_id == work_id,
            CitationFetchState.provider == provider,
            CitationFetchState.direction == direction,
        )
    )
    if not state:
        state = CitationFetchState(work_id=work_id, provider=provider, direction=direction)
        db.add(state)
        db.flush()
    return state


def _edge(db: Session, citing_id: str, cited_id: str, source: str) -> None:
    if citing_id == cited_id:
        return
    edge = db.scalar(
        select(ScholarlyCitationEdge).where(
            ScholarlyCitationEdge.citing_work_id == citing_id,
            ScholarlyCitationEdge.cited_work_id == cited_id,
        )
    )
    if edge:
        if source not in edge.sources:
            edge.sources = [*edge.sources, source]
        edge.last_seen_at = now()
    else:
        db.add(
            ScholarlyCitationEdge(
                citing_work_id=citing_id, cited_work_id=cited_id, sources=[source]
            )
        )


def _cached_neighbors(db: Session, work_id: str, direction: str) -> list[ScholarlyWork]:
    edge_work = (
        ScholarlyCitationEdge.cited_work_id
        if direction == "references"
        else ScholarlyCitationEdge.citing_work_id
    )
    center = (
        ScholarlyCitationEdge.citing_work_id
        if direction == "references"
        else ScholarlyCitationEdge.cited_work_id
    )
    return db.scalars(
        select(ScholarlyWork)
        .join(
            ScholarlyCitationEdge,
            ScholarlyWork.id == edge_work,
        )
        .where(center == work_id)
        .order_by(ScholarlyCitationEdge.first_seen_at, ScholarlyCitationEdge.id)
    ).all()


def fetch_neighbors(
    db: Session, work: ScholarlyWork, direction: str, limit: int, *, force: bool = False
) -> tuple[list[ScholarlyWork], dict]:
    """Fetch one page; failed refreshes leave previous citation facts intact."""
    if not attach_identity(db, work):
        return [], {"status": "unresolved", "has_more": False, "fetched_count": 0}
    primary, identifier = provider_for(work, direction)
    provider = primary
    state = _state(db, work.id, primary.name, direction)
    expires_at = state.expires_at
    if expires_at and expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    fresh = expires_at is not None and expires_at > now()
    if fresh and state.status == "complete" and not force:
        return _cached_neighbors(db, work.id, direction), {
            "status": "complete",
            "has_more": False,
            "fetched_count": state.fetched_count,
            "total_count": state.total_count,
            "provider": state.provider,
            "cached": True,
        }
    cursor = state.next_cursor if fresh and state.status == "partial" and not force else None
    if not fresh or force:
        cursor = None
    db.commit()
    try:
        page = (
            provider.get_references(identifier, limit, cursor)
            if direction == "references"
            else provider.get_citations(identifier, limit, cursor)
        )
    except (httpx.HTTPError, ValueError, TypeError, RuntimeError) as error:
        failure = str(error)
        backup = SemanticScholarProvider()
        if primary.name == "openalex":
            try:
                backup_id = backup._identifier(vars(work))
                if not backup_id:
                    metadata = backup.resolve_paper(vars(work))
                    backup_id = backup._identifier(metadata or {})
                if not backup_id:
                    raise ValueError("No Semantic Scholar identifier")
                provider = backup
                state = _state(db, work.id, provider.name, direction)
                page = (
                    provider.get_references(backup_id, limit, None)
                    if direction == "references"
                    else provider.get_citations(backup_id, limit, None)
                )
            except (httpx.HTTPError, ValueError, TypeError, RuntimeError) as backup_error:
                failure = str(backup_error)
                page = None
        else:
            page = None
        if page is None:
            state.last_error = failure[:500]
            if state.status != "complete" and state.status != "partial":
                state.status = "failed"
            db.commit()
            return _cached_neighbors(db, work.id, direction), {
                "status": "failed",
                "has_more": bool(state.next_cursor),
                "fetched_count": state.fetched_count,
                "error": state.last_error,
            }
    new_works = []
    for item in page.works:
        try:
            neighbor = upsert_work(db, item, page.provider)
        except ValueError:
            logger.warning("Skipping ambiguous citation neighbor: %s", item.get("title"))
            continue
        _edge(
            db,
            work.id if direction == "references" else neighbor.id,
            neighbor.id if direction == "references" else work.id,
            page.provider,
        )
        new_works.append(neighbor)
    state = _state(db, work.id, page.provider, direction)
    state.status = "partial" if page.next_cursor else "complete"
    state.next_cursor = page.next_cursor
    previous_count = state.fetched_count if cursor and page.provider == primary.name else 0
    state.fetched_count = previous_count + len(page.works)
    state.total_count = page.total_count
    state.fetched_at = now()
    state.expires_at = now() + timedelta(
        hours=(
            settings().citation_references_ttl_hours
            if direction == "references"
            else settings().citation_citations_ttl_hours
        )
    )
    state.last_error = None
    db.commit()
    return _cached_neighbors(db, work.id, direction), {
        "status": state.status,
        "has_more": bool(state.next_cursor),
        "fetched_count": state.fetched_count,
        "total_count": state.total_count,
        "provider": page.provider,
        "cached": False,
    }


def expand_view(
    db: Session, view_id: str, work_id: str, direction: str, limit: int, *, force: bool = False
) -> dict:
    view = db.get(GraphView, view_id)
    center = db.scalar(
        select(GraphViewNode).where(
            GraphViewNode.graph_id == view_id,
            GraphViewNode.work_id == work_id,
        )
    )
    if not view or not center:
        raise ValueError("Graph node not found")
    work = db.get(ScholarlyWork, work_id)
    directions = ("references", "citations") if direction == "both" else (direction,)
    statuses = {}
    before_nodes = {
        n.work_id
        for n in db.scalars(
            select(GraphViewNode).where(
                GraphViewNode.graph_id == view_id,
            )
        )
    }
    initial_count = len(before_nodes)
    for current in directions:
        neighbors, statuses[current] = fetch_neighbors(db, work, current, limit, force=force)
        added = 0
        for neighbor in neighbors:
            if neighbor.id not in before_nodes and len(before_nodes) < MAX_NODES and added < limit:
                db.add(GraphViewNode(graph_id=view_id, work_id=neighbor.id, depth=center.depth + 1))
                before_nodes.add(neighbor.id)
                added += 1
        unseen_cached = any(n.id not in before_nodes for n in neighbors)
        statuses[current]["has_more"] = statuses[current]["has_more"] or unseen_cached
        if len(before_nodes) >= MAX_NODES and any(n.id not in before_nodes for n in neighbors):
            statuses[current]["truncated"] = True
        if statuses[current]["status"] == "complete" and not unseen_cached:
            setattr(center, "expanded_" + current, True)
    view.version += 1
    view.updated_at = now()
    view.status = (
        "partial"
        if any(
            x["status"] in ("partial", "failed", "unresolved") or x["has_more"]
            for x in statuses.values()
        )
        else "completed"
    )
    db.commit()
    result = serialize_view(db, view)
    result["expansion"] = {"nodes_added": len(before_nodes) - initial_count, "directions": statuses}
    return result


def serialize_view(db: Session, view: GraphView) -> dict:
    rows = db.scalars(select(GraphViewNode).where(GraphViewNode.graph_id == view.id)).all()
    ids = [row.work_id for row in rows]
    works = {
        work.id: work for work in db.scalars(select(ScholarlyWork).where(ScholarlyWork.id.in_(ids)))
    }
    papers = {
        p.work_id: p
        for p in db.scalars(
            select(Paper).where(
                Paper.owner_id == view.owner_id,
                Paper.work_id.in_(ids),
            )
        )
    }
    edges = db.scalars(
        select(ScholarlyCitationEdge).where(
            ScholarlyCitationEdge.citing_work_id.in_(ids),
            ScholarlyCitationEdge.cited_work_id.in_(ids),
        )
    ).all()
    graph = nx.DiGraph()
    graph.add_nodes_from(ids)
    graph.add_edges_from((edge.citing_work_id, edge.cited_work_id) for edge in edges)
    rank = _pagerank(graph)
    internal = view.config.get("mode") == "internal"
    ranked = sorted(
        (work_id for work_id in ids if graph.in_degree(work_id) > 0),
        key=lambda work_id: (rank.get(work_id, 0), graph.in_degree(work_id)),
        reverse=True,
    )
    foundation = (
        set(ranked[: max(1, (len(ids) + 4) // 5)])
        if graph.number_of_edges() and internal
        else set()
    )
    states = {
        (s.work_id, s.direction): s
        for s in db.scalars(
            select(CitationFetchState).where(
                CitationFetchState.work_id.in_(ids),
            )
        )
    }
    verified_work_ids = (
        {
            state.work_id
            for state in db.scalars(
                select(CitationFetchState).where(
                    CitationFetchState.work_id.in_(ids),
                    CitationFetchState.provider.in_(
                        ("openalex_internal", "semantic_scholar_internal", "local_pdf")
                    ),
                    CitationFetchState.status == "complete",
                )
            )
        }
        if internal
        else set()
    )
    verified = len(verified_work_ids)
    unseen = {work_id: {"references": False, "citations": False} for work_id in ids}
    for edge in db.scalars(
        select(ScholarlyCitationEdge).where(
            (ScholarlyCitationEdge.citing_work_id.in_(ids))
            | (ScholarlyCitationEdge.cited_work_id.in_(ids))
        )
    ):
        if edge.citing_work_id in unseen and edge.cited_work_id not in works:
            unseen[edge.citing_work_id]["references"] = True
        if edge.cited_work_id in unseen and edge.citing_work_id not in works:
            unseen[edge.cited_work_id]["citations"] = True
    return {
        "graph_id": view.id,
        "mode": "internal" if internal else "explore",
        "status": view.status,
        "version": view.version,
        "error": view.error,
        "node_count": len(ids),
        "truncated": len(ids) >= MAX_NODES,
        "nodes": [
            {
                "id": row.work_id,
                "title": works[row.work_id].title,
                "authors": works[row.work_id].authors,
                "year": works[row.work_id].year,
                "abstract": works[row.work_id].abstract,
                "doi": works[row.work_id].doi,
                "arxiv_id": works[row.work_id].arxiv_id,
                "openalex_id": works[row.work_id].openalex_id,
                "s2_paper_id": works[row.work_id].s2_paper_id,
                "citation_count": works[row.work_id].citation_count,
                "citation_source": works[row.work_id].citation_source,
                "pagerank": rank.get(row.work_id, 0),
                "in_degree": graph.in_degree(row.work_id),
                "out_degree": graph.out_degree(row.work_id),
                "role": (
                    "foundation"
                    if row.work_id in foundation
                    else "isolated"
                    if internal and graph.degree(row.work_id) == 0
                    else "derivative"
                    if internal and graph.in_degree(row.work_id) == 0
                    else "connected"
                    if internal
                    else None
                ),
                "in_library": row.work_id in papers,
                "paper_id": papers[row.work_id].id if row.work_id in papers else None,
                "is_seed": row.is_seed,
                "depth": row.depth,
                "references": _state_summary(
                    states.get((row.work_id, "references")), unseen[row.work_id]["references"]
                ),
                "citations": _state_summary(
                    states.get((row.work_id, "citations")), unseen[row.work_id]["citations"]
                ),
            }
            for row in rows
        ],
        "edges": [
            {
                "source": edge.citing_work_id,
                "target": edge.cited_work_id,
                "provenance": edge.sources,
            }
            for edge in edges
        ],
        "metrics": {
            "algorithm": "local PageRank",
            "edge_direction": "citing → cited",
            "verified_papers": verified if internal else None,
            "total_papers": len(ids) if internal else None,
            "interpretation": "Relative to selected papers only; missing source metadata may hide edges"
            if internal
            else None,
        },
    }


def _state_summary(state: CitationFetchState | None, unseen_cached: bool = False) -> dict:
    if not state:
        return {"status": "not_fetched", "has_more": False, "fetched_count": 0}
    return {
        "status": state.status,
        "has_more": bool(state.next_cursor) or unseen_cached,
        "fetched_count": state.fetched_count,
        "total_count": state.total_count,
        "provider": state.provider,
        "error": state.last_error,
    }
