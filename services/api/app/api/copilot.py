"""Writing, diagram and chart API routes."""

import secrets

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import Response
from sqlalchemy.orm import Session

from ..auth import current_user
from ..db import get_db
from ..models import Artifact
from ..storage import Storage
from ..writing.charts import chart_data, choose_chart, inspect_csv
from ..writing.copilot_diagram import generate_mermaid
from ..writing.copilot_draft import create_draft
from ..writing.copilot_graph import graph_options, graph_writing_context
from ..writing.copilot_idea import analyze_idea, complete_fields
from .schemas import ChartCaptionIn, DiagramIn, DraftAnalyzeIn, DraftCompleteIn, DraftIn

router = APIRouter(prefix="/api/v1")


@router.get("/copilot/draft/graph-options")
def draft_graph_options(user: str = Depends(current_user), db: Session = Depends(get_db)):
    return {"items": graph_options(db, user)}


@router.post("/copilot/draft/analyze")
def draft_analyze(data: DraftAnalyzeIn, _user: str = Depends(current_user)):
    try:
        return {"fields": analyze_idea(data.idea)}
    except (ValueError, TypeError) as exc:
        raise HTTPException(422, f"IDEA 分析失败：{exc}") from exc


@router.post("/copilot/draft/complete")
def draft_complete(data: DraftCompleteIn, _user: str = Depends(current_user)):
    try:
        return {"fields": complete_fields(data.idea, data.fields)}
    except (ValueError, TypeError) as exc:
        raise HTTPException(422, f"IDEA 补全失败：{exc}") from exc


@router.post("/copilot/draft")
def draft(data: DraftIn, user: str = Depends(current_user), db: Session = Depends(get_db)):
    try:
        if data.graph_id:
            if data.idea_index is None:
                raise ValueError("请选择图谱中的 IDEA")
            context = graph_writing_context(db, user, data.graph_id, data.idea_index)
            result = create_draft(
                context["idea"], review=context["review"], seed_candidates=context["candidates"]
            )
            if context["stale"]:
                result["warnings"].append("图谱综述或 IDEA 早于当前图谱版本，建议重新生成图谱分析")
        else:
            if len(data.idea.strip()) < 10:
                raise ValueError("IDEA 至少需要 10 个字符")
            result = create_draft(data.idea, data.fields)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(422, f"草稿生成失败：{exc}") from exc
    # Persist only the validated draft; the reference service constructs its bibliography.
    artifact = Artifact(
        owner_id=user,
        type="draft",
        file_key=f"artifacts/{user}/{secrets.token_hex(12)}.md",
        metadata_json={
            "references": result["references"],
            "candidates": result["candidates"],
            "warnings": result["warnings"],
            "fields": result["fields"],
            "graph_id": data.graph_id,
            "idea_index": data.idea_index,
        },
    )
    Storage().put(artifact.file_key, result["markdown"].encode(), "text/markdown")
    db.add(artifact)
    db.commit()
    return {**result, "artifact_id": artifact.id}


@router.post("/copilot/diagrams")
def diagram(data: DiagramIn, _user: str = Depends(current_user)):
    try:
        return {
            "format": "mermaid",
            "script": generate_mermaid(data.description, data.previous, data.error),
        }
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/copilot/charts")
async def chart(
    file: UploadFile = File(...),
    chart_type: str = "auto",
    user: str = Depends(current_user),
    db: Session = Depends(get_db),
):
    data = await file.read(5_000_001)
    try:
        frame, overview = inspect_csv(data)
        intent = choose_chart(frame, overview, chart_type)
        rows, caption, spec = chart_data(frame, intent)
    except (ValueError, UnicodeError) as exc:
        raise HTTPException(422, str(exc)) from exc
    source = Artifact(
        owner_id=user,
        type="csv",
        file_key=f"artifacts/{user}/{secrets.token_hex(12)}.csv",
        metadata_json={"filename": file.filename},
    )
    Storage().put(source.file_key, data, "text/csv")
    db.add(source)
    db.commit()
    return {
        "task_id": None,
        "caption": caption,
        "spec": spec,
        "overview": overview,
        "rows": rows,
        "data_artifact_id": source.id,
    }


@router.post("/copilot/charts/caption")
def update_chart_caption(
    data: ChartCaptionIn, user: str = Depends(current_user), db: Session = Depends(get_db)
):
    source = db.get(Artifact, data.data_artifact_id)
    if not source or source.owner_id != user or source.type != "csv":
        raise HTTPException(404, "CSV not found")
    try:
        frame, _ = inspect_csv(Storage().get(source.file_key))
        rows, caption, spec = chart_data(frame, data.spec)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return {"rows": rows, "caption": caption, "spec": spec}


@router.get("/artifacts/{artifact_id}")
def get_artifact(
    artifact_id: str, user: str = Depends(current_user), db: Session = Depends(get_db)
):
    artifact = db.get(Artifact, artifact_id)
    if not artifact or artifact.owner_id != user:
        raise HTTPException(404, "Artifact not found")
    media_type = "text/csv" if artifact.type == "csv" else "image/png"
    return Response(Storage().get(artifact.file_key), media_type=media_type)
