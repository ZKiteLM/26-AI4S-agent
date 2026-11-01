"""Candidate enrichment and evidence-bound recommendation scoring."""

import json
import logging
import math
import re
from datetime import date, datetime, timedelta

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from .ai import generate
from .config import settings
from .discovery import citation_metrics
from .models import PaperCandidate, Recommendation, now
from .resolve_paper_code import resolve_paper_code

logger = logging.getLogger(__name__)


def title_key(title: str) -> str:
    return re.sub(r"\W+", "", title.casefold(), flags=re.UNICODE)


def _validated_analysis(data: dict) -> dict:
    relevance = float(data["relevance_score"])
    value = float(data["value_score"])
    if not math.isfinite(relevance) or not math.isfinite(value):
        raise ValueError("Non-finite recommendation score")
    return {
        "relevance_score": max(0, min(100, relevance)),
        "value_score": max(0, min(100, value)),
        "summary": str(data["summary"])[:600],
        "reason": str(data["reason"])[:600],
    }


def score_analyses(topic: str, candidates: list[PaperCandidate]) -> dict[str, dict]:
    """Evaluate up to four abstracts per LLM call to limit request overhead."""
    if not candidates:
        return {}
    prompt = json.dumps(
        {
            "topic": topic,
            "papers": [
                {
                    "id": candidate.id,
                    "title": candidate.title,
                    "abstract": candidate.abstract[:1800],
                }
                for candidate in candidates[:4]
            ],
        },
        ensure_ascii=False,
    )
    try:
        raw = generate(
            "只根据输入的标题、摘要和研究主题评分。返回 JSON 对象，字段 items 为数组；"
            "每项严格包含 id、relevance_score、value_score、summary、reason。"
            "分数为 0-100 数字；summary 和 reason 用简短中文。"
            "每篇论文只输出一项，不编造摘要未提到的贡献。",
            prompt,
            max_tokens=1100,
            json_mode=True,
        )
        ids = {candidate.id for candidate in candidates[:4]}
        results = {}
        for item in json.loads(raw)["items"]:
            if item.get("id") in ids:
                results[item["id"]] = _validated_analysis(item)
        return results
    except Exception:
        logger.exception("Recommendation batch analysis failed")
        return {}


def weighted_score(candidate: PaperCandidate, analysis: dict | None) -> tuple[float, dict]:
    age_days = (
        max(0, (date.today() - candidate.published_at.date()).days)
        if candidate.published_at
        else None
    )
    age_years = (age_days or 0) / 365.25
    factors = {
        "relevance": analysis["relevance_score"] if analysis else None,
        "value": analysis["value_score"] if analysis else None,
        "citations": min(100, 25 * math.log1p(candidate.citation_count / (age_years + 1)))
        if candidate.citation_count is not None
        else None,
        "github_stars": min(100, 20 * math.log1p(candidate.github_stars))
        if candidate.github_stars is not None and candidate.github_verification == "verified"
        else None,
        "recency": max(0, 100 * math.exp(-age_years / 3)) if age_days is not None else None,
    }
    weights = {"relevance": 45, "value": 25, "citations": 15, "github_stars": 10, "recency": 5}
    available = {key: value for key, value in factors.items() if value is not None}
    total = sum(weights[key] for key in available)
    score = sum(value * weights[key] for key, value in available.items()) / total if total else 0
    details = {
        key: round(value, 1) if value is not None else None for key, value in factors.items()
    }
    details["effective_weights"] = (
        {key: round(100 * weights[key] / total, 1) for key in available} if total else {}
    )
    return round(score, 1), details


def upsert_candidate(db: Session, work: dict) -> PaperCandidate:
    key = title_key(work["title"])
    if not key:
        raise ValueError("Candidate title is empty")
    checks = [PaperCandidate.title_key == key]
    for field in ("doi", "arxiv_id", "openalex_id"):
        if work.get(field):
            checks.append(getattr(PaperCandidate, field) == work[field])
    candidate = db.scalar(select(PaperCandidate).where(or_(*checks)))
    if candidate is None:
        candidate = PaperCandidate(title_key=key, title=work["title"], source=work["source"])
        db.add(candidate)
        db.flush()
    for field in (
        "authors",
        "abstract",
        "doi",
        "arxiv_id",
        "openalex_id",
        "pdf_url",
        "source_url",
        "year",
        "citation_count",
    ):
        if work.get(field) is not None and (
            not getattr(candidate, field, None) or field == "citation_count"
        ):
            setattr(candidate, field, work[field])
    published = work.get("published_at")
    if published and not candidate.published_at:
        candidate.published_at = datetime.fromisoformat(published[:10])
    if (
        work.get("citation_count") is not None
        and work.get("source") == "OpenAlex"
        and candidate.citation_updated_at is None
    ):
        candidate.citation_source = "openalex"
        candidate.citation_updated_at = now()
        candidate.citation_check_status = "found"
    candidate.updated_at = now()
    return candidate


def _due(checked: datetime | None, status: str | None, *, code: bool = False) -> bool:
    if checked is None:
        return True
    config = settings()
    hours = (
        config.external_error_retry_hours
        if status == "error"
        else config.code_retry_days * 24
        if code and status == "not_found"
        else config.metrics_refresh_hours
    )
    return now().replace(tzinfo=None) - checked.replace(tzinfo=None) >= timedelta(hours=hours)


def enrichment_due(candidate: PaperCandidate) -> bool:
    return _due(candidate.citation_updated_at, candidate.citation_check_status) or _due(
        candidate.code_checked_at, candidate.code_check_status, code=True
    )


def enrich_candidate(candidate: PaperCandidate) -> bool:
    """Refresh external evidence once per candidate, without touching LLM analysis."""
    before = (candidate.citation_count, candidate.github_stars, candidate.github_verification)
    if _due(candidate.citation_updated_at, candidate.citation_check_status):
        metrics = citation_metrics(
            candidate.doi,
            candidate.arxiv_id,
            candidate.openalex_id,
            title=candidate.title,
            authors=candidate.authors,
            year=candidate.year,
        )
        candidate.citation_check_status = metrics["status"]
        candidate.citation_updated_at = now()
        if metrics["status"] == "found":
            candidate.citation_count = metrics["citation_count"]
            candidate.citation_source = metrics["citation_source"]
            candidate.openalex_id = candidate.openalex_id or metrics.get("openalex_id")
    if _due(candidate.code_checked_at, candidate.code_check_status, code=True):
        code = resolve_paper_code(
            title=candidate.title,
            abstract=candidate.abstract,
            arxiv_id=candidate.arxiv_id,
            doi=candidate.doi,
            source_url=candidate.source_url,
        )
        candidate.code_check_status = code["status"]
        candidate.code_checked_at = now()
        candidate.project_page_url = code.get("project_page_url") or candidate.project_page_url
        # Preserve an existing verified official repository over weaker evidence.
        if code.get("repository") and (
            candidate.github_verification != "verified" or code["verification"] == "verified"
        ):
            candidate.github_url = code["repository"]
            candidate.github_source = code["source"]
            candidate.github_verification = code["verification"]
            candidate.github_stars = code.get("stars")
            candidate.github_stars_updated_at = now() if code.get("stars") is not None else None
    candidate.updated_at = now()
    return before != (
        candidate.citation_count,
        candidate.github_stars,
        candidate.github_verification,
    )


def recompute_recommendation(
    rec: Recommendation, candidate: PaperCandidate, *, allow_legacy: bool = True
) -> None:
    analyses = [
        item
        for item in (rec.topic_analyses or {}).values()
        if isinstance(item, dict)
        and item.get("relevance_score") is not None
        and item.get("value_score") is not None
    ]
    if analyses:
        best = max(analyses, key=lambda item: item["relevance_score"])
        rec.relevance_score, rec.value_score = best["relevance_score"], best["value_score"]
        rec.summary, rec.reason = best.get("summary", ""), best.get("reason", "")
    elif not allow_legacy:
        rec.relevance_score = rec.value_score = None
        rec.summary = rec.reason = ""
    analysis = (
        {"relevance_score": rec.relevance_score, "value_score": rec.value_score}
        if rec.relevance_score is not None and rec.value_score is not None
        else None
    )
    rec.score, rec.reasons = weighted_score(candidate, analysis)
    if analysis is None:
        rec.score = None


def refresh_candidate_scores(db: Session, candidate: PaperCandidate) -> None:
    for rec in db.scalars(
        select(Recommendation).where(Recommendation.candidate_id == candidate.id)
    ):
        recompute_recommendation(rec, candidate)


def upsert_recommendation(
    db: Session,
    owner_id: str,
    topic: str,
    candidate: PaperCandidate,
    analysis: dict | None = None,
    matched_keywords: list[str] | None = None,
) -> Recommendation:
    rec = db.scalar(
        select(Recommendation).where(
            Recommendation.owner_id == owner_id, Recommendation.candidate_id == candidate.id
        )
    )
    if rec is None:
        rec = Recommendation(
            owner_id=owner_id,
            candidate_id=candidate.id,
            score=0,
            topics=[],
            status="pending",
            reasons={},
        )
        db.add(rec)
    if topic not in rec.topics:
        rec.topics = [*rec.topics, topic]
    if matched_keywords:
        matches = dict(rec.matched_keywords or {})
        matches[topic] = list(dict.fromkeys([*(matches.get(topic) or []), *matched_keywords]))
        rec.matched_keywords = matches
    if rec.status != "pending":
        return rec
    if analysis:
        by_topic = dict(rec.topic_analyses or {})
        by_topic[topic] = analysis
        rec.topic_analyses = by_topic
    recompute_recommendation(rec, candidate)
    return rec
