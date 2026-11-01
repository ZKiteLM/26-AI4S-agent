import json
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from services.api.app import worker
from services.api.app.auth import current_user
from services.api.app.db import Base, get_db
from services.api.app.main import app
from services.api.app.models import Paper, PaperCandidate, Recommendation, Subscription, now
from services.api.app.subscription_keywords import generate_keywords


@pytest.fixture
def tracking(monkeypatch):
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    sessions = sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(worker, "SessionLocal", sessions)

    def db_override():
        with sessions() as db:
            yield db

    app.dependency_overrides[get_db] = db_override
    app.dependency_overrides[current_user] = lambda: "owner"
    queued = []
    monkeypatch.setattr(worker.refresh_subscriptions, "delay", lambda *args: queued.append(args))
    with TestClient(app) as client:
        yield client, sessions, queued
    app.dependency_overrides.clear()
    engine.dispose()


def work(title, source="OpenAlex"):
    return {
        "title": title,
        "authors": ["Researcher"],
        "abstract": "A paper about agents.",
        "year": 2026,
        "published_at": "2026-10-01",
        "source": source,
        "openalex_id": None,
        "arxiv_id": None,
        "doi": None,
        "citation_count": None,
        "source_url": None,
        "pdf_url": None,
    }


def test_keyword_generation_keeps_llm_suggestions_and_deduplicates(monkeypatch):
    monkeypatch.setattr(
        "services.api.app.subscription_keywords.generate",
        lambda *a, **k: json.dumps(
            {
                "keywords": [
                    "LLM agents",
                    "autonomous agents powered by language models",
                    "agent-based large language models",
                    "LLM agents",
                    "large language model agents",
                    "  ",
                    123,
                ]
            }
        ),
    )
    assert generate_keywords("large language model agents") == [
        "LLM agents",
        "autonomous agents powered by language models",
        "agent-based large language models",
    ]
    monkeypatch.setattr("services.api.app.subscription_keywords.generate", lambda *a, **k: "bad")
    with pytest.raises(ValueError, match="JSON"):
        generate_keywords("large language model agents")


def test_keyword_api_failure_and_persistence(tracking, monkeypatch):
    client, sessions, queued = tracking
    monkeypatch.setattr(
        "services.api.app.subscription_keywords.generate",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down")),
    )
    assert (
        client.post("/api/v1/subscriptions/keywords", json={"topic": "LLM agents"}).status_code
        == 503
    )
    created = client.post(
        "/api/v1/subscriptions",
        json={
            "topic": "LLM agents",
            "keywords": ["large language model agents", "LLM agent", "LLM agent"],
        },
    )
    assert created.status_code == 200
    sub_id = created.json()["id"]
    assert created.json()["keywords"] == ["LLM agents", "large language model agents", "LLM agent"]
    assert created.json()["refresh_status"] == "queued" and len(queued) == 1
    assert client.post(f"/api/v1/subscriptions/{sub_id}/refresh").json()["queued"] is False
    edited = client.patch(
        f"/api/v1/subscriptions/{sub_id}", json={"keywords": ["LLM-based agents"]}
    )
    assert edited.json()["keywords"] == ["LLM agents", "LLM-based agents"]
    with sessions() as db:
        assert db.get(Subscription, sub_id).keywords == edited.json()["keywords"]
    assert client.get(f"/api/v1/subscriptions/{sub_id}/refresh-status").status_code == 200


def test_multi_query_search_and_original_topic_scoring(tracking, monkeypatch):
    client, sessions, queued = tracking
    created = client.post(
        "/api/v1/subscriptions",
        json={"topic": "LLM agents", "keywords": ["large language model agents"]},
    )
    sub_id, token = queued[0]
    calls, scored = [], []

    def external(query, limit, cutoff):
        calls.append(("OpenAlex", query, limit))
        return [work("Shared Paper"), work("Unique OA " + query)]

    def arxiv(query, limit, cutoff):
        calls.append(("arXiv", query, limit))
        return [work("Shared Paper", "arXiv"), work("Unique AX " + query, "arXiv")]

    def score(topic, candidates):
        scored.append(topic)
        return {
            candidate.id: {
                "relevance_score": 80,
                "value_score": 70,
                "summary": "概述",
                "reason": "与原主题相关",
            }
            for candidate in candidates
        }

    monkeypatch.setattr(worker, "search_external", external)
    monkeypatch.setattr(worker, "search_arxiv", arxiv)
    monkeypatch.setattr(worker, "score_analyses", score)
    monkeypatch.setattr(worker, "enrich_candidate", lambda candidate: False)
    worker.refresh_subscriptions.run(sub_id, token)
    assert len(calls) == 4
    assert {entry[1] for entry in calls} == {"LLM agents", "large language model agents"}
    assert all(entry[2] == 8 for entry in calls)
    assert scored and set(scored) == {"LLM agents"}
    with sessions() as db:
        sub = db.get(Subscription, sub_id)
        assert sub.refresh_status == "completed" and sub.last_checked_at is not None
        assert sub.next_refresh_at - sub.last_auto_refreshed_at == timedelta(days=7)
        assert db.scalar(select(func.count()).select_from(Paper)) == 0
        recs = db.scalars(select(Recommendation)).all()
        assert len(recs) == 5
        shared = next(
            rec for rec in recs if db.get(PaperCandidate, rec.candidate_id).title == "Shared Paper"
        )
        assert shared.matched_keywords["LLM agents"] == [
            "LLM agents",
            "large language model agents",
        ]
    assert created.json()["topic"] == "LLM agents"


def test_scheduler_pause_manual_refresh_and_duplicate_guard(tracking, monkeypatch):
    client, sessions, queued = tracking
    first = client.post("/api/v1/subscriptions", json={"topic": "Topic One"}).json()
    second = client.post("/api/v1/subscriptions", json={"topic": "Topic Two"}).json()
    with sessions() as db:
        a, b = db.get(Subscription, first["id"]), db.get(Subscription, second["id"])
        a.refresh_status = b.refresh_status = "completed"
        a.next_refresh_at = now() - timedelta(minutes=1)
        b.next_refresh_at = now() + timedelta(days=3)
        db.commit()
    worker.schedule_due_subscriptions.run()
    assert len(queued) == 3 and queued[-1][0] == first["id"]
    assert (
        client.patch(f"/api/v1/subscriptions/{second['id']}", json={"active": False}).status_code
        == 200
    )
    assert client.post(f"/api/v1/subscriptions/{second['id']}/refresh").json()["queued"] is True
    assert client.post(f"/api/v1/subscriptions/{second['id']}/refresh").json()["queued"] is False
    assert len(queued) == 4
    with sessions() as db:
        b = db.get(Subscription, second["id"])
        b.refresh_status = "completed"
        b.next_refresh_at = now() - timedelta(minutes=1)
        db.commit()
    worker.schedule_due_subscriptions.run()
    assert len(queued) == 4
    client.patch(f"/api/v1/subscriptions/{second['id']}", json={"active": True})
    worker.schedule_due_subscriptions.run()
    assert len(queued) == 5 and queued[-1][0] == second["id"]


def test_paused_queued_auto_task_can_be_replaced_by_manual(tracking):
    client, sessions, queued = tracking
    sub_id = client.post("/api/v1/subscriptions", json={"topic": "Queued Topic"}).json()["id"]
    client.patch(f"/api/v1/subscriptions/{sub_id}", json={"active": False})
    assert client.post(f"/api/v1/subscriptions/{sub_id}/refresh").json()["queued"] is True
    assert len(queued) == 2 and queued[0][1] != queued[1][1]
    worker.refresh_subscriptions.run(*queued[0])
    with sessions() as db:
        assert db.get(Subscription, sub_id).refresh_status == "queued"


def test_delete_preserves_shared_and_imported_records(tracking):
    client, sessions, queued = tracking
    first = client.post("/api/v1/subscriptions", json={"topic": "Topic One"}).json()
    client.post("/api/v1/subscriptions", json={"topic": "Topic Two"})
    with sessions() as db:
        candidate = PaperCandidate(title_key="shared", title="Shared", source="arXiv")
        exclusive = PaperCandidate(title_key="exclusive", title="Exclusive", source="arXiv")
        imported = PaperCandidate(title_key="imported", title="Imported", source="arXiv")
        db.add_all([candidate, exclusive, imported])
        db.flush()
        paper = Paper(owner_id="owner", title="Imported")
        db.add(paper)
        db.flush()
        db.add(
            Recommendation(
                owner_id="owner",
                candidate_id=candidate.id,
                score=50,
                reasons={},
                topics=["Topic One", "Topic Two"],
                matched_keywords={"Topic One": ["one"], "Topic Two": ["two"]},
                status="pending",
            )
        )
        db.add(
            Recommendation(
                owner_id="owner",
                candidate_id=exclusive.id,
                score=30,
                reasons={},
                topics=["Topic One"],
                matched_keywords={},
                status="pending",
            )
        )
        db.add(
            Recommendation(
                owner_id="owner",
                candidate_id=imported.id,
                paper_id=paper.id,
                score=50,
                reasons={},
                topics=["Topic One"],
                matched_keywords={},
                status="imported",
            )
        )
        db.commit()
    app.dependency_overrides[current_user] = lambda: "intruder"
    assert client.delete(f"/api/v1/subscriptions/{first['id']}").status_code == 404
    app.dependency_overrides[current_user] = lambda: "owner"
    assert client.delete(f"/api/v1/subscriptions/{first['id']}").status_code == 200
    with sessions() as db:
        recs = db.scalars(select(Recommendation)).all()
        assert len(recs) == 2
        shared = next(r for r in recs if r.status == "pending")
        assert shared.topics == ["Topic Two"] and "Topic One" not in shared.matched_keywords
        assert db.get(Paper, paper.id) is not None
        assert db.get(PaperCandidate, candidate.id) is not None
    assert client.get(f"/api/v1/subscriptions/{first['id']}/refresh-status").status_code == 404
    # An old queued task cannot recreate a deleted topic.
    worker.refresh_subscriptions.run(*queued[0])
    with sessions() as db:
        assert db.scalar(select(func.count()).select_from(Recommendation)) == 2


def test_search_and_llm_failure_are_not_success(tracking, monkeypatch):
    client, sessions, queued = tracking
    sub_id = client.post("/api/v1/subscriptions", json={"topic": "Failing Topic"}).json()["id"]
    monkeypatch.setattr(
        worker, "search_external", lambda *args: (_ for _ in ()).throw(RuntimeError("down"))
    )
    monkeypatch.setattr(
        worker, "search_arxiv", lambda *args: (_ for _ in ()).throw(RuntimeError("down"))
    )
    worker.refresh_subscriptions.run(*queued[0])
    with sessions() as db:
        sub = db.get(Subscription, sub_id)
        assert sub.refresh_status == "failed" and sub.last_checked_at is None
        assert sub.refresh_error and sub.next_refresh_at.replace(tzinfo=None) < (
            now() + timedelta(hours=2)
        ).replace(tzinfo=None)


def test_available_source_results_complete_despite_other_source_error(tracking, monkeypatch):
    client, sessions, queued = tracking
    sub_id = client.post("/api/v1/subscriptions", json={"topic": "Partial Topic"}).json()["id"]
    monkeypatch.setattr(
        worker, "search_external", lambda *args: (_ for _ in ()).throw(RuntimeError("429"))
    )
    monkeypatch.setattr(worker, "search_arxiv", lambda *args: [work("Found on arXiv")])
    monkeypatch.setattr(worker, "enrich_candidate", lambda candidate: False)
    monkeypatch.setattr(
        worker,
        "score_analyses",
        lambda topic, candidates: {
            candidate.id: {
                "relevance_score": 80,
                "value_score": 70,
                "summary": "概述",
                "reason": "相关",
            }
            for candidate in candidates
        },
    )
    worker.refresh_subscriptions.run(*queued[0])
    with sessions() as db:
        sub = db.get(Subscription, sub_id)
        assert sub.refresh_status == "completed" and sub.refresh_error is None
        assert db.scalar(select(func.count()).select_from(Recommendation)) == 1
        assert sub.next_refresh_at - sub.last_checked_at == timedelta(hours=1)


def test_manual_refresh_runs_while_paused_without_rescheduling(tracking, monkeypatch):
    client, sessions, queued = tracking
    sub_id = client.post("/api/v1/subscriptions", json={"topic": "Manual Topic"}).json()["id"]
    with sessions() as db:
        sub = db.get(Subscription, sub_id)
        sub.refresh_status = "completed"
        sub.next_refresh_at = now() + timedelta(days=4)
        next_auto = sub.next_refresh_at
        db.commit()
    client.patch(f"/api/v1/subscriptions/{sub_id}", json={"active": False})
    monkeypatch.setattr(worker, "search_external", lambda *a: [work("Manual Result")])
    monkeypatch.setattr(worker, "search_arxiv", lambda *a: [])
    monkeypatch.setattr(worker, "enrich_candidate", lambda candidate: False)
    monkeypatch.setattr(
        worker,
        "score_analyses",
        lambda topic, candidates: {
            candidate.id: {
                "relevance_score": 75,
                "value_score": 65,
                "summary": "有效",
                "reason": "匹配主题",
            }
            for candidate in candidates
        },
    )
    assert client.post(f"/api/v1/subscriptions/{sub_id}/refresh").json()["queued"] is True
    worker.refresh_subscriptions.run(*queued[-1])
    with sessions() as db:
        sub = db.get(Subscription, sub_id)
        assert sub.refresh_status == "completed" and sub.last_checked_at is not None
        assert sub.next_refresh_at.replace(tzinfo=None) == next_auto.replace(tzinfo=None)
        assert db.scalar(select(func.count()).select_from(Recommendation)) == 1


def test_successful_manual_refresh_clears_failed_auto_retry(tracking, monkeypatch):
    client, sessions, queued = tracking
    sub_id = client.post("/api/v1/subscriptions", json={"topic": "Retry Topic"}).json()["id"]
    with sessions() as db:
        sub = db.get(Subscription, sub_id)
        sub.refresh_status = "failed"
        sub.next_refresh_at = now() + timedelta(minutes=30)
        db.commit()
    monkeypatch.setattr(worker, "search_external", lambda *args: [work("Found Result")])
    monkeypatch.setattr(worker, "search_arxiv", lambda *args: [])
    monkeypatch.setattr(worker, "enrich_candidate", lambda candidate: False)
    monkeypatch.setattr(
        worker,
        "score_analyses",
        lambda topic, candidates: {
            candidate.id: {
                "relevance_score": 80,
                "value_score": 70,
                "summary": "概述",
                "reason": "相关",
            }
            for candidate in candidates
        },
    )
    assert client.post(f"/api/v1/subscriptions/{sub_id}/refresh").json()["queued"]
    worker.refresh_subscriptions.run(*queued[-1])
    with sessions() as db:
        sub = db.get(Subscription, sub_id)
        assert sub.refresh_status == "completed"
        assert sub.next_refresh_at - sub.last_checked_at == timedelta(days=7)


def test_llm_failure_keeps_score_unknown(tracking, monkeypatch):
    client, sessions, queued = tracking
    sub_id = client.post("/api/v1/subscriptions", json={"topic": "Unscored Topic"}).json()["id"]
    monkeypatch.setattr(worker, "search_external", lambda *a: [work("Unscored Result")])
    monkeypatch.setattr(worker, "search_arxiv", lambda *a: [])
    monkeypatch.setattr(worker, "enrich_candidate", lambda candidate: False)
    monkeypatch.setattr(worker, "score_analyses", lambda topic, candidates: {})
    worker.refresh_subscriptions.run(*queued[0])
    with sessions() as db:
        assert db.get(Subscription, sub_id).refresh_status == "failed"
        assert db.scalar(select(Recommendation)).score is None


def test_topic_filter_never_uses_another_topics_score(tracking):
    from services.api.app.recommender import upsert_recommendation

    client, sessions, _queued = tracking
    client.post("/api/v1/subscriptions", json={"topic": "Topic A"})
    client.post("/api/v1/subscriptions", json={"topic": "Topic B"})
    with sessions() as db:
        candidate = PaperCandidate(
            title_key="topic-specific", title="Topic Specific", source="arXiv"
        )
        db.add(candidate)
        db.flush()
        upsert_recommendation(
            db,
            "owner",
            "Topic A",
            candidate,
            {
                "relevance_score": 95,
                "value_score": 80,
                "summary": "A summary",
                "reason": "A reason",
            },
            ["Topic A"],
        )
        upsert_recommendation(db, "owner", "Topic B", candidate, None, ["Topic B"])
        db.commit()
    unscored = client.get("/api/v1/recommendations?topic=Topic%20B").json()["items"][0]
    assert unscored["score"] is None and unscored["summary"] == ""
    with sessions() as db:
        candidate = db.scalar(
            select(PaperCandidate).where(PaperCandidate.title_key == "topic-specific")
        )
        upsert_recommendation(
            db,
            "owner",
            "Topic B",
            candidate,
            {
                "relevance_score": 20,
                "value_score": 30,
                "summary": "B summary",
                "reason": "B reason",
            },
            ["Topic B"],
        )
        db.commit()
    scored = client.get("/api/v1/recommendations?topic=Topic%20B").json()["items"][0]
    assert scored["summary"] == "B summary"
    assert (
        scored["score"]
        < client.get("/api/v1/recommendations?topic=Topic%20A").json()["items"][0]["score"]
    )
