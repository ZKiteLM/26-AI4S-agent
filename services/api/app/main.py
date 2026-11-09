import hashlib
import re
import secrets
import time
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, File, Header, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, or_, select, text
from sqlalchemy.orm import Session
from sse_starlette.sse import EventSourceResponse

from .auth import check_login, current_user, make_token
from .charts import chart_data, choose_chart, inspect_csv
from .citation_graph_service import add_seed_paper, serialize_view
from .citation_identity import link_paper_to_work, normalize_identifier
from .config import settings
from .copilot_diagram import generate_mermaid
from .copilot_draft import create_draft
from .copilot_graph import graph_options, graph_writing_context
from .copilot_idea import analyze_idea, complete_fields
from .db import Base, SessionLocal, engine, get_db
from .discovery import safe_pdf_download, search_arxiv, search_external
from .graphs import future_work
from .models import (
    AgentRun,
    Artifact,
    BackgroundTask,
    Block,
    ChatMessage,
    ChatSession,
    Chunk,
    CitationEdge,
    GraphInsight,
    GraphReviewEvidence,
    GraphReviewRun,
    GraphView,
    GraphViewNode,
    Paper,
    PaperCandidate,
    Recommendation,
    RunEvent,
    ScholarlyWork,
    Subscription,
    Translation,
    Version,
    now,
)
from .observability import client as diagnostic_client
from .observability import recent as recent_events
from .observability import record
from .parsing import identify_pdf, similarity
from .recommender import recompute_recommendation, weighted_score
from .retrieval import remove_version_index
from .storage import Storage
from .subscription_keywords import generate_keywords, normalize_keywords
from .translation import translate_blocks, translate_text
from .worker import (
    build_citation_graph,
    celery_app,
    enqueue_subscription_refresh,
    expand_citation_graph,
    generate_graph_insight,
    parse_version,
    run_agent,
)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    if settings().database_url.startswith("sqlite"):
        Base.metadata.create_all(engine)
    yield


app = FastAPI(title="AI Research Agent", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
API = "/api/v1"


@app.middleware("http")
async def log_request(request: Request, call_next):
    started = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception as exc:
        record("api", "ERROR", f"{request.method} {request.url.path}", error=type(exc).__name__)
        raise
    if not request.url.path.startswith((API + "/debug", "/health")):
        record(
            "api",
            "ERROR" if response.status_code >= 500 else "INFO",
            f"{request.method} {request.url.path}",
            status=response.status_code,
            duration_ms=round((time.perf_counter() - started) * 1000, 1),
        )
    return response


def debug_access(_user: str = Depends(current_user), x_debug_key: str = Header(default="")) -> None:
    cfg = settings()
    if cfg.deployment_mode == "cloud" and (
        not cfg.debug_console_key or not secrets.compare_digest(x_debug_key, cfg.debug_console_key)
    ):
        raise HTTPException(403, "Debug console key required")


class LoginIn(BaseModel):
    email: str
    password: str


class ChatIn(BaseModel):
    question: str = Field(min_length=2)
    paper_ids: list[str] = Field(min_length=1)


class ChatSessionIn(BaseModel):
    paper_ids: list[str] = Field(default_factory=list)


class ChatMessageIn(BaseModel):
    content: str = Field(min_length=2, max_length=8000)
    paper_ids: list[str] | None = None


class ResolveIn(BaseModel):
    resolution: str


class TranslateIn(BaseModel):
    version_id: str | None = None
    block_ids: list[str] = Field(default_factory=list, max_length=100)
    text: str | None = None
    target_lang: str = "zh"


class SearchIn(BaseModel):
    query: str = Field(min_length=2)
    limit: int = Field(default=10, ge=1, le=25)


class ImportIn(BaseModel):
    openalex_id: str | None = None
    arxiv_id: str | None = None


class GraphIn(BaseModel):
    paper_ids: list[str] = Field(default_factory=list)
    external_seeds: list[dict[str, str]] = Field(default_factory=list, max_length=5)
    mode: str = "explore"
    direction: str = "both"
    per_direction_limit: int = Field(default=20, ge=1, le=50)


class GraphExpandIn(BaseModel):
    work_id: str
    direction: str = "both"
    per_direction_limit: int = Field(default=20, ge=1, le=50)


class GraphRefreshIn(BaseModel):
    work_id: str
    direction: str = "both"
    per_direction_limit: int = Field(default=20, ge=1, le=50)


class DraftIn(BaseModel):
    idea: str = Field(default="", max_length=5000)
    fields: dict | None = None
    graph_id: str | None = None
    idea_index: int | None = Field(default=None, ge=0)


class DraftAnalyzeIn(BaseModel):
    idea: str = Field(min_length=10, max_length=5000)


class DraftCompleteIn(DraftAnalyzeIn):
    fields: dict


class DiagramIn(BaseModel):
    description: str = Field(min_length=5, max_length=5000)
    previous: str = Field(default="", max_length=20000)
    error: str = Field(default="", max_length=1000)


class ChartCaptionIn(BaseModel):
    data_artifact_id: str
    spec: dict


class SubscriptionIn(BaseModel):
    topic: str = Field(min_length=2, max_length=255)
    keywords: list[str] | None = None


class SubscriptionUpdateIn(BaseModel):
    active: bool | None = None
    keywords: list[str] | None = None


class FeedbackIn(BaseModel):
    value: int = Field(ge=-1, le=1)


def owned_paper(db: Session, paper_id: str, user: str) -> Paper:
    paper = db.get(Paper, paper_id)
    if not paper or paper.owner_id != user:
        raise HTTPException(404, "Paper not found")
    return paper


def owned_version(db: Session, version_id: str, user: str) -> Version:
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


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post(API + "/auth/login")
def login(data: LoginIn):
    if not check_login(data.email, data.password):
        raise HTTPException(401, "Invalid credentials")
    return {"access_token": make_token(), "token_type": "bearer"}


@app.post(API + "/papers/upload")
async def upload_papers(
    files: list[UploadFile] = File(...),
    user: str = Depends(current_user),
    db: Session = Depends(get_db),
):
    result = {"paper_ids": [], "version_ids": [], "task_ids": [], "conflicts": []}
    for file in files:
        data = await file.read(50_000_001)
        if len(data) > 50_000_000 or not data.startswith(b"%PDF-"):
            raise HTTPException(400, f"Invalid or oversized PDF: {file.filename}")
        try:
            info = identify_pdf(data, file.filename or "paper.pdf")
        except Exception as exc:
            raise HTTPException(400, f"Cannot read PDF: {exc}") from exc
        sha = hashlib.sha256(data).hexdigest()
        existing = db.scalars(select(Paper).where(Paper.owner_id == user)).all()
        same = None
        for candidate in existing:
            candidate_version = (
                db.get(Version, candidate.preferred_version_id)
                if candidate.preferred_version_id
                else None
            )
            if (
                (info["arxiv_id"] and candidate.arxiv_id == info["arxiv_id"])
                or (candidate_version and candidate_version.sha256 == sha)
                or (
                    candidate_version
                    and similarity(info["sample_text"], candidate_version.sample_text) > 0.8
                )
                or similarity(info["title"], candidate.title) > 0.9
            ):
                same = candidate
                break
        paper = same or Paper(
            owner_id=user,
            title=info["title"],
            arxiv_id=info["arxiv_id"],
            year=2000 + int(info["arxiv_id"][:2]) if info["arxiv_id"] else None,
        )
        db.add(paper)
        db.flush()
        key = f"papers/{user}/{paper.id}/{secrets.token_hex(12)}.pdf"
        Storage().put(key, data, "application/pdf")
        version = Version(
            paper_id=paper.id,
            label=info["label"],
            filename=file.filename or "paper.pdf",
            file_key=key,
            sha256=sha,
            page_count=info["page_count"],
            content_fingerprint=info["fingerprint"],
            sample_text=info["sample_text"],
            parse_status="conflict" if same else "pending",
        )
        db.add(version)
        db.flush()
        if not same:
            paper.preferred_version_id = version.id
        db.commit()
        result["paper_ids"].append(paper.id)
        result["version_ids"].append(version.id)
        if same:
            previous = db.get(Version, paper.preferred_version_id)
            old_number = int(previous.label[1:]) if previous and previous.label[1:].isdigit() else 0
            new_number = int(version.label[1:]) if version.label[1:].isdigit() else 0
            result["conflicts"].append(
                {
                    "id": version.id,
                    "paper_id": paper.id,
                    "existing_version": previous.label if previous else None,
                    "incoming_version": version.label,
                    "recommendation": "keep_existing"
                    if old_number >= new_number
                    else "use_incoming",
                    "reason": "Same paper identity or high body similarity",
                }
            )
        else:
            task = BackgroundTask(owner_id=user, type="parse_pdf", args={"version_id": version.id})
            db.add(task)
            db.commit()
            parse_version.apply_async(args=[task.id, version.id], queue="parser")
            result["task_ids"].append(task.id)
    return result


@app.get(API + "/papers")
def list_papers(q: str = "", user: str = Depends(current_user), db: Session = Depends(get_db)):
    statement = select(Paper).where(Paper.owner_id == user)
    if q:
        statement = statement.where(Paper.title.ilike(f"%{q}%"))
    papers = db.scalars(statement.order_by(Paper.created_at.desc())).all()
    return {"items": [paper_json(p) for p in papers], "total": len(papers)}


@app.get(API + "/papers/{paper_id}")
def get_paper(paper_id: str, user: str = Depends(current_user), db: Session = Depends(get_db)):
    paper = owned_paper(db, paper_id, user)
    versions = db.scalars(select(Version).where(Version.paper_id == paper_id)).all()
    return {"paper": paper_json(paper), "versions": [version_json(x) for x in versions]}


@app.delete(API + "/papers/{paper_id}")
def delete_paper(paper_id: str, user: str = Depends(current_user), db: Session = Depends(get_db)):
    owned_paper(db, paper_id, user)
    versions = db.scalars(select(Version).where(Version.paper_id == paper_id)).all()
    for version in versions:
        remove_version_index(version.id)
        db.execute(delete(Chunk).where(Chunk.version_id == version.id))
        db.execute(
            delete(Translation).where(
                Translation.block_id.in_(select(Block.id).where(Block.version_id == version.id))
            )
        )
        db.execute(delete(Block).where(Block.version_id == version.id))
        Storage().delete(version.file_key)
        db.delete(version)
    db.flush()
    db.execute(delete(Recommendation).where(Recommendation.paper_id == paper_id))
    db.execute(
        delete(CitationEdge).where(
            or_(CitationEdge.citing_paper_id == paper_id, CitationEdge.cited_paper_id == paper_id)
        )
    )
    db.delete(db.get(Paper, paper_id))
    db.commit()
    return {"deleted": paper_id}


@app.get(API + "/versions/{version_id}/blocks")
def get_blocks(
    version_id: str,
    page_start: int | None = Query(default=None, ge=0),
    page_end: int | None = Query(default=None, ge=0),
    user: str = Depends(current_user),
    db: Session = Depends(get_db),
):
    owned_version(db, version_id, user)
    if page_start is not None and page_end is not None and page_end < page_start:
        raise HTTPException(400, "page_end must be >= page_start")
    statement = select(Block).where(Block.version_id == version_id)
    if page_start is not None:
        statement = statement.where(Block.page_index >= page_start)
    if page_end is not None:
        statement = statement.where(Block.page_index <= page_end)
    blocks = db.scalars(statement.order_by(Block.page_index, Block.order)).all()
    items = [
        {
            key: getattr(b, key)
            for key in (
                "id",
                "page_index",
                "order",
                "type",
                "text",
                "markdown",
                "latex",
                "bbox",
                "section_path",
                "parser",
                "confidence",
            )
        }
        for b in blocks
    ]
    return {"blocks": items, "outline": [x for x in items if x["type"] in ("title", "heading")]}


@app.get(API + "/versions/{version_id}/translations")
def get_cached_translations(
    version_id: str,
    page_index: int = Query(ge=0),
    target_lang: str = "zh",
    user: str = Depends(current_user),
    db: Session = Depends(get_db),
):
    owned_version(db, version_id, user)
    rows = db.execute(
        select(Translation, Block)
        .join(Block, Translation.block_id == Block.id)
        .where(
            Block.version_id == version_id,
            Block.page_index == page_index,
            Translation.target_lang == target_lang,
        )
        .order_by(Block.order)
    ).all()
    return {
        "translations": [
            {"block_id": item.block_id, "page_index": block.page_index, "text": item.text}
            for item, block in rows
        ]
    }


@app.get(API + "/versions/{version_id}/file")
def get_file(version_id: str, user: str = Depends(current_user), db: Session = Depends(get_db)):
    version = owned_version(db, version_id, user)
    return Response(Storage().get(version.file_key), media_type="application/pdf")


@app.post(API + "/versions/conflicts/{version_id}/resolve")
def resolve_conflict(
    version_id: str,
    data: ResolveIn,
    user: str = Depends(current_user),
    db: Session = Depends(get_db),
):
    version = owned_version(db, version_id, user)
    if version.parse_status != "conflict" or data.resolution not in ("keep", "replace", "discard"):
        raise HTTPException(400, "Invalid conflict resolution")
    paper = db.get(Paper, version.paper_id)
    if data.resolution == "discard":
        Storage().delete(version.file_key)
        db.delete(version)
        db.commit()
        return {"resolution": "discard", "preferred_version_id": paper.preferred_version_id}
    if data.resolution == "replace":
        paper.preferred_version_id = version.id
    version.parse_status = "pending"
    task = BackgroundTask(owner_id=user, type="parse_pdf", args={"version_id": version.id})
    db.add(task)
    db.commit()
    parse_version.apply_async(args=[task.id, version.id], queue="parser")
    return {
        "resolution": data.resolution,
        "preferred_version_id": paper.preferred_version_id,
        "task_id": task.id,
    }


@app.post(API + "/versions/{version_id}/reparse")
def reparse_version(
    version_id: str, user: str = Depends(current_user), db: Session = Depends(get_db)
):
    version = owned_version(db, version_id, user)
    if version.parse_status in ("pending", "running", "conflict"):
        raise HTTPException(409, "Version is already queued or awaiting conflict resolution")
    version.parse_status = "pending"
    task = BackgroundTask(owner_id=user, type="parse_pdf", args={"version_id": version.id})
    db.add(task)
    db.commit()
    parse_version.apply_async(args=[task.id, version.id], queue="parser")
    return {"task_id": task.id, "parse_status": version.parse_status}


@app.get(API + "/tasks")
def list_tasks(user: str = Depends(current_user), db: Session = Depends(get_db)):
    tasks = db.scalars(
        select(BackgroundTask)
        .where(BackgroundTask.owner_id == user)
        .order_by(BackgroundTask.created_at.desc())
    ).all()
    return {"items": [task_json(x) for x in tasks]}


@app.get(API + "/debug/overview", dependencies=[Depends(debug_access)])
def debug_overview(db: Session = Depends(get_db)):
    """Read-only diagnostic snapshot. The cloud console requires a separate key."""
    database = {"backend": engine.url.get_backend_name(), "available": True}
    try:
        db.execute(text("SELECT 1").execution_options(skip_diagnostic_log=True))
        database["papers"] = db.scalar(
            select(func.count()).select_from(Paper).execution_options(skip_diagnostic_log=True)
        )
        database["versions"] = db.scalar(
            select(func.count()).select_from(Version).execution_options(skip_diagnostic_log=True)
        )
        database["blocks"] = db.scalar(
            select(func.count()).select_from(Block).execution_options(skip_diagnostic_log=True)
        )
        if database["backend"] == "postgresql":
            database["postgres"] = dict(
                db.execute(
                    text("""
                SELECT numbackends, xact_commit, xact_rollback, blks_read, blks_hit, deadlocks
                FROM pg_stat_database WHERE datname = current_database()
            """).execution_options(skip_diagnostic_log=True)
                )
                .mappings()
                .one()
            )
    except Exception as exc:
        database = {
            "backend": engine.url.get_backend_name(),
            "available": False,
            "error": type(exc).__name__,
        }
        db.rollback()

    jobs = (
        db.scalars(
            select(BackgroundTask)
            .order_by(BackgroundTask.created_at.desc())
            .limit(30)
            .execution_options(skip_diagnostic_log=True)
        ).all()
        if database["available"]
        else []
    )
    version_ids = [
        job.args["version_id"] for job in jobs if job.args and job.args.get("version_id")
    ]
    versions = {
        version.id: version
        for version in db.scalars(
            select(Version)
            .where(Version.id.in_(version_ids))
            .execution_options(skip_diagnostic_log=True)
        )
    }
    papers = {
        paper.id: paper
        for paper in db.scalars(
            select(Paper)
            .where(Paper.id.in_([version.paper_id for version in versions.values()]))
            .execution_options(skip_diagnostic_log=True)
        )
    }
    task_items = []
    for job in jobs:
        item = task_json(job)
        version_id = job.args.get("version_id") if job.args else None
        if version_id:
            version = versions.get(version_id)
            paper = papers.get(version.paper_id) if version else None
            item["paper_title"] = paper.title if paper else None
            item["paper_id"] = paper.id if paper else None
            item["version_status"] = version.parse_status if version else None
        task_items.append(item)

    broker = {"available": False, "parser_queue": None, "default_queue": None, "workers": []}
    events = []
    try:
        redis = diagnostic_client()
        redis.ping()
        broker.update(
            available=True, parser_queue=redis.llen("parser"), default_queue=redis.llen("default")
        )
        events = recent_events()
        queues = celery_app.control.inspect(timeout=0.6).active_queues() or {}
        broker["workers"] = [
            {"name": name, "queues": [entry["name"] for entry in entries]}
            for name, entries in queues.items()
        ]
    except Exception as exc:
        broker["error"] = type(exc).__name__
    return {"database": database, "broker": broker, "tasks": task_items, "events": events}


@app.get(API + "/tasks/{task_id}")
def get_task(task_id: str, user: str = Depends(current_user), db: Session = Depends(get_db)):
    task = db.get(BackgroundTask, task_id)
    if not task or task.owner_id != user:
        raise HTTPException(404, "Task not found")
    return task_json(task)


@app.post(API + "/tasks/{task_id}/retry")
def retry_task(task_id: str, user: str = Depends(current_user), db: Session = Depends(get_db)):
    old = db.get(BackgroundTask, task_id)
    if not old or old.owner_id != user or old.status != "failed":
        raise HTTPException(400, "Only failed own tasks can be retried")
    task = BackgroundTask(owner_id=user, type=old.type, args=old.args, retries=old.retries + 1)
    db.add(task)
    db.commit()
    if task.type == "parse_pdf":
        parse_version.apply_async(args=[task.id, task.args["version_id"]], queue="parser")
    return {"new_task_id": task.id}


def create_run(db: Session, user: str, question: str, paper_ids: list[str], kind: str) -> AgentRun:
    for paper_id in paper_ids:
        owned_paper(db, paper_id, user)
    run = AgentRun(owner_id=user, question=question, paper_ids=paper_ids, kind=kind)
    db.add(run)
    db.commit()
    run_agent.delay(run.id)
    return run


def chat_session_json(session: ChatSession) -> dict:
    return {
        "id": session.id,
        "title": session.title,
        "paper_ids": session.paper_ids,
        "created_at": session.created_at,
        "updated_at": session.updated_at,
    }


def owned_chat_session(db: Session, session_id: str, user: str) -> ChatSession:
    session = db.get(ChatSession, session_id)
    if not session or session.owner_id != user:
        raise HTTPException(404, "Conversation not found")
    return session


@app.get(API + "/chat/sessions")
def list_chat_sessions(user: str = Depends(current_user), db: Session = Depends(get_db)):
    sessions = db.scalars(
        select(ChatSession)
        .where(ChatSession.owner_id == user)
        .order_by(ChatSession.updated_at.desc(), ChatSession.created_at.desc())
    ).all()
    return {"sessions": [chat_session_json(session) for session in sessions]}


@app.post(API + "/chat/sessions")
def new_chat_session(
    data: ChatSessionIn, user: str = Depends(current_user), db: Session = Depends(get_db)
):
    for paper_id in data.paper_ids:
        owned_paper(db, paper_id, user)
    session = ChatSession(owner_id=user, paper_ids=list(dict.fromkeys(data.paper_ids)))
    db.add(session)
    db.commit()
    return chat_session_json(session)


@app.get(API + "/chat/sessions/{session_id}")
def get_chat_session(
    session_id: str, user: str = Depends(current_user), db: Session = Depends(get_db)
):
    session = owned_chat_session(db, session_id, user)
    messages = db.scalars(
        select(ChatMessage)
        .where(ChatMessage.session_id == session_id)
        .order_by(ChatMessage.created_at, ChatMessage.id)
    ).all()
    run_ids = [item.run_id for item in messages if item.run_id]
    runs = {run.id: run for run in db.scalars(select(AgentRun).where(AgentRun.id.in_(run_ids)))}
    events = db.scalars(
        select(RunEvent).where(RunEvent.run_id.in_(run_ids)).order_by(RunEvent.sequence)
    ).all()
    by_run: dict[str, list[dict]] = {}
    for item in events:
        by_run.setdefault(item.run_id, []).append(
            {"event": item.event, "data": item.data, "sequence": item.sequence}
        )
    return {
        **chat_session_json(session),
        "messages": [
            {
                "id": item.id,
                "role": item.role,
                "content": item.content,
                "run_id": item.run_id,
                "evidence": item.evidence,
                "status": runs[item.run_id].status if item.run_id in runs else None,
                "error": runs[item.run_id].error if item.run_id in runs else None,
                "events": by_run.get(item.run_id, []),
                "created_at": item.created_at,
            }
            for item in messages
            if item.role == "user" or (item.role == "assistant" and not item.tool_calls)
        ],
    }


@app.delete(API + "/chat/sessions/{session_id}")
def delete_chat_session(
    session_id: str, user: str = Depends(current_user), db: Session = Depends(get_db)
):
    session = owned_chat_session(db, session_id, user)
    runs = db.scalars(select(AgentRun).where(AgentRun.session_id == session_id)).all()
    if any(run.status in ("queued", "running") for run in runs):
        raise HTTPException(409, "Wait for the current answer before deleting this conversation")
    run_ids = [run.id for run in runs]
    if run_ids:
        db.execute(delete(RunEvent).where(RunEvent.run_id.in_(run_ids)))
    db.execute(delete(ChatMessage).where(ChatMessage.session_id == session_id))
    db.execute(delete(AgentRun).where(AgentRun.session_id == session_id))
    db.delete(session)
    db.commit()
    return {"deleted": True}


@app.post(API + "/chat/sessions/{session_id}/messages")
def send_chat_message(
    session_id: str,
    data: ChatMessageIn,
    user: str = Depends(current_user),
    db: Session = Depends(get_db),
):
    session = owned_chat_session(db, session_id, user)
    active = db.scalar(
        select(AgentRun.id)
        .where(AgentRun.session_id == session_id, AgentRun.status.in_(["queued", "running"]))
        .limit(1)
    )
    if active:
        raise HTTPException(409, "Wait for the current answer")
    paper_ids = list(
        dict.fromkeys(data.paper_ids if data.paper_ids is not None else session.paper_ids)
    )
    if not paper_ids:
        raise HTTPException(400, "Select at least one paper")
    for paper_id in paper_ids:
        owned_paper(db, paper_id, user)
    question = re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", " ", data.content).strip()
    if len(question) < 2:
        raise HTTPException(400, "Question is too short")
    session.paper_ids = paper_ids
    if session.title == "新会话":
        session.title = question[:60]
    session.updated_at = now()
    run = AgentRun(
        owner_id=user,
        session_id=session_id,
        question=question,
        paper_ids=paper_ids,
        kind="chat",
    )
    db.add(run)
    db.flush()
    db.add(ChatMessage(session_id=session_id, role="user", content=question, run_id=run.id))
    db.commit()
    run_agent.delay(run.id)
    return {"run_id": run.id, "session_id": session.id}


@app.post(API + "/chat/runs")
def start_chat(data: ChatIn, user: str = Depends(current_user), db: Session = Depends(get_db)):
    session = new_chat_session(ChatSessionIn(paper_ids=data.paper_ids), user, db)
    return send_chat_message(
        session["id"], ChatMessageIn(content=data.question, paper_ids=data.paper_ids), user, db
    )


@app.get(API + "/chat/runs/{run_id}")
def get_run(run_id: str, user: str = Depends(current_user), db: Session = Depends(get_db)):
    run = db.get(AgentRun, run_id)
    if not run or run.owner_id != user:
        raise HTTPException(404, "Run not found")
    events = db.scalars(
        select(RunEvent).where(RunEvent.run_id == run_id).order_by(RunEvent.sequence)
    ).all()
    return {
        "id": run.id,
        "status": run.status,
        "answer": run.answer,
        "evidence": run.evidence,
        "error": run.error,
        "events": [{"event": x.event, "data": x.data, "sequence": x.sequence} for x in events],
    }


@app.get(API + "/chat/runs/{run_id}/events")
async def run_events(
    run_id: str, after: int = 0, user: str = Depends(current_user), db: Session = Depends(get_db)
):
    run = db.get(AgentRun, run_id)
    if not run or run.owner_id != user:
        raise HTTPException(404, "Run not found")
    import asyncio
    import json

    async def stream():
        sequence = after
        while True:
            with SessionLocal() as session:
                events = session.scalars(
                    select(RunEvent)
                    .where(RunEvent.run_id == run_id, RunEvent.sequence > sequence)
                    .order_by(RunEvent.sequence)
                ).all()
                status = session.get(AgentRun, run_id).status
            for item in events:
                sequence = item.sequence
                yield {"id": str(sequence), "event": item.event, "data": json.dumps(item.data)}
            if status in ("completed", "failed"):
                return
            await asyncio.sleep(0.5)

    return EventSourceResponse(stream())


@app.post(API + "/translate")
def translate(data: TranslateIn, user: str = Depends(current_user), db: Session = Depends(get_db)):
    if data.text:
        return {"text": translate_text(data.text, data.target_lang)}
    if not data.version_id or not data.block_ids:
        raise HTTPException(400, "version_id and block_ids or text required")
    owned_version(db, data.version_id, user)
    statement = select(Block).where(Block.version_id == data.version_id)
    statement = statement.where(Block.id.in_(data.block_ids))
    blocks = db.scalars(statement.order_by(Block.page_index, Block.order)).all()
    if len(blocks) != len(set(data.block_ids)):
        raise HTTPException(400, "Some blocks do not belong to the selected version")
    return {"translations": translate_blocks(db, blocks, data.target_lang)}


@app.post(API + "/discovery/search")
def discover(data: SearchIn, _user: str = Depends(current_user)):
    openalex = search_external(data.query, data.limit)
    try:
        arxiv = search_arxiv(data.query, data.limit)
    except Exception:
        arxiv = []
    seen = {x["title"].lower() for x in openalex}
    return {
        "papers": openalex + [x for x in arxiv if x["title"].lower() not in seen],
        "provenance": ["OpenAlex", "arXiv"],
    }


@app.post(API + "/discovery/import")
def import_paper(data: ImportIn, user: str = Depends(current_user), db: Session = Depends(get_db)):
    if data.arxiv_id and re.fullmatch(r"\d{4}\.\d{4,5}", data.arxiv_id):
        matches = search_arxiv(data.arxiv_id, 1)
        if not matches:
            raise HTTPException(404, "arXiv paper not found")
        record = matches[0]
        metadata = {
            "display_name": record["title"],
            "publication_year": record["year"],
            "authorships": [{"author": {"display_name": a}} for a in record["authors"]],
            "doi": None,
            "best_oa_location": {"pdf_url": record["pdf_url"]},
        }
        existing = db.scalar(
            select(Paper).where(Paper.owner_id == user, Paper.arxiv_id == data.arxiv_id)
        )
    elif data.openalex_id and re.fullmatch(r"https://openalex.org/W\d+", data.openalex_id):
        import httpx

        from .discovery import openalex_headers

        work = httpx.get(
            "https://api.openalex.org/works/" + data.openalex_id.rsplit("/", 1)[-1],
            headers=openalex_headers(),
            timeout=20,
        )
        work.raise_for_status()
        metadata = work.json()
        existing = db.scalar(
            select(Paper).where(Paper.owner_id == user, Paper.openalex_id == data.openalex_id)
        )
    else:
        raise HTTPException(400, "Provide a valid OpenAlex or arXiv ID")
    if existing:
        return {"paper_id": existing.id, "task_id": None, "status": "already_imported"}
    paper = Paper(
        owner_id=user,
        title=metadata.get("display_name", ""),
        authors=[x["author"]["display_name"] for x in metadata.get("authorships", [])],
        year=metadata.get("publication_year"),
        doi=metadata.get("doi"),
        openalex_id=data.openalex_id,
        arxiv_id=data.arxiv_id,
        source="arXiv" if data.arxiv_id else "OpenAlex",
    )
    return persist_discovered_paper(
        db, paper, (metadata.get("best_oa_location") or {}).get("pdf_url"), user
    )


def persist_discovered_paper(db: Session, paper: Paper, pdf_url: str | None, user: str) -> dict:
    db.add(paper)
    db.commit()
    if not pdf_url:
        return {"paper_id": paper.id, "task_id": None, "status": "metadata_only"}
    try:
        pdf = safe_pdf_download(pdf_url)
    except Exception:
        return {"paper_id": paper.id, "task_id": None, "status": "metadata_only"}
    info = identify_pdf(pdf, paper.title + ".pdf")
    key = f"papers/{user}/{paper.id}/{secrets.token_hex(12)}.pdf"
    Storage().put(key, pdf, "application/pdf")
    version = Version(
        paper_id=paper.id,
        label=info["label"],
        filename=paper.title + ".pdf",
        file_key=key,
        sha256=hashlib.sha256(pdf).hexdigest(),
        page_count=info["page_count"],
        sample_text=info["sample_text"],
    )
    db.add(version)
    db.flush()
    paper.preferred_version_id = version.id
    task = BackgroundTask(owner_id=user, type="parse_pdf", args={"version_id": version.id})
    db.add(task)
    db.commit()
    parse_version.apply_async(args=[task.id, version.id], queue="parser")
    return {"paper_id": paper.id, "task_id": task.id, "status": "queued"}


def subscription_json(sub: Subscription) -> dict:
    return {
        "id": sub.id,
        "topic": sub.topic,
        "keywords": sub.keywords or [sub.topic],
        "active": sub.active,
        "last_checked_at": sub.last_checked_at,
        "next_refresh_at": sub.next_refresh_at if sub.active else None,
        "refresh_status": sub.refresh_status,
        "refresh_mode": sub.refresh_mode,
        "refresh_error": sub.refresh_error,
        "refresh_requested_at": sub.refresh_requested_at,
    }


@app.post(API + "/subscriptions/keywords")
def suggest_subscription_keywords(
    data: SubscriptionIn,
    user: str = Depends(current_user),
):
    try:
        topic = normalize_keywords(data.topic, None)[0]
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    try:
        return {"topic": topic, "keywords": generate_keywords(topic)}
    except Exception as exc:
        raise HTTPException(503, "检索词生成失败；可直接使用原始主题创建订阅") from exc


@app.get(API + "/subscriptions")
def list_subscriptions(user: str = Depends(current_user), db: Session = Depends(get_db)):
    return {
        "items": [
            subscription_json(sub)
            for sub in db.scalars(
                select(Subscription)
                .where(Subscription.owner_id == user, Subscription.deleted_at.is_(None))
                .order_by(Subscription.topic)
            )
        ]
    }


@app.post(API + "/subscriptions")
def subscribe(
    data: SubscriptionIn, user: str = Depends(current_user), db: Session = Depends(get_db)
):
    try:
        keywords = normalize_keywords(data.topic, data.keywords)
    except (ValueError, TypeError) as exc:
        raise HTTPException(422, str(exc)) from exc
    topic = keywords[0]
    sub = db.scalar(
        select(Subscription).where(
            Subscription.owner_id == user,
            Subscription.deleted_at.is_(None),
            func.lower(Subscription.topic) == topic.casefold(),
        )
    )
    if sub is None:
        sub = Subscription(
            owner_id=user,
            topic=topic,
            keywords=keywords,
            active=True,
            next_refresh_at=now(),
            refresh_status="idle",
        )
        db.add(sub)
        db.commit()
        try:
            enqueue_subscription_refresh(db, sub, "auto")
        except Exception as exc:
            raise HTTPException(503, "订阅已保存，但首次刷新任务提交失败；请稍后立即刷新") from exc
    return subscription_json(sub)


@app.patch(API + "/subscriptions/{subscription_id}")
def update_subscription(
    subscription_id: str,
    data: SubscriptionUpdateIn,
    user: str = Depends(current_user),
    db: Session = Depends(get_db),
):
    sub = db.get(Subscription, subscription_id)
    if not sub or sub.owner_id != user or sub.deleted_at:
        raise HTTPException(404, "Subscription not found")
    if data.active is None and data.keywords is None:
        raise HTTPException(422, "No changes provided")
    if data.keywords is not None:
        try:
            sub.keywords = normalize_keywords(sub.topic, data.keywords)
        except (ValueError, TypeError) as exc:
            raise HTTPException(422, str(exc)) from exc
    if data.active is not None:
        sub.active = data.active
    db.commit()
    return subscription_json(sub)


@app.post(API + "/subscriptions/{subscription_id}/refresh")
def refresh_subscription(
    subscription_id: str, user: str = Depends(current_user), db: Session = Depends(get_db)
):
    sub = db.get(Subscription, subscription_id)
    if not sub or sub.owner_id != user or sub.deleted_at:
        raise HTTPException(404, "Subscription not found")
    try:
        queued = enqueue_subscription_refresh(db, sub, "manual")
    except Exception as exc:
        raise HTTPException(503, "刷新任务提交失败，请稍后重试") from exc
    return {
        "id": sub.id,
        "queued": queued,
        "refresh_status": "queued" if queued else sub.refresh_status,
    }


@app.get(API + "/subscriptions/{subscription_id}/refresh-status")
def subscription_refresh_status(
    subscription_id: str, user: str = Depends(current_user), db: Session = Depends(get_db)
):
    sub = db.get(Subscription, subscription_id)
    if not sub or sub.owner_id != user or sub.deleted_at:
        raise HTTPException(404, "Subscription not found")
    return subscription_json(sub)


@app.delete(API + "/subscriptions/{subscription_id}")
def delete_subscription(
    subscription_id: str, user: str = Depends(current_user), db: Session = Depends(get_db)
):
    sub = db.scalar(
        select(Subscription)
        .where(
            Subscription.id == subscription_id,
            Subscription.owner_id == user,
            Subscription.deleted_at.is_(None),
        )
        .with_for_update()
    )
    if not sub:
        raise HTTPException(404, "Subscription not found")
    sub.deleted_at = now()
    sub.active = False
    sub.refresh_status = "deleted"
    sub.refresh_token = None
    for rec in db.scalars(select(Recommendation).where(Recommendation.owner_id == user)):
        if sub.topic not in (rec.topics or []):
            continue
        rec.topics = [topic for topic in rec.topics if topic != sub.topic]
        matched = dict(rec.matched_keywords or {})
        matched.pop(sub.topic, None)
        rec.matched_keywords = matched
        analyses = dict(rec.topic_analyses or {})
        analyses.pop(sub.topic, None)
        rec.topic_analyses = analyses
        if not rec.topics and rec.status != "imported":
            db.delete(rec)
        elif rec.status == "pending":
            recompute_recommendation(
                rec, db.get(PaperCandidate, rec.candidate_id), allow_legacy=False
            )
    db.commit()
    return {"deleted": True}


@app.get(API + "/recommendations")
def recommendations(
    topic: str | None = None,
    status: str = Query(default="pending", pattern="^(pending|imported|dismissed|all)$"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=12, ge=1, le=50),
    user: str = Depends(current_user),
    db: Session = Depends(get_db),
):
    query = select(Recommendation).where(Recommendation.owner_id == user)
    if status != "all":
        query = query.where(Recommendation.status == status)
    items = db.scalars(
        query.order_by(Recommendation.score.desc().nullslast(), Recommendation.created_at.desc())
    ).all()
    if topic:
        items = [item for item in items if topic in item.topics]
        items.sort(
            key=lambda item: (
                weighted_score(
                    db.get(PaperCandidate, item.candidate_id),
                    (item.topic_analyses or {}).get(topic),
                )[0]
                if (item.topic_analyses or {}).get(topic)
                else -1
            ),
            reverse=True,
        )
    total = len(items)
    items = items[(page - 1) * page_size : page * page_size]
    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": [recommendation_json(db, item, topic) for item in items],
    }


def recommendation_json(db: Session, rec: Recommendation, topic: str | None) -> dict:
    candidate = db.get(PaperCandidate, rec.candidate_id)
    analysis = (rec.topic_analyses or {}).get(topic) if topic else None
    if topic:
        calculated, reasons = weighted_score(candidate, analysis)
        score = calculated if analysis else None
    else:
        score, reasons = rec.score, rec.reasons or {}
    return {
        "id": rec.id,
        "candidate": candidate_json(candidate),
        "paper_id": rec.paper_id,
        "score": score,
        "scoring_factors": reasons,
        "scoring_weights": reasons.get("effective_weights", {}),
        "feedback": rec.feedback,
        "topics": rec.topics,
        "matched_keywords": rec.matched_keywords or {},
        "summary": analysis.get("summary", "")
        if topic and analysis
        else rec.summary
        if not topic
        else "",
        "reason": analysis.get("reason", "")
        if topic and analysis
        else rec.reason
        if not topic
        else "",
        "status": rec.status,
    }


def candidate_json(candidate: PaperCandidate) -> dict:
    return {
        key: getattr(candidate, key)
        for key in (
            "id",
            "title",
            "authors",
            "abstract",
            "published_at",
            "year",
            "source",
            "source_url",
            "doi",
            "arxiv_id",
            "openalex_id",
            "pdf_url",
            "github_url",
            "citation_count",
            "github_stars",
            "project_page_url",
            "github_source",
            "github_verification",
            "citation_source",
        )
    }


@app.post(API + "/recommendations/{recommendation_id}/import")
def import_recommendation(
    recommendation_id: str, user: str = Depends(current_user), db: Session = Depends(get_db)
):
    item = db.get(Recommendation, recommendation_id)
    if not item or item.owner_id != user:
        raise HTTPException(404, "Recommendation not found")
    if item.paper_id:
        return {"paper_id": item.paper_id, "task_id": None, "status": "already_imported"}
    candidate = db.get(PaperCandidate, item.candidate_id)
    if not candidate:
        raise HTTPException(404, "Candidate not found")
    checks = [Paper.title == candidate.title]
    for field in ("doi", "arxiv_id", "openalex_id"):
        if getattr(candidate, field):
            checks.append(getattr(Paper, field) == getattr(candidate, field))
    existing = db.scalar(select(Paper).where(Paper.owner_id == user, or_(*checks)))
    if existing:
        result = {"paper_id": existing.id, "task_id": None, "status": "already_imported"}
    else:
        paper = Paper(
            owner_id=user,
            title=candidate.title,
            authors=candidate.authors,
            abstract=candidate.abstract,
            year=candidate.year,
            doi=candidate.doi,
            arxiv_id=candidate.arxiv_id,
            openalex_id=candidate.openalex_id,
            source=candidate.source,
        )
        result = persist_discovered_paper(db, paper, candidate.pdf_url, user)
    item.paper_id = result["paper_id"]
    item.status = "imported"
    db.commit()
    return result


@app.post(API + "/recommendations/{recommendation_id}/feedback")
def feedback(
    recommendation_id: str,
    data: FeedbackIn,
    user: str = Depends(current_user),
    db: Session = Depends(get_db),
):
    item = db.get(Recommendation, recommendation_id)
    if not item or item.owner_id != user:
        raise HTTPException(404, "Recommendation not found")
    item.feedback = data.value
    if data.value < 0:
        item.status = "dismissed"
    db.commit()
    return {"feedback_id": item.id, "status": item.status, "feedback": item.feedback}


@app.post(API + "/graphs")
def create_graph(data: GraphIn, user: str = Depends(current_user), db: Session = Depends(get_db)):
    if data.mode not in ("internal", "explore"):
        raise HTTPException(422, "Invalid graph mode")
    if data.mode == "internal" and (len(set(data.paper_ids)) < 2 or data.external_seeds):
        raise HTTPException(
            422, "Internal analysis needs at least two library papers and no external seeds"
        )
    if data.direction not in ("references", "citations", "both"):
        raise HTTPException(422, "Invalid direction")
    if not data.paper_ids and not data.external_seeds:
        raise HTTPException(422, "Select at least one seed")
    for seed in data.external_seeds:
        if seed.get("type") not in ("doi", "arxiv", "openalex") or not normalize_identifier(
            seed["type"], seed.get("value")
        ):
            raise HTTPException(422, "External seed needs a valid DOI, arXiv or OpenAlex ID")
    view = GraphView(
        owner_id=user,
        config={
            "mode": data.mode,
            "direction": data.direction,
            "per_direction_limit": data.per_direction_limit,
            "external_seeds": data.external_seeds,
        },
    )
    db.add(view)
    db.flush()
    for paper_id in dict.fromkeys(data.paper_ids):
        add_seed_paper(db, view, owned_paper(db, paper_id, user))
    task = BackgroundTask(owner_id=user, type="citation_graph", args={"graph_id": view.id})
    db.add(task)
    db.commit()
    build_citation_graph.delay(task.id, view.id)
    return {"graph_id": view.id, "task_id": task.id, "status": "queued"}


@app.get(API + "/graphs")
def list_graphs(user: str = Depends(current_user), db: Session = Depends(get_db)):
    views = db.scalars(
        select(GraphView).where(GraphView.owner_id == user).order_by(GraphView.updated_at.desc())
    ).all()
    return {
        "items": [
            {
                "id": view.id,
                "status": view.status,
                "mode": view.config.get("mode", "explore"),
                "updated_at": view.updated_at,
                "node_count": db.scalar(
                    select(func.count())
                    .select_from(GraphViewNode)
                    .where(GraphViewNode.graph_id == view.id)
                ),
            }
            for view in views
        ]
    }


@app.get(API + "/graphs/{graph_id}")
def get_graph(graph_id: str, user: str = Depends(current_user), db: Session = Depends(get_db)):
    graph = db.get(GraphView, graph_id)
    if not graph or graph.owner_id != user:
        raise HTTPException(404, "Graph not found")
    return serialize_view(db, graph)


def owned_graph(db: Session, graph_id: str, user: str, *, lock: bool = False) -> GraphView:
    query = select(GraphView).where(GraphView.id == graph_id)
    view = db.scalar(query.with_for_update() if lock else query)
    if not view or view.owner_id != user:
        raise HTTPException(404, "Graph not found")
    return view


def active_graph_task(db: Session, graph_id: str, kind: str | None = None) -> BackgroundTask | None:
    tasks = db.scalars(
        select(BackgroundTask).where(
            BackgroundTask.type.in_(("citation_graph", "graph_insight")),
            BackgroundTask.status.in_(("queued", "running")),
        )
    ).all()
    return next(
        (
            task
            for task in tasks
            if task.args.get("graph_id") == graph_id
            and (kind is None or task.args.get("kind") == kind)
        ),
        None,
    )


@app.get(API + "/graphs/{graph_id}/insights")
def get_graph_insights(
    graph_id: str, user: str = Depends(current_user), db: Session = Depends(get_db)
):
    view = owned_graph(db, graph_id, user)
    records = db.scalars(select(GraphInsight).where(GraphInsight.graph_id == graph_id)).all()
    review = next((record for record in records if record.kind == "review"), None)
    return {
        record.kind: {
            "content": record.content,
            "sources": record.sources,
            "graph_version": record.graph_version,
            "stale": record.graph_version != view.version
            or (
                record.kind == "future"
                and review is not None
                and record.updated_at < review.updated_at
            ),
            "updated_at": record.updated_at,
        }
        for record in records
    }


@app.get(API + "/graphs/{graph_id}/insights/review/research")
def get_graph_review_research(
    graph_id: str, user: str = Depends(current_user), db: Session = Depends(get_db)
):
    owned_graph(db, graph_id, user)
    run = db.scalar(
        select(GraphReviewRun)
        .where(GraphReviewRun.graph_id == graph_id, GraphReviewRun.owner_id == user)
        .order_by(GraphReviewRun.created_at.desc())
    )
    if not run:
        return {"run": None}
    evidence = db.scalars(
        select(GraphReviewEvidence).where(GraphReviewEvidence.run_id == run.id)
    ).all()
    work_titles = {
        work.id: work.title
        for work in db.scalars(
            select(ScholarlyWork).where(
                ScholarlyWork.id.in_({item.work_id for item in evidence})
            )
        )
    }
    return {
        "run": {
            "id": run.id,
            "task_id": run.task_id,
            "status": run.status,
            "phase": run.phase,
            "graph_version": run.graph_version,
            "stale": run.graph_version != db.get(GraphView, graph_id).version,
            "outline": run.outline,
            "sections": [
                {"id": item["id"], "title": item["title"], "status": item["status"]}
                for item in run.sections
            ],
            "evidence_count": len(evidence),
            "paper_count": len({item.work_id for item in evidence}),
            "evidence": [
                {
                    "id": item.id,
                    "work_id": item.work_id,
                    "title": work_titles.get(item.work_id, ""),
                    "paper_id": item.paper_id,
                    "source_type": item.source_type,
                    "page_index": item.page_index,
                    "start": item.start,
                    "summary": item.summary,
                    "quote": item.quote[:650],
                }
                for item in evidence
            ],
            "coverage": run.coverage,
            "error": run.error,
        }
    }


@app.post(API + "/graphs/{graph_id}/insights/{kind}")
def queue_graph_insight(
    graph_id: str, kind: str, user: str = Depends(current_user), db: Session = Depends(get_db)
):
    view = owned_graph(db, graph_id, user, lock=True)
    if kind not in ("review", "future"):
        raise HTTPException(422, "Invalid insight kind")
    if view.status in ("queued", "running") or not db.scalar(
        select(GraphViewNode.id).where(GraphViewNode.graph_id == graph_id).limit(1)
    ):
        raise HTTPException(409, "Build the graph before generating an insight")
    if running := active_graph_task(db, graph_id, kind):
        return {"task_id": running.id, "status": running.status}
    if active_graph_task(db, graph_id):
        raise HTTPException(409, "Wait for the current graph task to finish")
    task = BackgroundTask(
        owner_id=user,
        type="graph_insight",
        args={"graph_id": graph_id, "kind": kind, "version": view.version},
    )
    db.add(task)
    db.flush()
    if kind == "review":
        run = db.scalar(
            select(GraphReviewRun)
            .where(GraphReviewRun.graph_id == graph_id, GraphReviewRun.owner_id == user)
            .order_by(GraphReviewRun.created_at.desc())
        )
        if (
            run
            and run.status == "failed"
            and run.graph_version == view.version
            and run.outline.get("sections")
            and run.sections
            and not run.coverage.get("gaps")
            and (
                len(run.sections) < len(run.outline["sections"])
                or not run.error
                or run.error.startswith(("Server error", "Client error", "Connect", "ReadTimeout", "Local model"))
            )
        ):
            run.task_id = task.id
            run.status = "queued"
            run.phase = "writing" if len(run.sections) < len(run.outline["sections"]) else "verifying"
            run.error = None
            run.updated_at = now()
        else:
            run = GraphReviewRun(
                graph_id=graph_id,
                owner_id=user,
                task_id=task.id,
                graph_version=view.version,
            )
            db.add(run)
            db.flush()
        task.args = {**task.args, "review_run_id": run.id}
    db.commit()
    generate_graph_insight.delay(task.id, graph_id, kind)
    return {"task_id": task.id, "status": "queued"}


@app.delete(API + "/graphs/{graph_id}")
def delete_graph(graph_id: str, user: str = Depends(current_user), db: Session = Depends(get_db)):
    view = owned_graph(db, graph_id, user, lock=True)
    if active_graph_task(db, graph_id):
        raise HTTPException(409, "Graph has a running task; retry when it finishes")
    review_ids = select(GraphReviewRun.id).where(GraphReviewRun.graph_id == graph_id)
    db.execute(delete(GraphReviewEvidence).where(GraphReviewEvidence.run_id.in_(review_ids)))
    db.execute(delete(GraphReviewRun).where(GraphReviewRun.graph_id == graph_id))
    db.execute(delete(GraphInsight).where(GraphInsight.graph_id == graph_id))
    db.execute(delete(GraphViewNode).where(GraphViewNode.graph_id == graph_id))
    db.delete(view)
    db.commit()
    return {"deleted": True}


def graph_task(db: Session, user: str, graph_id: str, data: GraphExpandIn, force: bool = False):
    view = db.scalar(select(GraphView).where(GraphView.id == graph_id).with_for_update())
    if not view or view.owner_id != user:
        raise HTTPException(404, "Graph not found")
    if view.config.get("mode") == "internal":
        raise HTTPException(409, "Internal analysis has a fixed paper set")
    if data.direction not in ("references", "citations", "both"):
        raise HTTPException(422, "Invalid direction")
    node = db.scalar(
        select(GraphViewNode).where(
            GraphViewNode.graph_id == graph_id,
            GraphViewNode.work_id == data.work_id,
        )
    )
    if not node:
        raise HTTPException(404, "Graph node not found")
    for task in db.scalars(
        select(BackgroundTask).where(
            BackgroundTask.owner_id == user,
            BackgroundTask.type == "citation_graph",
            BackgroundTask.status.in_(("queued", "running")),
        )
    ):
        if task.args.get("graph_id") == graph_id:
            return {"graph_id": graph_id, "task_id": task.id, "status": task.status}
    task = BackgroundTask(
        owner_id=user,
        type="citation_graph",
        args={
            "graph_id": graph_id,
            "work_id": data.work_id,
            "direction": data.direction,
            "limit": data.per_direction_limit,
            "force": force,
        },
    )
    db.add(task)
    db.commit()
    expand_citation_graph.delay(
        task.id, graph_id, data.work_id, data.direction, data.per_direction_limit, force
    )
    return {"graph_id": graph_id, "task_id": task.id, "status": "queued"}


@app.post(API + "/graphs/{graph_id}/expand")
def expand_graph(
    graph_id: str,
    data: GraphExpandIn,
    user: str = Depends(current_user),
    db: Session = Depends(get_db),
):
    return graph_task(db, user, graph_id, data)


@app.post(API + "/graphs/{graph_id}/refresh")
def refresh_graph(
    graph_id: str,
    data: GraphRefreshIn,
    user: str = Depends(current_user),
    db: Session = Depends(get_db),
):
    return graph_task(db, user, graph_id, data, force=True)


@app.get(API + "/graphs/{graph_id}/nodes/{work_id}")
def get_graph_node(
    graph_id: str, work_id: str, user: str = Depends(current_user), db: Session = Depends(get_db)
):
    graph = db.get(GraphView, graph_id)
    if not graph or graph.owner_id != user:
        raise HTTPException(404, "Graph not found")
    node = next(
        (node for node in serialize_view(db, graph)["nodes"] if node["id"] == work_id), None
    )
    if not node:
        raise HTTPException(404, "Graph node not found")
    return node


@app.post(API + "/graphs/{graph_id}/nodes/{work_id}/import")
def import_graph_node(
    graph_id: str, work_id: str, user: str = Depends(current_user), db: Session = Depends(get_db)
):
    view = db.get(GraphView, graph_id)
    if not view or view.owner_id != user:
        raise HTTPException(404, "Graph not found")
    node = db.scalar(
        select(GraphViewNode).where(
            GraphViewNode.graph_id == graph_id,
            GraphViewNode.work_id == work_id,
        )
    )
    if not node:
        raise HTTPException(404, "Graph node not found")
    work = db.get(ScholarlyWork, work_id)
    existing = db.scalar(select(Paper).where(Paper.owner_id == user, Paper.work_id == work_id))
    if existing:
        return {"paper_id": existing.id, "status": "already_imported"}
    paper = Paper(
        owner_id=user,
        title=work.title,
        authors=work.authors,
        abstract=work.abstract,
        year=work.year,
        doi=work.doi,
        arxiv_id=work.arxiv_id,
        openalex_id=f"https://openalex.org/{work.openalex_id}" if work.openalex_id else None,
        source=work.metadata_source,
    )
    db.add(paper)
    link_paper_to_work(db, paper, work)
    url = f"https://arxiv.org/pdf/{work.arxiv_id}" if work.arxiv_id else None
    return persist_discovered_paper(db, paper, url, user)


@app.post(API + "/research/synthesize")
def synthesize(data: ChatIn, user: str = Depends(current_user), db: Session = Depends(get_db)):
    run = create_run(db, user, data.question, data.paper_ids, "synthesis")
    return {"run_id": run.id, "future_work": future_work(db, data.paper_ids)}


@app.get(API + "/copilot/draft/graph-options")
def draft_graph_options(user: str = Depends(current_user), db: Session = Depends(get_db)):
    return {"items": graph_options(db, user)}


@app.post(API + "/copilot/draft/analyze")
def draft_analyze(data: DraftAnalyzeIn, _user: str = Depends(current_user)):
    try:
        return {"fields": analyze_idea(data.idea)}
    except (ValueError, TypeError) as exc:
        raise HTTPException(422, f"IDEA 分析失败：{exc}") from exc


@app.post(API + "/copilot/draft/complete")
def draft_complete(data: DraftCompleteIn, _user: str = Depends(current_user)):
    try:
        return {"fields": complete_fields(data.idea, data.fields)}
    except (ValueError, TypeError) as exc:
        raise HTTPException(422, f"IDEA 补全失败：{exc}") from exc


@app.post(API + "/copilot/draft")
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


@app.post(API + "/copilot/diagrams")
def diagram(data: DiagramIn, _user: str = Depends(current_user)):
    try:
        return {
            "format": "mermaid",
            "script": generate_mermaid(data.description, data.previous, data.error),
        }
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.post(API + "/copilot/charts")
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


@app.post(API + "/copilot/charts/caption")
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


@app.get(API + "/artifacts/{artifact_id}")
def get_artifact(
    artifact_id: str, user: str = Depends(current_user), db: Session = Depends(get_db)
):
    artifact = db.get(Artifact, artifact_id)
    if not artifact or artifact.owner_id != user:
        raise HTTPException(404, "Artifact not found")
    media_type = "text/csv" if artifact.type == "csv" else "image/png"
    return Response(Storage().get(artifact.file_key), media_type=media_type)
