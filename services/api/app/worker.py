import logging
import os
import re
from collections import defaultdict, deque
from datetime import timedelta
from uuid import uuid4

from celery import Celery
from sqlalchemy import and_, delete, or_, select, update

from .chat_agent import number_citations, run_react
from .citation_graph_service import expand_view
from .citation_identity import normalize_identifier, upsert_work
from .citation_providers import resolve_paper
from .config import settings
from .db import SessionLocal
from .discovery import search_arxiv, search_external
from .graph_insights import generate_future, generate_review, save_insight
from .internal_citation_graph import build_internal_graph
from .models import (
    AgentRun,
    BackgroundTask,
    Block,
    ChatMessage,
    ChatSession,
    Chunk,
    GraphView,
    GraphViewNode,
    Paper,
    PaperCandidate,
    Recommendation,
    RunEvent,
    Subscription,
    Translation,
    Version,
    now,
)
from .observability import record
from .parsing import parse_pdf
from .recommender import (
    enrich_candidate,
    enrichment_due,
    refresh_candidate_scores,
    score_analyses,
    upsert_candidate,
    upsert_recommendation,
)
from .retrieval import index_chunks, make_chunks, remove_version_index
from .storage import Storage
from .subscription_keywords import normalize_keywords

logger = logging.getLogger(__name__)

celery_app = Celery("research", broker=settings().redis_url, backend=settings().redis_url)
celery_app.conf.task_default_queue = "default"
celery_app.conf.task_always_eager = os.getenv("CELERY_TASK_ALWAYS_EAGER") == "1"
celery_app.conf.beat_schedule = {
    "schedule-due-subscriptions": {
        "task": "research.schedule_due_subscriptions",
        "schedule": 900.0,
    },
    "refresh-candidate-metrics-daily": {
        "task": "research.refresh_candidate_metrics",
        "schedule": 86400.0,
    },
}


def event(db, run_id: str, name: str, data: dict) -> None:
    previous = db.scalars(
        select(RunEvent).where(RunEvent.run_id == run_id).order_by(RunEvent.sequence.desc())
    ).first()
    db.add(
        RunEvent(
            run_id=run_id, sequence=previous.sequence + 1 if previous else 1, event=name, data=data
        )
    )
    db.commit()


@celery_app.task(name="research.parse_version")
def parse_version(task_id: str, version_id: str) -> None:
    with SessionLocal() as db:
        task = db.get(BackgroundTask, task_id)
        version = db.get(Version, version_id)
        if not task or not version:
            logger.warning("Skipped obsolete parse task %s for version %s", task_id, version_id)
            return
        had_blocks = (
            db.scalar(select(Block.id).where(Block.version_id == version_id).limit(1)) is not None
        )
        task.status = version.parse_status = "running"
        db.commit()
        record("parser", "INFO", "开始解析 PDF", task_id=task_id, version_id=version_id)
        try:
            data = Storage().get(version.file_key)

            def progress(done: int, total: int) -> None:
                task.progress = done / total
                db.commit()
                record("parser", "INFO", "解析页进度", task_id=task_id, done=done, total=total)

            parsed = parse_pdf(data, progress)
            previous = defaultdict(deque)
            seen_old_ids = set()
            for old, translated in db.execute(
                select(Block, Translation)
                .join(Translation, Translation.block_id == Block.id)
                .where(Block.version_id == version_id)
                .order_by(Block.page_index, Block.order)
            ):
                key = (old.page_index, old.type, old.markdown or old.text)
                if old.id not in seen_old_ids:
                    previous[key].append([])
                    seen_old_ids.add(old.id)
                previous[key][-1].append((translated.target_lang, translated.text))
            remove_version_index(version_id)
            db.execute(delete(Chunk).where(Chunk.version_id == version_id))
            db.execute(
                delete(Translation).where(
                    Translation.block_id.in_(select(Block.id).where(Block.version_id == version_id))
                )
            )
            db.execute(delete(Block).where(Block.version_id == version_id))
            new_blocks = [Block(version_id=version_id, **block) for block in parsed]
            db.add_all(new_blocks)
            db.flush()
            restored = 0
            for block in new_blocks:
                matches = previous.get((block.page_index, block.type, block.markdown or block.text))
                if matches:
                    for language, text in matches.popleft():
                        db.add(Translation(block_id=block.id, target_lang=language, text=text))
                        restored += 1
            db.commit()
            chunks = make_chunks(db, version_id)
            paper = db.get(Paper, version.paper_id)
            index_chunks(db, chunks, paper.id, paper.owner_id)
            task.status = version.parse_status = "completed"
            task.result = {
                "version_id": version_id,
                "blocks": len(parsed),
                "chunks": len(chunks),
                "parsers": {
                    name: sum(block["parser"] == name for block in parsed)
                    for name in {block["parser"] for block in parsed}
                },
                "equations": sum(block["type"] == "equation" for block in parsed),
                "tables": sum(block["type"] == "table" for block in parsed),
                "translations_preserved": restored,
            }
            task.progress = 1
            record("parser", "INFO", "PDF 解析与索引完成", task_id=task_id, **task.result)
        except Exception as exc:
            db.rollback()
            task.status = "failed"
            version.parse_status = "completed" if had_blocks else "failed"
            task.error = str(exc)[:1000]
            record("parser", "ERROR", "PDF 解析失败", task_id=task_id, error=task.error)
        db.commit()


@celery_app.task(name="research.run_agent")
def run_agent(run_id: str) -> None:
    record("worker", "INFO", "科研任务开始", run_id=run_id)
    with SessionLocal() as db:
        run = db.get(AgentRun, run_id)
        run.status = "running"
        db.commit()
    try:

        def emit(name: str, data: dict) -> None:
            with SessionLocal() as session_db:
                event(session_db, run_id, name, data)

        draft, evidence = run_react(run, emit)
        answer, cited = number_citations(draft, evidence)
        result = {"answer": answer, "evidence": cited}
        with SessionLocal() as db:
            run = db.get(AgentRun, run_id)
            run.answer = result["answer"]
            run.evidence = result["evidence"]
            run.status = "completed"
            if run.session_id:
                db.add(
                    ChatMessage(
                        session_id=run.session_id,
                        role="assistant",
                        content=run.answer,
                        run_id=run.id,
                        evidence=run.evidence,
                    )
                )
                db.get(ChatSession, run.session_id).updated_at = now()
            db.commit()
            event(db, run_id, "completed", {"answer": run.answer, "evidence": run.evidence})
            record("worker", "INFO", "科研任务完成", run_id=run_id)
    except Exception as exc:
        with SessionLocal() as db:
            run = db.get(AgentRun, run_id)
            run.status = "failed"
            run.error = str(exc)[:1000]
            db.commit()
            event(db, run_id, "failed", {"error": run.error})
            record("worker", "ERROR", "科研任务失败", run_id=run_id, error=run.error)


@celery_app.task(name="research.build_citation_graph")
def build_citation_graph(task_id: str, graph_id: str) -> None:
    with SessionLocal() as db:
        task = db.get(BackgroundTask, task_id)
        view = db.get(GraphView, graph_id)
        if not task or not view:
            return
        task.status = view.status = "running"
        db.commit()
        errors = []
        try:
            if view.config.get("mode") == "internal":
                errors = build_internal_graph(
                    db, view, lambda value: _citation_progress(db, task, value)
                )
                view.version += 1
                view.status = task.status = "partial" if errors else "completed"
                view.error = "; ".join(errors)[:1000] if errors else None
                task.progress = 1
                task.result = {"graph_id": graph_id, "version": view.version, "errors": errors}
                db.commit()
                return
            for seed in view.config.get("external_seeds", []):
                namespace = seed["type"]
                field = {"doi": "doi", "arxiv": "arxiv_id", "openalex": "openalex_id"}[namespace]
                metadata = {field: normalize_identifier(namespace, seed["value"])}
                found = resolve_paper(metadata)
                if not found:
                    errors.append(f"Could not resolve external {namespace} seed")
                    continue
                data, provider = found
                work = upsert_work(db, data, provider)
                if not db.scalar(
                    select(GraphViewNode).where(
                        GraphViewNode.graph_id == graph_id,
                        GraphViewNode.work_id == work.id,
                    )
                ):
                    db.add(GraphViewNode(graph_id=graph_id, work_id=work.id, is_seed=True))
                db.commit()
            seeds = db.scalars(
                select(GraphViewNode).where(
                    GraphViewNode.graph_id == graph_id,
                    GraphViewNode.is_seed.is_(True),
                )
            ).all()
            if not seeds:
                raise ValueError("No seed could be resolved")
            partial = False
            for index, seed in enumerate(seeds):
                result = expand_view(
                    db,
                    graph_id,
                    seed.work_id,
                    view.config["direction"],
                    view.config["per_direction_limit"],
                )
                for item in result.get("expansion", {}).get("directions", {}).values():
                    if item.get("error"):
                        errors.append(item["error"])
                    if item.get("status") != "complete" or item.get("has_more"):
                        partial = True
                task.progress = (index + 1) / len(seeds)
                db.commit()
            view.status = "partial" if errors or partial else "completed"
            task.status = view.status
            task.progress = 1
            task.result = {"graph_id": graph_id, "version": view.version, "errors": errors}
            view.error = "; ".join(errors)[:1000] if errors else None
        except Exception as exc:
            db.rollback()
            task.status = view.status = "failed"
            task.error = view.error = str(exc)[:1000]
            logger.exception("Citation graph build failed: %s", graph_id)
        db.commit()


def _citation_progress(db, task, value: float) -> None:
    task.progress = value
    db.commit()


@celery_app.task(name="research.expand_citation_graph")
def expand_citation_graph(
    task_id: str, graph_id: str, work_id: str, direction: str, limit: int, force: bool = False
) -> None:
    with SessionLocal() as db:
        task = db.get(BackgroundTask, task_id)
        view = db.get(GraphView, graph_id)
        if not task or not view:
            return
        task.status = "running"
        db.commit()
        try:
            result = expand_view(db, graph_id, work_id, direction, limit, force=force)
            task.result = {
                "graph_id": graph_id,
                "version": result["version"],
                **result["expansion"],
            }
            task.status = "partial" if result["status"] == "partial" else "completed"
            task.progress = 1
        except Exception as exc:
            db.rollback()
            task.status = "failed"
            task.error = str(exc)[:1000]
            logger.exception("Citation graph expansion failed: %s", graph_id)
        db.commit()


@celery_app.task(name="research.generate_graph_insight")
def generate_graph_insight(task_id: str, graph_id: str, kind: str) -> None:
    with SessionLocal() as db:
        task = db.get(BackgroundTask, task_id)
        view = db.get(GraphView, graph_id)
        if not task or not view or task.status != "queued":
            return
        version = view.version
        task.status = "running"
        db.commit()
        try:
            content, sources = (
                generate_review(db, view) if kind == "review" else generate_future(db, view)
            )
            db.expire_all()
            view = db.get(GraphView, graph_id)
            if not view or view.version != version:
                raise ValueError("图谱已变化，请重新生成")
            save_insight(db, view, kind, content, sources)
            task.status = "completed"
            task.progress = 1
            task.result = {"graph_id": graph_id, "kind": kind, "paper_count": len(sources)}
        except Exception as exc:
            db.rollback()
            task = db.get(BackgroundTask, task_id)
            if task:
                task.status = "failed"
                task.error = str(exc)[:1000]
            logger.exception("Graph %s insight failed: %s", kind, graph_id)
        db.commit()


ACTIVE_REFRESH = ("queued", "searching", "scoring")


def enqueue_subscription_refresh(db, sub: Subscription, mode: str) -> bool:
    """Atomically reserve one refresh slot before publishing a Celery task."""
    current = now()
    token = str(uuid4())
    available = [
        Subscription.refresh_status.notin_(ACTIVE_REFRESH),
        Subscription.refresh_requested_at < current - timedelta(hours=2),
    ]
    if mode == "manual":
        available.append(
            and_(
                Subscription.refresh_status == "queued",
                Subscription.refresh_mode == "auto",
                Subscription.active.is_(False),
            )
        )
    where = [
        Subscription.id == sub.id,
        Subscription.deleted_at.is_(None),
        or_(*available),
    ]
    if mode == "auto":
        where.extend((Subscription.active.is_(True), Subscription.next_refresh_at <= current))
    changed = db.execute(
        update(Subscription)
        .where(*where)
        .values(
            refresh_status="queued",
            refresh_token=token,
            refresh_mode=mode,
            refresh_requested_at=current,
            refresh_error=None,
        )
        .execution_options(synchronize_session=False)
    ).rowcount
    db.commit()
    db.refresh(sub)
    if not changed:
        return False
    try:
        refresh_subscriptions.delay(sub.id, token)
    except Exception as exc:
        db.execute(
            update(Subscription)
            .where(Subscription.id == sub.id, Subscription.refresh_token == token)
            .values(
                refresh_status="failed",
                refresh_error=f"任务提交失败：{exc}"[:500],
                next_refresh_at=current + timedelta(hours=1)
                if mode == "auto"
                else sub.next_refresh_at,
            )
        )
        db.commit()
        raise
    return True


@celery_app.task(name="research.schedule_due_subscriptions")
def schedule_due_subscriptions() -> None:
    with SessionLocal() as db:
        due = db.scalars(
            select(Subscription)
            .where(
                Subscription.active.is_(True),
                Subscription.deleted_at.is_(None),
                Subscription.next_refresh_at <= now(),
            )
            .order_by(Subscription.next_refresh_at)
            .limit(100)
        ).all()
        for sub in due:
            try:
                enqueue_subscription_refresh(db, sub, "auto")
            except Exception:
                logger.exception("Failed to queue subscription %s", sub.id)


def _discovered_works(sub: Subscription) -> tuple[list[tuple[dict, list[str]]], list[str]]:
    keywords = normalize_keywords(sub.topic, sub.keywords or [sub.topic])[:10]
    cutoff = (
        max(sub.last_checked_at.date() - timedelta(days=2), now().date() - timedelta(days=90))
        if sub.last_checked_at
        else now().date() - timedelta(days=30)
    )
    buckets: list[tuple[str, list[dict]]] = []
    errors = []
    for keyword in keywords:
        for source, search_fn in (("OpenAlex", search_external), ("arXiv", search_arxiv)):
            try:
                buckets.append((keyword, search_fn(keyword, 8, cutoff)))
            except Exception as exc:
                logger.exception("%s search failed for subscription %s", source, sub.id)
                errors.append(f"{source} / {keyword}: {type(exc).__name__}")
    chosen: dict[str, tuple[dict, list[str]]] = {}
    for rank in range(8):
        for keyword, works in buckets:
            if rank >= len(works):
                continue
            work = works[rank]
            if not work.get("title") or not work.get("abstract"):
                continue
            # Title is stable across arXiv/OpenAlex even when one source lacks identifiers.
            key = re.sub(r"\W+", "", work["title"].casefold())
            if not key:
                continue
            if key not in chosen:
                if len(chosen) >= 40:
                    continue
                chosen[key] = (work, [keyword])
            else:
                previous, matched = chosen[key]
                if keyword not in matched:
                    matched.append(keyword)
                if work.get("source") == "OpenAlex" and previous.get("source") != "OpenAlex":
                    chosen[key] = (work, matched)
    return list(chosen.values()), errors


@celery_app.task(name="research.refresh_subscriptions")
def refresh_subscriptions(subscription_id: str | None = None, token: str | None = None) -> None:
    if subscription_id is None:
        schedule_due_subscriptions.run()
        return
    with SessionLocal() as db:
        sub = db.get(Subscription, subscription_id)
        if not sub or sub.deleted_at or (token and sub.refresh_token != token):
            return
        if token and sub.refresh_mode == "auto" and not sub.active:
            sub.refresh_status = "idle"
            db.commit()
            return
        mode = sub.refresh_mode if token else "auto"
        sub.refresh_status = "searching"
        db.commit()
        try:
            discovered, source_errors = _discovered_works(sub)
            errors = []
            # Lock this subscription while writing recommendations. A concurrent delete
            # waits and then removes this topic's recommendations in the same order.
            sub = db.scalar(
                select(Subscription).where(Subscription.id == subscription_id).with_for_update()
            )
            if not sub or sub.deleted_at or (token and sub.refresh_token != token):
                db.rollback()
                return
            sub.refresh_status = "scoring"
            db.commit()
            sub = db.scalar(
                select(Subscription).where(Subscription.id == subscription_id).with_for_update()
            )
            if not sub or sub.deleted_at or (token and sub.refresh_token != token):
                db.rollback()
                return
            candidates, matches, seen = [], {}, set()
            for work, keywords in discovered:
                try:
                    with db.begin_nested():
                        candidate = upsert_candidate(db, work)
                        matches.setdefault(candidate.id, [])
                        matches[candidate.id] = list(
                            dict.fromkeys([*matches[candidate.id], *keywords])
                        )
                        if candidate.id not in seen:
                            if enrich_candidate(candidate):
                                refresh_candidate_scores(db, candidate)
                            db.flush()
                            seen.add(candidate.id)
                            candidates.append(candidate)
                except Exception:
                    logger.exception("Candidate refresh failed for %s", work.get("title"))
                    errors.append(f"候选论文处理失败：{work.get('title', '')[:80]}")
            pending = db.scalars(
                select(Recommendation).where(
                    Recommendation.owner_id == sub.owner_id,
                    Recommendation.status == "pending",
                )
            ).all()
            for rec in pending:
                if len(candidates) >= 50:
                    break
                if sub.topic not in rec.topics or rec.candidate_id in seen:
                    continue
                candidate = db.get(PaperCandidate, rec.candidate_id)
                if not candidate or (
                    sub.topic in (rec.topic_analyses or {}) and not enrichment_due(candidate)
                ):
                    continue
                try:
                    with db.begin_nested():
                        if enrich_candidate(candidate):
                            refresh_candidate_scores(db, candidate)
                        db.flush()
                        seen.add(candidate.id)
                        candidates.append(candidate)
                except Exception:
                    logger.exception("Candidate enrichment retry failed for %s", rec.candidate_id)
            needs_analysis = []
            for candidate in candidates:
                existing = db.scalar(
                    select(Recommendation).where(
                        Recommendation.owner_id == sub.owner_id,
                        Recommendation.candidate_id == candidate.id,
                    )
                )
                if not existing or (
                    existing.status == "pending"
                    and sub.topic not in (existing.topic_analyses or {})
                ):
                    needs_analysis.append(candidate)
            analyses = {}
            for index in range(0, len(needs_analysis), 4):
                analyses.update(score_analyses(sub.topic, needs_analysis[index : index + 4]))
            if len(analyses) < len(needs_analysis):
                errors.append("部分论文的 LLM 相关性评分失败")
            for candidate in candidates:
                try:
                    with db.begin_nested():
                        upsert_recommendation(
                            db,
                            sub.owner_id,
                            sub.topic,
                            candidate,
                            analyses.get(candidate.id),
                            matches.get(candidate.id),
                        )
                        db.flush()
                except Exception:
                    logger.exception("Recommendation scoring failed for %s", candidate.id)
                    errors.append(f"推荐记录保存失败：{candidate.id}")
            if errors or (source_errors and not discovered):
                sub.refresh_status = "failed"
                sub.refresh_error = "；".join((errors or source_errors)[:4])[:500]
                if mode == "auto":
                    sub.next_refresh_at = now() + timedelta(hours=1)
            else:
                sub.refresh_status = "completed"
                sub.refresh_error = None
                sub.last_checked_at = now()
                if source_errors:
                    logger.warning(
                        "Subscription %s completed with unavailable sources: %s",
                        subscription_id,
                        "; ".join(source_errors),
                    )
                if mode == "auto":
                    sub.last_auto_refreshed_at = sub.last_checked_at
                    sub.next_refresh_at = sub.last_checked_at + timedelta(
                        hours=1 if source_errors else 24 * 7
                    )
                elif sub.active and (
                    sub.last_auto_refreshed_at is None
                    or sub.next_refresh_at is not None
                    and sub.next_refresh_at <= sub.last_checked_at + timedelta(hours=1)
                ):
                    # A successful manual run supersedes a failed automatic run's retry.
                    sub.next_refresh_at = sub.last_checked_at + timedelta(days=7)
            db.commit()
        except Exception as exc:
            logger.exception("Subscription refresh failed for %s", subscription_id)
            db.rollback()
            sub = db.get(Subscription, subscription_id)
            if sub and not sub.deleted_at and (not token or sub.refresh_token == token):
                sub.refresh_status = "failed"
                sub.refresh_error = str(exc)[:500]
                if mode == "auto":
                    sub.next_refresh_at = now() + timedelta(hours=1)
                db.commit()


@celery_app.task(name="research.refresh_candidate_metrics")
def refresh_candidate_metrics() -> None:
    """Refresh cached evidence for older recommendations without rerunning the LLM."""
    with SessionLocal() as db:
        candidates = db.scalars(select(PaperCandidate).order_by(PaperCandidate.updated_at)).all()
        refreshed = 0
        for candidate in candidates:
            if not enrichment_due(candidate):
                continue
            try:
                with db.begin_nested():
                    if enrich_candidate(candidate):
                        refresh_candidate_scores(db, candidate)
                db.commit()
                refreshed += 1
                if refreshed >= 500:
                    break
            except Exception:
                logger.exception("Candidate metric refresh failed for %s", candidate.id)
