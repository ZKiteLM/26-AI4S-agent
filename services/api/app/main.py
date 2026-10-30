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

from .ai import generate
from .auth import check_login, current_user, make_token
from .charts import render_chart
from .config import settings
from .db import Base, SessionLocal, engine, get_db
from .discovery import safe_pdf_download, search_arxiv, search_external
from .graphs import build_graph, future_work
from .models import (
    AgentRun,
    Artifact,
    BackgroundTask,
    Block,
    Chunk,
    CitationEdge,
    Paper,
    Recommendation,
    RunEvent,
    Subscription,
    Translation,
    Version,
)
from .observability import client as diagnostic_client
from .observability import recent as recent_events
from .observability import record
from .parsing import identify_pdf, similarity
from .retrieval import remove_version_index
from .storage import Storage
from .translation import translate_blocks, translate_text
from .worker import celery_app, parse_version, refresh_subscriptions, run_agent


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
    paper_ids: list[str] = Field(min_length=2)


class DraftIn(BaseModel):
    idea: str = Field(min_length=10)


class DiagramIn(BaseModel):
    description: str = Field(min_length=5)
    format: str = "mermaid"


class SubscriptionIn(BaseModel):
    topic: str = Field(min_length=2)


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
    if len(files) > 20:
        raise HTTPException(400, "Upload at most 20 PDFs at once")
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


@app.post(API + "/chat/runs")
def start_chat(data: ChatIn, user: str = Depends(current_user), db: Session = Depends(get_db)):
    return {"run_id": create_run(db, user, data.question, data.paper_ids, "chat").id}


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

        work = httpx.get(
            "https://api.openalex.org/works/" + data.openalex_id.rsplit("/", 1)[-1], timeout=20
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
    db.add(paper)
    db.commit()
    pdf_url = (metadata.get("best_oa_location") or {}).get("pdf_url")
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


@app.get(API + "/subscriptions")
def list_subscriptions(user: str = Depends(current_user), db: Session = Depends(get_db)):
    return {
        "items": [
            {"id": s.id, "topic": s.topic, "active": s.active}
            for s in db.scalars(select(Subscription).where(Subscription.owner_id == user))
        ]
    }


@app.post(API + "/subscriptions")
def subscribe(
    data: SubscriptionIn, user: str = Depends(current_user), db: Session = Depends(get_db)
):
    sub = Subscription(owner_id=user, topic=data.topic)
    db.add(sub)
    db.commit()
    refresh_subscriptions.delay()
    return {"id": sub.id, "topic": sub.topic}


@app.get(API + "/recommendations")
def recommendations(user: str = Depends(current_user), db: Session = Depends(get_db)):
    items = db.scalars(
        select(Recommendation)
        .where(Recommendation.owner_id == user)
        .order_by(Recommendation.score.desc())
    ).all()
    return {
        "items": [
            {
                "id": x.id,
                "paper": paper_json(db.get(Paper, x.paper_id)),
                "score": x.score,
                "scoring_factors": x.reasons,
                "feedback": x.feedback,
            }
            for x in items
        ]
    }


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
    item.score = max(0, min(1, item.score + data.value * 0.1))
    db.commit()
    return {"feedback_id": item.id, "score": item.score}


@app.post(API + "/graphs")
def create_graph(data: GraphIn, user: str = Depends(current_user), db: Session = Depends(get_db)):
    for paper_id in data.paper_ids:
        owned_paper(db, paper_id, user)
    graph = build_graph(db, user, data.paper_ids)
    task = BackgroundTask(owner_id=user, type="graph", status="completed", progress=1, result=graph)
    db.add(task)
    db.commit()
    return {"graph_id": task.id, "task_id": task.id}


@app.get(API + "/graphs/{graph_id}")
def get_graph(graph_id: str, user: str = Depends(current_user), db: Session = Depends(get_db)):
    graph = db.get(BackgroundTask, graph_id)
    if not graph or graph.owner_id != user or graph.type != "graph":
        raise HTTPException(404, "Graph not found")
    return graph.result


@app.post(API + "/research/synthesize")
def synthesize(data: ChatIn, user: str = Depends(current_user), db: Session = Depends(get_db)):
    run = create_run(db, user, data.question, data.paper_ids, "synthesis")
    return {"run_id": run.id, "future_work": future_work(db, data.paper_ids)}


@app.post(API + "/copilot/draft")
def draft(data: DraftIn, _user: str = Depends(current_user)):
    references = search_external(data.idea, 8)
    verified = [x for x in references if x["doi"] or x["arxiv_id"] or x["openalex_id"]]
    if not verified:
        raise HTTPException(422, "No verifiable references found")
    allowed = "\n".join(
        f"[{i + 1}] {x['title']} ({x['year']}) {x['doi'] or x['openalex_id']}"
        for i, x in enumerate(verified)
    )
    body = generate(
        "Create editable academic draft sections Abstract, Introduction, Related Work. "
        "Cite only the numbered verified references. Do not write a References section or invent bibliographic fields.",
        f"Idea: {data.idea}\nVerified references:\n{allowed}",
        max_tokens=1500,
    )
    body = re.split(r"^#{1,3}\s+References\b", body, maxsplit=1, flags=re.I | re.M)[0].rstrip()
    bibliography = "\n".join(
        f"[{i + 1}] {', '.join(item['authors'])}. {item['title']} ({item['year']}). "
        f"{item['doi'] or item['arxiv_id'] or item['openalex_id']}"
        for i, item in enumerate(verified)
    )
    return {"markdown": f"{body}\n\n## References\n\n{bibliography}", "references": verified}


@app.post(API + "/copilot/diagrams")
def diagram(data: DiagramIn, _user: str = Depends(current_user)):
    if data.format not in ("mermaid", "graphviz", "tikz", "matplotlib"):
        raise HTTPException(400, "Unsupported diagram format")
    script = generate(
        f"Create only valid {data.format} diagram source. No code fence or commentary. "
        "Do not include executable shell commands.",
        data.description,
        max_tokens=700,
    )
    if len(script) > 20_000 or not script.strip():
        raise HTTPException(422, "Invalid diagram output")
    return {"format": data.format, "script": script}


@app.post(API + "/copilot/charts")
async def chart(
    file: UploadFile = File(...),
    chart_type: str = "line",
    user: str = Depends(current_user),
    db: Session = Depends(get_db),
):
    if chart_type not in ("line", "bar", "scatter", "radar", "3d", "table"):
        raise HTTPException(400, "Unsupported chart type")
    data = await file.read(5_000_001)
    if len(data) > 5_000_000:
        raise HTTPException(400, "CSV too large")
    try:
        image, script, caption, spec = render_chart(data, chart_type)
    except (ValueError, UnicodeError) as exc:
        raise HTTPException(422, str(exc)) from exc
    artifact = Artifact(
        owner_id=user,
        type="chart",
        file_key=f"artifacts/{user}/{secrets.token_hex(12)}.png",
        metadata_json={"caption": caption, "script": script, "spec": spec},
    )
    Storage().put(artifact.file_key, image, "image/png")
    source = Artifact(
        owner_id=user,
        type="csv",
        file_key=f"artifacts/{user}/{secrets.token_hex(12)}.csv",
        metadata_json={"filename": file.filename},
    )
    Storage().put(source.file_key, data, "text/csv")
    db.add_all([artifact, source])
    db.commit()
    return {
        "task_id": None,
        "artifact_id": artifact.id,
        "caption": caption,
        "script": script,
        "spec": spec,
        "image_url": f"{API}/artifacts/{artifact.id}",
        "data_artifact_id": source.id,
    }


@app.get(API + "/artifacts/{artifact_id}")
def get_artifact(
    artifact_id: str, user: str = Depends(current_user), db: Session = Depends(get_db)
):
    artifact = db.get(Artifact, artifact_id)
    if not artifact or artifact.owner_id != user:
        raise HTTPException(404, "Artifact not found")
    media_type = "text/csv" if artifact.type == "csv" else "image/png"
    return Response(Storage().get(artifact.file_key), media_type=media_type)
