"""Persist generated paper frameworks independently of the browser request."""

import secrets
from collections.abc import Callable

from sqlalchemy.orm import Session

from ..models import Artifact
from ..storage import Storage
from .copilot_draft import create_draft
from .copilot_graph import graph_writing_context


def build_draft(
    db: Session, owner: str, payload: dict, progress: Callable[[str, float], None] | None = None
) -> dict:
    if payload.get("graph_id"):
        if payload.get("idea_index") is None:
            raise ValueError("请选择图谱中的 IDEA")
        context = graph_writing_context(db, owner, payload["graph_id"], payload["idea_index"])
        result = create_draft(
            context["idea"],
            review=context["review"],
            seed_candidates=context["candidates"],
            progress=progress,
        )
        if context["stale"]:
            result["warnings"].append("图谱综述或 IDEA 早于当前图谱版本，建议重新生成图谱分析")
    else:
        idea = (payload.get("idea") or "").strip()
        if len(idea) < 10:
            raise ValueError("IDEA 至少需要 10 个字符")
        result = create_draft(idea, payload.get("fields"), progress=progress)
    artifact = Artifact(
        owner_id=owner,
        type="draft",
        file_key=f"artifacts/{owner}/{secrets.token_hex(12)}.md",
        metadata_json={
            "references": result["references"],
            "candidates": result["candidates"],
            "warnings": result["warnings"],
            "fields": result["fields"],
            "graph_id": payload.get("graph_id"),
            "idea_index": payload.get("idea_index"),
        },
    )
    Storage().put(artifact.file_key, result["markdown"].encode(), "text/markdown")
    db.add(artifact)
    db.commit()
    return {**result, "artifact_id": artifact.id}


def load_draft(artifact: Artifact) -> dict:
    return {
        **artifact.metadata_json,
        "markdown": Storage().get(artifact.file_key).decode(),
        "artifact_id": artifact.id,
    }
