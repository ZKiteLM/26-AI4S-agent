import json
import zipfile
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from services.api.app.charts import chart_data, inspect_csv, validate_intent
from services.api.app.db import Base, SessionLocal, engine
from services.api.app.graphs import build_graph
from services.api.app.main import app
from services.api.app.models import AgentRun, BackgroundTask, Block, Paper, Translation, Version
from services.api.app.parsing import (
    _merge_split_scientific_cells,
    _mineru_markdown,
    _pymupdf_blocks,
    identify_pdf,
    parse_pdf,
)
from services.api.app.translation import protect_math, restore_math, translate_text


def test_subscription_candidates_are_separate_from_library(monkeypatch):
    from sqlalchemy import func

    from services.api.app.models import PaperCandidate, Recommendation, Subscription
    from services.api.app.worker import refresh_subscriptions

    Base.metadata.create_all(engine)

    work = {
        "title": "A New Study of Scientific Agents",
        "authors": ["Researcher"],
        "abstract": "We study scientific agents with reproducible experiments.",
        "year": 2026,
        "published_at": "2026-10-15",
        "doi": None,
        "openalex_id": "https://openalex.org/W123456789",
        "arxiv_id": None,
        "pdf_url": None,
        "citation_count": 3,
        "source": "OpenAlex",
        "source_url": "https://openalex.org/W123456789",
    }
    monkeypatch.setattr("services.api.app.worker.search_external", lambda *_args: [work])
    monkeypatch.setattr(
        "services.api.app.worker.search_arxiv", lambda *_args: [{**work, "source": "arXiv"}]
    )
    monkeypatch.setattr("services.api.app.worker.enrich_candidate", lambda _candidate: None)
    model_calls = []

    def analyze(_system, prompt, **_kwargs):
        model_calls.append(1)
        return json.dumps(
            {
                "items": [
                    {
                        "id": paper["id"],
                        "relevance_score": 92,
                        "value_score": 78,
                        "summary": "研究科研智能体",
                        "reason": "与主题相关",
                    }
                    for paper in json.loads(prompt)["papers"]
                ]
            }
        )

    monkeypatch.setattr(
        "services.api.app.recommender.generate",
        analyze,
    )
    with SessionLocal() as db:
        before = db.scalar(select(func.count()).select_from(Paper))
        sub = Subscription(owner_id="recommendation-test", topic="scientific agents")
        db.add(sub)
        db.commit()
        sub_id = sub.id
    refresh_subscriptions.run(sub_id)
    with SessionLocal() as db:
        first = db.scalar(
            select(Recommendation).where(Recommendation.owner_id == "recommendation-test")
        )
        assert first.relevance_score == 92
        assert first.topics == ["scientific agents"]
    refresh_subscriptions.run(sub_id)
    assert len(model_calls) == 1
    with SessionLocal() as db:
        second_sub = Subscription(owner_id="recommendation-test", topic="agent evaluation")
        db.add(second_sub)
        db.commit()
        second_sub_id = second_sub.id
    refresh_subscriptions.run(second_sub_id)
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(Paper)) == before
        assert db.scalar(select(func.count()).select_from(PaperCandidate)) == 1
        recs = db.scalars(
            select(Recommendation).where(Recommendation.owner_id == "recommendation-test")
        ).all()
        assert len(recs) == 1
        assert recs[0].score > 0
        assert recs[0].summary == "研究科研智能体"
        assert recs[0].paper_id is None
        assert recs[0].topics == ["scientific agents", "agent evaluation"]


def test_recommendation_feedback_and_explicit_import(monkeypatch):
    from services.api.app.models import PaperCandidate, Recommendation

    Base.metadata.create_all(engine)
    with TestClient(app) as client:
        login = client.post(
            "/api/v1/auth/login", json={"email": "demo@example.com", "password": "change-me"}
        )
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
        with SessionLocal() as db:
            candidate = PaperCandidate(
                title_key="recommendationimportfixture",
                title="Recommendation Import Fixture",
                authors=["A"],
                abstract="A paper about agents.",
                source="arXiv",
                source_url="https://arxiv.org/abs/2601.12345",
                arxiv_id="2601.12345",
            )
            db.add(candidate)
            db.flush()
            imported = Recommendation(
                owner_id="demo@example.com", candidate_id=candidate.id, score=85, topics=["agents"]
            )
            dismissed = PaperCandidate(
                title_key="dismissfixture",
                title="Dismiss Fixture",
                authors=["B"],
                abstract="Unrelated.",
                source="arXiv",
            )
            db.add(dismissed)
            db.flush()
            negative = Recommendation(
                owner_id="demo@example.com", candidate_id=dismissed.id, score=12, topics=["agents"]
            )
            db.add_all([imported, negative])
            db.commit()
            import_id, negative_id = imported.id, negative.id
        pending = client.get("/api/v1/recommendations", headers=headers).json()
        assert any(x["id"] == import_id for x in pending["items"])
        assert (
            client.post(
                f"/api/v1/recommendations/{negative_id}/feedback",
                json={"value": -1},
                headers=headers,
            ).status_code
            == 200
        )
        pending = client.get("/api/v1/recommendations", headers=headers).json()
        assert all(x["id"] != negative_id for x in pending["items"])
        monkeypatch.setattr("services.api.app.main.search_arxiv", lambda *_args: 1 / 0)
        response = client.post(f"/api/v1/recommendations/{import_id}/import", headers=headers)
        assert response.status_code == 200
        assert response.json()["status"] == "metadata_only"
        with SessionLocal() as db:
            rec = db.get(Recommendation, import_id)
            assert rec.status == "imported"
            assert db.get(Paper, rec.paper_id).title == "Recommendation Import Fixture"


def test_recommendation_missing_metrics_are_unknown(monkeypatch):
    from datetime import datetime

    from services.api.app.models import PaperCandidate
    from services.api.app.recommender import score_analyses, weighted_score

    candidate = PaperCandidate(
        id="candidate-test",
        title_key="candidate-test",
        title="Fixture",
        authors=[],
        abstract="An abstract",
        source="arXiv",
        published_at=datetime(2026, 10, 1),
    )
    monkeypatch.setattr("services.api.app.recommender.generate", lambda *_args, **_kwargs: 1 / 0)
    assert score_analyses("topic", [candidate]) == {}
    score, factors = weighted_score(candidate, None)
    assert 0 <= score <= 100
    assert factors["citations"] is None
    assert factors["github_stars"] is None
    assert factors["relevance"] is None
    _, scored = weighted_score(candidate, {"relevance_score": 80, "value_score": 70})
    assert scored["effective_weights"] == {
        "relevance": 60.0,
        "value": 33.3,
        "recency": 6.7,
    }


def test_subscription_retries_partial_source_and_unscored_candidate(monkeypatch):
    from services.api.app.models import Recommendation, Subscription
    from services.api.app.worker import refresh_subscriptions

    Base.metadata.create_all(engine)
    work = {
        "title": "Retry Candidate Fixture",
        "authors": ["A"],
        "abstract": "We evaluate a new method.",
        "year": 2026,
        "published_at": "2026-10-01",
        "doi": None,
        "arxiv_id": "2601.23456",
        "openalex_id": None,
        "pdf_url": None,
        "citation_count": None,
        "source": "arXiv",
        "source_url": "https://arxiv.org/abs/2601.23456",
    }
    monkeypatch.setattr("services.api.app.worker.search_external", lambda *_args: [work])
    monkeypatch.setattr("services.api.app.worker.search_arxiv", lambda *_args: 1 / 0)
    monkeypatch.setattr("services.api.app.worker.enrich_candidate", lambda _candidate: None)
    monkeypatch.setattr("services.api.app.worker.score_analyses", lambda *_args: {})
    with SessionLocal() as db:
        sub = Subscription(owner_id="retry-test", topic="new method")
        db.add(sub)
        db.commit()
        sub_id = sub.id
    refresh_subscriptions.run(sub_id)
    with SessionLocal() as db:
        assert db.get(Subscription, sub_id).last_checked_at is None
        rec = db.scalar(select(Recommendation).where(Recommendation.owner_id == "retry-test"))
        assert rec.relevance_score is None
        assert rec.score is None
    monkeypatch.setattr("services.api.app.worker.search_external", lambda *_args: [])
    monkeypatch.setattr("services.api.app.worker.search_arxiv", lambda *_args: [])
    monkeypatch.setattr(
        "services.api.app.worker.score_analyses",
        lambda _topic, candidates: {
            candidate.id: {
                "relevance_score": 75,
                "value_score": 66,
                "summary": "研究新方法",
                "reason": "与主题相关",
            }
            for candidate in candidates
        },
    )
    refresh_subscriptions.run(sub_id)
    with SessionLocal() as db:
        assert db.get(Subscription, sub_id).last_checked_at is not None
        rec = db.scalar(select(Recommendation).where(Recommendation.owner_id == "retry-test"))
        assert rec.relevance_score == 75


def test_subscription_can_pause_resume_and_refresh(monkeypatch):
    from services.api.app.models import Subscription

    queued = []
    monkeypatch.setattr(
        "services.api.app.worker.refresh_subscriptions.delay", lambda *args: queued.append(args)
    )
    with TestClient(app) as client:
        login = client.post(
            "/api/v1/auth/login", json={"email": "demo@example.com", "password": "change-me"}
        )
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
        first = client.post(
            "/api/v1/subscriptions", json={"topic": "Fixture Topic"}, headers=headers
        )
        assert first.status_code == 200
        sub_id = first.json()["id"]
        duplicate = client.post(
            "/api/v1/subscriptions", json={"topic": "fixture topic"}, headers=headers
        )
        assert duplicate.json()["id"] == sub_id
        paused = client.patch(
            f"/api/v1/subscriptions/{sub_id}", json={"active": False}, headers=headers
        )
        assert paused.json()["active"] is False
        # Pausing a queued automatic task permits a manual refresh to replace it.
        repeated = client.post(f"/api/v1/subscriptions/{sub_id}/refresh", headers=headers)
        assert repeated.status_code == 200, repeated.text
        assert repeated.json()["queued"] is True
        assert (
            client.post(f"/api/v1/subscriptions/{sub_id}/refresh", headers=headers).json()["queued"]
            is False
        )
        with SessionLocal() as db:
            sub = db.get(Subscription, sub_id)
            sub.refresh_status = "completed"
            db.commit()
        assert (
            client.post(f"/api/v1/subscriptions/{sub_id}/refresh", headers=headers).json()["queued"]
            is True
        )
        resumed = client.patch(
            f"/api/v1/subscriptions/{sub_id}", json={"active": True}, headers=headers
        )
        assert resumed.json()["active"] is True
        assert len(queued) == 3
        assert all(args[0] == sub_id for args in queued)


ROOT = Path(__file__).resolve().parents[3]
SAMPLES = ROOT / "科研小助手" / "pdf"


def test_official_transformer_pdf_has_structured_blocks():
    data = (SAMPLES / "1706.03762v1.pdf").read_bytes()
    info = identify_pdf(data, "1706.03762v1.pdf")
    assert info["arxiv_id"] == "1706.03762"
    assert info["label"] == "v1"
    assert info["page_count"] > 10
    blocks = parse_pdf(data)
    assert len(blocks) > 50
    assert all(0 <= x["bbox"][0] <= 1 for x in blocks)
    assert any("attention" in x["text"].lower() for x in blocks)
    outline = [x for x in blocks if x["type"] in ("title", "heading")]
    assert sum(x["type"] == "title" for x in outline) == 1
    assert any(x["text"] == "5.3 Optimizer" for x in outline)
    assert not any("avaswani@" in x["text"] for x in outline)


def test_permission_notice_is_not_the_paper_title():
    import pymupdf

    doc = pymupdf.open(SAMPLES / "1706.03762v7.pdf")
    blocks = _pymupdf_blocks(doc[0], 0, 0)
    titles = [block["text"] for block in blocks if block["type"] == "title"]
    assert titles == ["Attention Is All You Need"]


def test_two_column_page_keeps_each_column_in_reading_order():
    import pymupdf

    document = pymupdf.open()
    page = document.new_page(width=600, height=800)
    page.insert_text((50, 80), "Synthetic Paper", fontsize=18)
    for index in range(3):
        page.insert_text((50, 150 + index * 90), f"Left paragraph {index}")
        page.insert_text((350, 155 + index * 90), f"Right paragraph {index}")
    blocks = _pymupdf_blocks(page, 0, 0)
    text = [block["text"] for block in blocks]
    assert text.index("Left paragraph 2") < text.index("Right paragraph 0")


def test_inline_math_is_restored_without_model_changes():
    source = r"We compare $x^2$ and \[a+b=c\] in this equation."
    masked, formulas = protect_math(source)
    assert "$x^2$" not in masked
    assert restore_math("我们比较 " + masked, formulas).endswith(source)
    with pytest.raises(ValueError):
        restore_math("公式被遗漏", formulas)


def test_mineru_inline_formula_becomes_markdown_math():
    assert (
        _mineru_markdown(
            [
                {"type": "text", "content": "where"},
                {"type": "inline_equation", "content": {"latex": r"x^2 + y^2"}},
                {"type": "text", "content": "is constant"},
            ]
        )
        == "where $x^2 + y^2$ is constant"
    )
    assert _mineru_markdown(r"Use $\\sqrt{x}$ here") == r"Use $\sqrt{x}$ here"


def test_mineru_table_keeps_scientific_notation_in_one_cell():
    source = (
        "<tr><td>Method A</td><td>1.2 ×</td><td>10<sup>7</sup></td></tr>"
        "<tr><td>Method B</td><td>3.3 ·</td><td>10<sup>18</sup></td></tr>"
    )
    assert _merge_split_scientific_cells(source) == (
        '<tr><td>Method A</td><td colspan="2">1.2 × 10<sup>7</sup></td></tr>'
        '<tr><td>Method B</td><td colspan="2">3.3 · 10<sup>18</sup></td></tr>'
    )


def test_selection_translation_preserves_formula(monkeypatch):
    monkeypatch.setattr(
        "services.api.app.translation.generate",
        lambda _instruction, prompt: prompt.split("\n", 1)[1].replace("where", "其中"),
    )
    assert translate_text("where $x^2$ is fixed", "zh") == "其中 $x^2$ is fixed"


def test_reader_loads_one_page_and_its_cached_translations():
    with TestClient(app) as client:
        login = client.post(
            "/api/v1/auth/login", json={"email": "demo@example.com", "password": "change-me"}
        )
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
        with SessionLocal() as db:
            paper = Paper(owner_id="demo@example.com", title="Reader fixture")
            db.add(paper)
            db.flush()
            version = Version(
                paper_id=paper.id, filename="fixture.pdf", file_key="fixture.pdf", sha256="fixture"
            )
            db.add(version)
            db.flush()
            first = Block(version_id=version.id, page_index=0, order=0, text="first")
            second = Block(version_id=version.id, page_index=1, order=1, text="second")
            db.add_all([first, second])
            db.flush()
            db.add(Translation(block_id=second.id, target_lang="zh", text="第二段"))
            db.commit()
            version_id, second_id = version.id, second.id

        blocks = client.get(
            f"/api/v1/versions/{version_id}/blocks?page_start=1&page_end=1", headers=headers
        )
        assert blocks.status_code == 200
        assert [item["id"] for item in blocks.json()["blocks"]] == [second_id]
        cached = client.get(
            f"/api/v1/versions/{version_id}/translations?page_index=1", headers=headers
        )
        assert cached.json()["translations"] == [
            {"block_id": second_id, "page_index": 1, "text": "第二段"}
        ]
        assert (
            client.get(
                f"/api/v1/versions/{version_id}/blocks?page_start=2&page_end=1",
                headers=headers,
            ).status_code
            == 400
        )


def test_reparse_keeps_unchanged_translation_and_previous_blocks_on_failure(monkeypatch):
    from services.api.app import worker

    with SessionLocal() as db:
        paper = Paper(owner_id="demo@example.com", title="Reparse fixture")
        db.add(paper)
        db.flush()
        version = Version(
            paper_id=paper.id,
            filename="fixture.pdf",
            file_key="fixture.pdf",
            sha256="reparse-fixture",
            parse_status="completed",
        )
        db.add(version)
        db.flush()
        old = Block(
            version_id=version.id,
            page_index=0,
            order=0,
            text="Same paragraph",
            markdown="Same paragraph",
        )
        db.add(old)
        db.flush()
        db.add(Translation(block_id=old.id, target_lang="zh", text="相同段落"))
        task = BackgroundTask(owner_id="demo@example.com", type="parse_pdf")
        db.add(task)
        db.commit()
        version_id, task_id, old_id = version.id, task.id, old.id

    monkeypatch.setattr(worker.Storage, "get", lambda _self, _key: b"%PDF")
    monkeypatch.setattr(worker, "remove_version_index", lambda _version_id: None)
    monkeypatch.setattr(worker, "make_chunks", lambda _db, _version_id: [])
    monkeypatch.setattr(worker, "index_chunks", lambda *_args: None)
    parsed = [
        {
            "page_index": 0,
            "order": 0,
            "type": "paragraph",
            "text": "Same paragraph",
            "markdown": "Same paragraph",
            "bbox": [0.1, 0.1, 0.9, 0.2],
        }
    ]
    monkeypatch.setattr(worker, "parse_pdf", lambda _data, _progress: parsed)
    worker.parse_version.run(task_id, version_id)
    with SessionLocal() as db:
        replacement = db.scalars(select(Block).where(Block.version_id == version_id)).one()
        assert replacement.id != old_id
        assert (
            db.scalars(select(Translation).where(Translation.block_id == replacement.id)).one().text
            == "相同段落"
        )
        failed_task = BackgroundTask(owner_id="demo@example.com", type="parse_pdf")
        db.add(failed_task)
        db.commit()
        failed_task_id = failed_task.id
        replacement_id = replacement.id

    monkeypatch.setattr(
        worker,
        "parse_pdf",
        lambda _data, _progress: (_ for _ in ()).throw(RuntimeError("parser failed")),
    )
    worker.parse_version.run(failed_task_id, version_id)
    with SessionLocal() as db:
        assert db.get(Version, version_id).parse_status == "completed"
        assert db.get(Block, replacement_id) is not None
        assert db.get(BackgroundTask, failed_task_id).status == "failed"


def test_version_conflict_requires_user_resolution(monkeypatch):
    from services.api.app.config import settings

    # This test may run against a persistent development database. Isolate its owner.
    email = f"version-conflict-{uuid4().hex}@example.com"
    monkeypatch.setattr(settings(), "demo_email", email)
    monkeypatch.setattr("services.api.app.main.parse_version.apply_async", lambda **kwargs: None)
    with TestClient(app) as client:
        login = client.post("/api/v1/auth/login", json={"email": email, "password": "change-me"})
        token = login.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}
        assert client.get("/api/v1/papers").status_code == 401
        v7 = (SAMPLES / "1706.03762v7.pdf").read_bytes()
        v1 = (SAMPLES / "1706.03762v1.pdf").read_bytes()
        first = client.post(
            "/api/v1/papers/upload",
            headers=headers,
            files={"files": ("1706.03762v7.pdf", v7, "application/pdf")},
        )
        assert first.status_code == 200, first.text
        second = client.post(
            "/api/v1/papers/upload",
            headers=headers,
            files={"files": ("1706.03762v1.pdf", v1, "application/pdf")},
        )
        assert second.status_code == 200, second.text
        conflict = second.json()["conflicts"][0]
        assert conflict["recommendation"] == "keep_existing"
        assert second.json()["paper_ids"][0] == first.json()["paper_ids"][0]
        result = client.post(
            f"/api/v1/versions/conflicts/{conflict['id']}/resolve",
            headers=headers,
            json={"resolution": "keep"},
        )
        assert result.status_code == 200, result.text
        assert result.json()["preferred_version_id"] == first.json()["version_ids"][0]


def test_debug_requires_auth_and_completed_version_can_be_reparsed(monkeypatch):
    monkeypatch.setattr("services.api.app.main.parse_version.apply_async", lambda **kwargs: None)
    with TestClient(app) as client:
        assert client.get("/api/v1/debug/overview").status_code == 401
        login = client.post(
            "/api/v1/auth/login", json={"email": "demo@example.com", "password": "change-me"}
        )
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
        overview = client.get("/api/v1/debug/overview", headers=headers)
        assert overview.status_code == 200
        assert "database" in overview.json()
        with monkeypatch.context() as patch:
            patch.setattr(
                "services.api.app.main.settings",
                lambda: SimpleNamespace(deployment_mode="cloud", debug_console_key="test-key"),
            )
            assert client.get("/api/v1/debug/overview", headers=headers).status_code == 403
            assert (
                client.get(
                    "/api/v1/debug/overview", headers={**headers, "X-Debug-Key": "test-key"}
                ).status_code
                == 200
            )
        paper = client.post(
            "/api/v1/papers/upload",
            headers=headers,
            files={
                "files": (
                    "1706.03762v1.pdf",
                    (SAMPLES / "1706.03762v1.pdf").read_bytes(),
                    "application/pdf",
                )
            },
        ).json()
        version_id = paper["version_ids"][0]
        assert (
            client.post(f"/api/v1/versions/{version_id}/reparse", headers=headers).status_code
            == 409
        )
        with SessionLocal() as db:
            version = db.get(Version, version_id)
            version.parse_status = "completed"
            db.commit()
        assert (
            client.post(f"/api/v1/versions/{version_id}/reparse", headers=headers).status_code
            == 200
        )


def test_chart_caption_is_computed_from_csv():
    frame, overview = inspect_csv(b"epoch,score\n1,0.3\n2,0.5\n3,0.7\n")
    rows, caption, spec = chart_data(
        frame, validate_intent({"type": "line", "x": "epoch", "y": "score"}, frame)
    )
    assert "0.3 to 0.7" in caption
    assert len(rows) == overview["row_count"] == 3
    assert spec["y"] == "score"


def test_citation_graph_keeps_citing_to_cited_direction(monkeypatch):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        newer = Paper(owner_id="owner", title="Newer work", openalex_id="https://openalex.org/W1")
        older = Paper(owner_id="owner", title="Older work", openalex_id="https://openalex.org/W2")
        db.add_all([newer, older])
        db.commit()
        newer_id, older_id = newer.id, older.id
        monkeypatch.setattr(
            "services.api.app.graphs._references",
            lambda key: ["https://openalex.org/W2"] if key == "https://openalex.org/W1" else [],
        )
        result = build_graph(db, "owner", [newer_id, older_id])
        assert result["edges"] == [
            {"source": newer_id, "target": older_id, "provenance": "OpenAlex"}
        ]
        assert next(x for x in result["nodes"] if x["id"] == older_id)["in_degree"] == 1


def test_conversation_persists_and_can_be_deleted(monkeypatch):
    monkeypatch.setattr("services.api.app.main.run_agent.delay", lambda _run_id: None)
    with TestClient(app) as client:
        login = client.post(
            "/api/v1/auth/login", json={"email": "demo@example.com", "password": "change-me"}
        )
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
        with SessionLocal() as db:
            paper = Paper(owner_id="demo@example.com", title="Conversation fixture")
            db.add(paper)
            db.commit()
            paper_id = paper.id
        created = client.post(
            "/api/v1/chat/sessions", headers=headers, json={"paper_ids": [paper_id]}
        )
        assert created.status_code == 200
        session_id = created.json()["id"]
        sent = client.post(
            f"/api/v1/chat/sessions/{session_id}/messages",
            headers=headers,
            json={"content": "What is the method?"},
        )
        assert sent.status_code == 200
        assert (
            client.post(
                f"/api/v1/chat/sessions/{session_id}/messages",
                headers=headers,
                json={"content": "Another question"},
            ).status_code
            == 409
        )
        with SessionLocal() as db:
            run = db.get(AgentRun, sent.json()["run_id"])
            run.status = "completed"
            db.commit()
        detail = client.get(f"/api/v1/chat/sessions/{session_id}", headers=headers).json()
        assert detail["messages"][0]["content"] == "What is the method?"
        assert detail["messages"][0]["status"] == "completed"
        assert any(
            item["id"] == session_id
            for item in client.get("/api/v1/chat/sessions", headers=headers).json()["sessions"]
        )
        assert client.delete(f"/api/v1/chat/sessions/{session_id}", headers=headers).json() == {
            "deleted": True
        }
        assert client.get(f"/api/v1/chat/sessions/{session_id}", headers=headers).status_code == 404


def test_remote_parser_adapter_preserves_equation_and_location(monkeypatch):
    from services.api.app import parsing

    archive = BytesIO()
    content = {
        "pages": [
            {
                "page_idx": 0,
                "blocks": [
                    {
                        "type": "interline_equation",
                        "content": {"latex": "E=mc^2"},
                        "bbox": [100, 200, 600, 300],
                    },
                    {
                        "type": "table",
                        "content": "<table><tr><td>28.4</td></tr></table>",
                        "bbox": [100, 400, 600, 600],
                        "captions": [
                            {"content": "Table 2: BLEU scores", "bbox": [100, 350, 600, 390]}
                        ],
                    },
                ],
            }
        ]
    }
    with zipfile.ZipFile(archive, "w") as zipped:
        zipped.writestr("output/structured_content.json", json.dumps(content))
    monkeypatch.setattr(
        parsing,
        "settings",
        lambda: type(
            "Config",
            (),
            {
                "parser_backend": "remote",
                "mineru_api_url": "https://parser.example",
                "mineru_api_key": "secret",
            },
        )(),
    )

    class Response:
        status_code = 200
        content = archive.getvalue()

        def raise_for_status(self):
            pass

    monkeypatch.setattr(parsing.httpx, "post", lambda *args, **kwargs: Response())
    blocks = parsing._mineru_blocks(b"%PDF", 0, 1, "auto")
    assert blocks[0]["latex"] == "E=mc^2"
    assert blocks[0]["markdown"] == "$$\nE=mc^2\n$$"
    assert blocks[0]["bbox"] == [0.1, 0.2, 0.6, 0.3]
    assert [(block["type"], block["text"]) for block in blocks[1:]] == [
        ("caption", "Table 2: BLEU scores"),
        ("table", "<table><tr><td>28.4</td></tr></table>"),
    ]
    assert blocks[1]["bbox"] == [0.1, 0.35, 0.6, 0.39]


def test_local_llm_adapter_is_configured_without_cloud_dependency(monkeypatch):
    from services.api.app import ai

    monkeypatch.setattr(
        ai,
        "settings",
        lambda: type(
            "Config",
            (),
            {
                "llm_base_url": "http://localhost:11434/v1",
                "llm_model": "local-model",
                "llm_provider": "ollama",
                "ollama_num_ctx": 8192,
            },
        )(),
    )
    calls = []

    class Response:
        status_code = 200
        text = ""

        def raise_for_status(self):
            pass

        def json(self):
            return {"message": {"content": "answer"}}

    def post(url, **kwargs):
        calls.append((url, kwargs))
        return Response()

    monkeypatch.setattr(ai.httpx, "post", post)
    assert ai.generate("system", "question", max_tokens=100) == "answer"
    assert calls[0][1]["json"]["options"]["num_ctx"] == 8192
    assert calls[0][0] == "http://localhost:11434/api/chat"
    assert calls[0][1]["json"]["think"] is False


def test_cloud_mode_rejects_local_model_endpoints():
    from services.api.app.config import Settings

    with pytest.raises(ValidationError, match="Cloud mode requires"):
        Settings(deployment_mode="cloud", llm_provider="ollama", parser_backend="local")
