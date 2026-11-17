from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from services.api.app import main, worker
from services.api.app.api import copilot
from services.api.app.auth import make_token
from services.api.app.db import Base, get_db
from services.api.app.models import Artifact, BackgroundTask


def test_draft_job_survives_request_and_is_visible_in_activity(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    sessions = sessionmaker(engine)

    def session():
        with sessions() as db:
            yield db

    main.app.dependency_overrides[get_db] = session
    monkeypatch.setattr(worker, "SessionLocal", sessions)
    queued = []
    monkeypatch.setattr(
        worker.run_copilot_draft, "apply_async", lambda *, args, queue: queued.append((args, queue))
    )

    def build(db, owner, payload, progress):
        assert owner and payload["idea"] == "A grounded research direction"
        progress("searching", 0.15)
        progress("writing", 0.55)
        artifact = Artifact(owner_id=owner, type="draft", file_key="test.md", metadata_json={})
        db.add(artifact)
        db.commit()
        return {"artifact_id": artifact.id}

    monkeypatch.setattr(worker, "build_draft", build)
    monkeypatch.setattr(copilot, "load_draft", lambda artifact: {"artifact_id": artifact.id, "markdown": "## Abstract"})
    headers = {"Authorization": f"Bearer {make_token()}"}
    try:
        client = TestClient(main.app)
        payload = {"idea": "A grounded research direction"}
        first = client.post("/api/v1/copilot/draft/jobs", headers=headers, json=payload).json()
        duplicate = client.post("/api/v1/copilot/draft/jobs", headers=headers, json=payload).json()
        assert first["id"] == duplicate["id"] and queued == [([first["id"]], "default")]
        worker.run_copilot_draft(first["id"])
        detail = client.get(f"/api/v1/copilot/draft/jobs/{first['id']}", headers=headers).json()
        assert detail["status"] == "completed" and detail["draft"]["markdown"] == "## Abstract"
        assert client.get("/api/v1/copilot/draft/jobs", headers=headers).json()["items"][0]["id"] == first["id"]
        activity = client.get("/api/v1/activity", headers=headers).json()["items"]
        assert any(item["id"] == first["id"] and item["href"] == "/copilot" for item in activity)
        with Session(engine) as db:
            assert db.get(BackgroundTask, first["id"]).result["artifact_id"] == detail["artifact_id"]
    finally:
        main.app.dependency_overrides.clear()


def test_failed_draft_job_retains_error(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    sessions = sessionmaker(engine)
    monkeypatch.setattr(worker, "SessionLocal", sessions)
    with sessions() as db:
        task = BackgroundTask(owner_id="owner", type="copilot_draft", args={"idea": "research idea"})
        db.add(task)
        db.commit()
        task_id = task.id
    monkeypatch.setattr(worker, "build_draft", lambda *_args: (_ for _ in ()).throw(ValueError("source unavailable")))
    worker.run_copilot_draft(task_id)
    with sessions() as db:
        saved = db.get(BackgroundTask, task_id)
        assert saved.status == "failed" and saved.error == "source unavailable"
