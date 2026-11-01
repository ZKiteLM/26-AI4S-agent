"""Candidate enrichment and evidence-bound recommendation scoring."""

import json
import logging
import math
import re
from datetime import date, datetime

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from .ai import generate
from .discovery import inspect_github, openalex_metrics
from .models import PaperCandidate, Recommendation, now

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
        if candidate.github_stars is not None
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
    candidate.updated_at = now()
    return candidate


def enrich_candidate(candidate: PaperCandidate) -> None:
    if candidate.citation_count is None and (
        candidate.doi or candidate.arxiv_id or candidate.openalex_id
    ):
        metrics = openalex_metrics(candidate.doi, candidate.arxiv_id, candidate.openalex_id)
        candidate.openalex_id = candidate.openalex_id or metrics.get("openalex_id")
        candidate.citation_count = metrics.get("citation_count")
    if candidate.github_stars is None:
        code = inspect_github(candidate.abstract + " " + (candidate.source_url or ""))
        candidate.github_url = code.get("repository")
        candidate.github_stars = code.get("stars") if code.get("verified") else None


def upsert_recommendation(
    db: Session,
    owner_id: str,
    topic: str,
    candidate: PaperCandidate,
    analysis: dict | None = None,
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
    if rec.status != "pending":
        return rec
    if analysis and (
        rec.relevance_score is None or analysis["relevance_score"] >= rec.relevance_score
    ):
        rec.relevance_score = analysis["relevance_score"]
        rec.value_score = analysis["value_score"]
        rec.summary = analysis["summary"]
        rec.reason = analysis["reason"]
    if rec.relevance_score is not None:
        analysis = {"relevance_score": rec.relevance_score, "value_score": rec.value_score}
    rec.score, rec.reasons = weighted_score(candidate, analysis)
    if rec.relevance_score is None or rec.value_score is None:
        rec.score = None
    return rec
