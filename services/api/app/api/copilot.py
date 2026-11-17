"""Writing, diagram and chart API routes."""

import secrets

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import current_user
from ..db import get_db
from ..models import Artifact, BackgroundTask
from ..storage import Storage
from ..writing.charts import chart_data, choose_chart, inspect_csv
from ..writing.copilot_diagram import generate_mermaid
from ..writing.copilot_graph import graph_options
from ..writing.copilot_idea import analyze_idea, complete_fields
from ..writing.copilot_jobs import build_draft, load_draft
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
        return build_draft(db, user, data.model_dump())
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(422, f"草稿生成失败：{exc}") from exc


def _draft_job(task: BackgroundTask) -> dict:
    return {
        "id": task.id,
        "mode": "graph" if task.args.get("graph_id") else "manual",
        "status": task.status,
        "progress": task.progress,
        "phase": (task.result or {}).get("phase"),
        "artifact_id": (task.result or {}).get("artifact_id"),
        "error": task.error,
        "created_at": task.created_at,
    }


@router.post("/copilot/draft/jobs")
def start_draft_job(
    data: DraftIn, user: str = Depends(current_user), db: Session = Depends(get_db)
):
    payload = data.model_dump()
    if not data.graph_id and len(data.idea.strip()) < 10:
        raise HTTPException(422, "IDEA 至少需要 10 个字符")
    if data.graph_id and data.idea_index is None:
        raise HTTPException(422, "请选择图谱中的 IDEA")
    # Keep one active generation per user and source mode across tabs and reloads.
    active = db.scalars(
        select(BackgroundTask).where(
            BackgroundTask.owner_id == user,
            BackgroundTask.type == "copilot_draft",
            BackgroundTask.status.in_(("queued", "running")),
        )
    ).all()
    mode = "graph" if data.graph_id else "manual"
    for task in active:
        if ("graph" if task.args.get("graph_id") else "manual") == mode:
            return _draft_job(task)
    task = BackgroundTask(owner_id=user, type="copilot_draft", args=payload)
    db.add(task)
    db.commit()
    from ..worker import run_copilot_draft

    try:
        run_copilot_draft.apply_async(args=[task.id], queue="default")
    except Exception as exc:
        task.status = "failed"
        task.error = f"后台任务提交失败：{type(exc).__name__}"
        db.commit()
        raise HTTPException(503, task.error) from exc
    return _draft_job(task)


@router.get("/copilot/draft/jobs")
def list_draft_jobs(user: str = Depends(current_user), db: Session = Depends(get_db)):
    tasks = db.scalars(
        select(BackgroundTask)
        .where(BackgroundTask.owner_id == user, BackgroundTask.type == "copilot_draft")
        .order_by(BackgroundTask.created_at.desc())
        .limit(30)
    ).all()
    return {"items": [_draft_job(task) for task in tasks]}


@router.get("/copilot/draft/jobs/{task_id}")
def get_draft_job(task_id: str, user: str = Depends(current_user), db: Session = Depends(get_db)):
    task = db.get(BackgroundTask, task_id)
    if not task or task.owner_id != user or task.type != "copilot_draft":
        raise HTTPException(404, "Draft task not found")
    item = _draft_job(task)
    artifact_id = item["artifact_id"]
    if task.status == "completed" and artifact_id:
        artifact = db.get(Artifact, artifact_id)
        if artifact and artifact.owner_id == user and artifact.type == "draft":
            item["draft"] = load_draft(artifact)
    return item


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
