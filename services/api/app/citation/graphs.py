import logging
import re
from concurrent.futures import ThreadPoolExecutor

import httpx
import networkx as nx
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Block, CitationEdge, Paper, Version
from ..paper_discovery.discovery import search_external

logger = logging.getLogger(__name__)


def build_graph(db: Session, owner_id: str, paper_ids: list[str]) -> dict:
    papers = db.scalars(
        select(Paper).where(Paper.id.in_(paper_ids), Paper.owner_id == owner_id)
    ).all()
    by_openalex = {p.openalex_id: p for p in papers if p.openalex_id}
    missing = [paper for paper in papers if not paper.openalex_id]
    with ThreadPoolExecutor(max_workers=5) as pool:
        matches_by_paper = list(pool.map(_search_title, [paper.title for paper in missing]))
    for paper, matches in zip(missing, matches_by_paper, strict=True):
        if matches:
            match = next((x for x in matches if x["title"].lower() == paper.title.lower()), None)
            if match:
                paper.openalex_id = match["openalex_id"]
                by_openalex[paper.openalex_id] = paper
    db.commit()
    edges = []
    identified = [paper for paper in papers if paper.openalex_id]
    with ThreadPoolExecutor(max_workers=5) as pool:
        references_by_paper = list(
            pool.map(_references, [paper.openalex_id for paper in identified])
        )
    for paper, references in zip(identified, references_by_paper, strict=True):
        for cited_id in references:
            cited = by_openalex.get(cited_id)
            if cited and cited.id != paper.id:
                edges.append((paper.id, cited.id))
                existing = db.scalar(
                    select(CitationEdge).where(
                        CitationEdge.citing_paper_id == paper.id,
                        CitationEdge.cited_paper_id == cited.id,
                    )
                )
                if not existing:
                    db.add(
                        CitationEdge(
                            citing_paper_id=paper.id,
                            cited_paper_id=cited.id,
                            source="OpenAlex.referenced_works",
                        )
                    )
    db.commit()
    graph = nx.DiGraph()
    graph.add_nodes_from(p.id for p in papers)
    graph.add_edges_from(edges)
    rank = _pagerank(graph)
    return {
        "nodes": [
            {
                "id": p.id,
                "title": p.title,
                "year": p.year,
                "pagerank": rank.get(p.id, 0),
                "in_degree": graph.in_degree(p.id),
            }
            for p in papers
        ],
        "edges": [
            {"source": source, "target": target, "provenance": "OpenAlex"}
            for source, target in edges
        ],
        "metrics": {"algorithm": "PageRank", "edge_direction": "citing → cited"},
    }


def _pagerank(graph: nx.DiGraph, damping: float = 0.85) -> dict[str, float]:
    nodes = list(graph)
    if not nodes:
        return {}
    size = len(nodes)
    rank = {node: 1 / size for node in nodes}
    for _ in range(100):
        dangling = sum(rank[node] for node in nodes if graph.out_degree(node) == 0)
        updated = {node: (1 - damping) / size + damping * dangling / size for node in nodes}
        for source, target in graph.edges:
            updated[target] += damping * rank[source] / graph.out_degree(source)
        if sum(abs(updated[node] - rank[node]) for node in nodes) < 1e-9:
            return updated
        rank = updated
    return rank


def _search_title(title: str) -> list[dict]:
    try:
        return search_external(title, 3)
    except httpx.HTTPError:
        logger.exception("Could not match OpenAlex title: %s", title)
        return []


def _references(openalex_id: str) -> list[str]:
    try:
        from ..paper_discovery.discovery import openalex_headers

        response = httpx.get(
            "https://api.openalex.org/works/" + openalex_id.rsplit("/", 1)[-1],
            headers=openalex_headers(),
            timeout=20,
        )
        response.raise_for_status()
        return response.json().get("referenced_works", [])
    except httpx.HTTPError:
        logger.exception("Could not fetch OpenAlex references: %s", openalex_id)
        return []


def future_work(db: Session, paper_ids: list[str]) -> list[dict]:
    output = []
    versions = db.scalars(select(Version).where(Version.paper_id.in_(paper_ids))).all()
    for version in versions:
        blocks = db.scalars(select(Block).where(Block.version_id == version.id)).all()
        for block in blocks:
            if re.search(
                r"future work|future research|limitation|未来工作|进一步研究", block.text, re.I
            ):
                paper = db.get(Paper, version.paper_id)
                query = " ".join(re.findall(r"[A-Za-z]{4,}", block.text)[:12])
                followups = []
                if query and len(output) < 5:
                    try:
                        followups = [
                            x
                            for x in search_external(query, 5)
                            if x["year"] and paper.year and x["year"] > paper.year
                        ]
                    except Exception:
                        logger.exception("Could not look up later work for paper %s", paper.id)
                output.append(
                    {
                        "paper_id": version.paper_id,
                        "block_id": block.id,
                        "page_index": block.page_index,
                        "quote": block.text[:1200],
                        "status": "later_related_work_found"
                        if followups
                        else "evidence_insufficient",
                        "related_later_works": [
                            {
                                "title": x["title"],
                                "year": x["year"],
                                "openalex_id": x["openalex_id"],
                            }
                            for x in followups
                        ],
                    }
                )
                if len(output) >= 20:
                    return output
    return output
