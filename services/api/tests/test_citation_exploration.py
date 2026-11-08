from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from services.api.app import citation_graph_service as service
from services.api.app.citation_identity import normalize_identifier, upsert_work
from services.api.app.citation_providers import CitationPage
from services.api.app.db import Base, SessionLocal
from services.api.app.main import app
from services.api.app.models import (
    CitationFetchState,
    GraphView,
    GraphViewNode,
    Paper,
    ScholarlyCitationEdge,
    ScholarlyWork,
)


def database():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    return Session(engine)


def test_identifiers_are_normalized_and_conflicting_dois_do_not_merge():
    assert normalize_identifier("doi", "https://doi.org/10.1000/ABC") == "10.1000/abc"
    assert normalize_identifier("arxiv", "https://arxiv.org/abs/1706.03762v5") == "1706.03762"
    assert normalize_identifier("openalex", "https://openalex.org/w123") == "W123"
    with database() as db:
        one = upsert_work(db, {"title": "Same title", "doi": "10.1000/ABC"}, "openalex")
        again = upsert_work(db, {"title": "Same title", "doi": "https://doi.org/10.1000/abc"}, "s2")
        other = upsert_work(db, {"title": "Same title", "doi": "10.1000/XYZ"}, "openalex")
        assert one.id == again.id
        assert one.id != other.id
        assert len(db.scalars(select(ScholarlyWork)).all()) == 2


def test_expansion_keeps_direction_and_uses_cached_page(monkeypatch):
    class Provider:
        name = "openalex"
        calls = 0

        def get_references(self, identifier, limit, cursor):
            self.calls += 1
            if identifier == "W1":
                return CitationPage([{"title": "Older", "openalex_id": "W2"}], None, 1, self.name)
            return CitationPage([], None, 0, self.name)

        def get_citations(self, identifier, limit, cursor):
            self.calls += 1
            return CitationPage([], None, 0, self.name)

    provider = Provider()
    monkeypatch.setattr(
        service, "provider_for", lambda work, direction: (provider, work.openalex_id)
    )
    with database() as db:
        paper = Paper(owner_id="owner", title="Newer", openalex_id="https://openalex.org/W1")
        db.add(paper)
        view = GraphView(owner_id="owner", config={})
        db.add(view)
        db.flush()
        seed = service.add_seed_paper(db, view, paper)
        db.commit()
        first = service.expand_view(db, view.id, seed.id, "references", 20)
        assert first["expansion"]["nodes_added"] == 1
        assert first["edges"] == [
            {
                "source": seed.id,
                "target": next(node["id"] for node in first["nodes"] if node["title"] == "Older"),
                "provenance": ["openalex"],
            }
        ]
        assert len(db.scalars(select(Paper)).all()) == 1
        assert provider.calls == 1
        second = service.expand_view(db, view.id, seed.id, "references", 20)
        assert second["expansion"]["nodes_added"] == 0
        assert provider.calls == 1
        assert len(db.scalars(select(GraphViewNode)).all()) == 2
        assert len(db.scalars(select(ScholarlyCitationEdge)).all()) == 1
        state = db.scalar(select(CitationFetchState))
        assert state.status == "complete" and state.fetched_count == 1


def test_cached_neighbors_are_added_in_bounded_batches(monkeypatch):
    class Provider:
        name = "openalex"
        calls = 0

        def get_references(self, identifier, limit, cursor):
            self.calls += 1
            return CitationPage([], None, 0, self.name)

    provider = Provider()
    monkeypatch.setattr(service, "provider_for", lambda *_: (provider, "W1"))
    with database() as db:
        paper = Paper(owner_id="owner", title="Seed", openalex_id="W1")
        db.add(paper)
        view = GraphView(owner_id="owner", config={})
        db.add(view)
        db.flush()
        seed = service.add_seed_paper(db, view, paper)
        for index in range(4):
            neighbor = upsert_work(
                db, {"title": str(index), "openalex_id": f"W{index + 2}"}, "openalex"
            )
            service._edge(db, seed.id, neighbor.id, "openalex")
        state = service._state(db, seed.id, "openalex", "references")
        state.status = "complete"
        from datetime import timedelta

        from services.api.app.models import now

        state.expires_at = now() + timedelta(days=1)
        db.commit()
        first = service.expand_view(db, view.id, seed.id, "references", 2)
        assert first["expansion"]["nodes_added"] == 2
        assert first["expansion"]["directions"]["references"]["has_more"]
        second = service.expand_view(db, view.id, seed.id, "references", 2)
        assert second["expansion"]["nodes_added"] == 2
        assert not second["expansion"]["directions"]["references"]["has_more"]
        assert provider.calls == 0


def test_graph_api_accepts_one_seed_and_checks_ownership(monkeypatch):
    monkeypatch.setattr("services.api.app.main.build_citation_graph.delay", lambda *_: None)
    with TestClient(app) as client:
        login = client.post(
            "/api/v1/auth/login",
            json={
                "email": "demo@example.com",
                "password": "change-me",
            },
        )
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
        with SessionLocal() as db:
            own = Paper(owner_id="demo@example.com", title="A seed", arxiv_id="1706.03762")
            other = Paper(owner_id="other", title="Private seed")
            db.add_all([own, other])
            db.commit()
            own_id, other_id = own.id, other.id
        denied = client.post("/api/v1/graphs", headers=headers, json={"paper_ids": [other_id]})
        assert denied.status_code == 404
        response = client.post("/api/v1/graphs", headers=headers, json={"paper_ids": [own_id]})
        assert response.status_code == 200
        graph_id = response.json()["graph_id"]
        assert response.json()["task_id"] != graph_id
        graph = client.get(f"/api/v1/graphs/{graph_id}", headers=headers).json()
        assert graph["status"] == "queued"
        assert len(graph["nodes"]) == 1
        assert graph["nodes"][0]["paper_id"] == own_id
        assert (
            client.get(f"/api/v1/graphs/{graph_id}/nodes/not-visible", headers=headers).status_code
            == 404
        )
