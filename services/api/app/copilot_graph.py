"""Owned graph review and future IDEA selection for writing."""

import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from .graph_insights import ranked_material
from .models import GraphInsight, GraphView


def future_ideas(content: str) -> list[dict]:
    sections = re.split(r"(?m)^###\s+", content)
    ideas = []
    for section in sections[1:]:
        title, _, detail = section.partition("\n")
        if title.strip() and detail.strip():
            ideas.append({"title": title.strip()[:160], "description": detail.strip()[:3000]})
    return ideas[:10]


def graph_options(db: Session, owner_id: str) -> list[dict]:
    views = db.scalars(
        select(GraphView)
        .where(GraphView.owner_id == owner_id)
        .order_by(GraphView.updated_at.desc())
    ).all()
    options = []
    for view in views:
        insights = {
            item.kind: item
            for item in db.scalars(select(GraphInsight).where(GraphInsight.graph_id == view.id))
        }
        review, future = insights.get("review"), insights.get("future")
        if not review or not future:
            continue
        ideas = future_ideas(future.content)
        if ideas:
            options.append(
                {
                    "graph_id": view.id,
                    "updated_at": view.updated_at,
                    "review_preview": review.content[:280],
                    "stale": review.graph_version != view.version
                    or future.graph_version != view.version,
                    "ideas": [
                        {
                            "index": i,
                            "title": item["title"],
                            "description": item["description"][:300],
                        }
                        for i, item in enumerate(ideas)
                    ],
                }
            )
    return options


def graph_writing_context(db: Session, owner_id: str, graph_id: str, idea_index: int) -> dict:
    view = db.get(GraphView, graph_id)
    if not view or view.owner_id != owner_id:
        raise LookupError("Graph not found")
    insights = {
        item.kind: item
        for item in db.scalars(select(GraphInsight).where(GraphInsight.graph_id == graph_id))
    }
    review, future = insights.get("review"), insights.get("future")
    if not review or not future:
        raise ValueError("请先在该图谱生成自动综述和未来工作 IDEA")
    ideas = future_ideas(future.content)
    if not 0 <= idea_index < len(ideas):
        raise ValueError("IDEA 不存在")
    selected = ideas[idea_index]
    source_ids = {source.get("work_id") for source in review.sources}
    candidates = [
        item for item in ranked_material(db, view, "review") if item["work_id"] in source_ids
    ]
    return {
        "idea": selected["title"] + "\n" + selected["description"],
        "review": review.content[:7000],
        "candidates": candidates[:12],
        "stale": review.graph_version != view.version or future.graph_version != view.version,
    }
