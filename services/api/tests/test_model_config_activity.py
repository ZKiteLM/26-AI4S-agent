from types import SimpleNamespace

import httpx
from fastapi.testclient import TestClient
from openai import AuthenticationError
from sqlalchemy import delete, select

from services.api.app import main, model_config, worker
from services.api.app.auth import make_token
from services.api.app.config import settings
from services.api.app.db import Base, SessionLocal, engine
from services.api.app.models import (
    AgentRun,
    BackgroundTask,
    Paper,
    RunEvent,
    UserModelConfig,
    Version,
)


def _headers():
    return {"Authorization": f"Bearer {make_token()}"}


def test_model_connection_is_tested_and_key_is_encrypted(monkeypatch):
    Base.metadata.create_all(engine)
    monkeypatch.setattr(
        model_config.socket,
        "getaddrinfo",
        lambda *_args: [(None, None, None, None, ("8.8.8.8", 443))],
    )
    calls = []

    def fake_generate(_system, _prompt, **kwargs):
        calls.append(kwargs["llm_config"])
        return "连接成功"

    monkeypatch.setattr(main, "generate", fake_generate)
    client = TestClient(main.app)
    data = {
        "provider": "openai",
        "base_url": "https://models.example/v1",
        "model": "sample-model",
        "api_key": "private-test-key",
    }
    assert (
        client.post("/api/v1/model-config/test", json=data, headers=_headers()).json()["reply"]
        == "连接成功"
    )
    assert calls[-1].llm_api_key == "private-test-key"
    assert client.put("/api/v1/model-config", json=data, headers=_headers()).status_code == 200
    with SessionLocal() as db:
        row = db.get(UserModelConfig, settings().demo_email)
        assert row.api_key_ciphertext != "private-test-key"
        assert "private-test-key" not in row.api_key_ciphertext
    response = client.get("/api/v1/model-config", headers=_headers()).json()
    assert response["has_api_key"] and response["model"] == "sample-model"
    assert "api_key" not in response
    switched = {**data, "base_url": "https://other.example/v1", "api_key": ""}
    assert client.put("/api/v1/model-config", json=switched, headers=_headers()).status_code == 422
    with model_config.model_for_user(settings().demo_email):
        assert model_config.effective_settings().llm_model == "sample-model"
    monkeypatch.setattr(
        main, "generate_keywords", lambda _topic: [model_config.effective_settings().llm_model]
    )
    response = client.post(
        "/api/v1/subscriptions/keywords", json={"topic": "research agents"}, headers=_headers()
    )
    assert response.json()["keywords"] == ["sample-model"]
    assert client.delete("/api/v1/model-config", headers=_headers()).status_code == 200


def test_model_endpoint_rejects_private_remote_address():
    Base.metadata.create_all(engine)
    response = TestClient(main.app).post(
        "/api/v1/model-config/test",
        json={
            "provider": "openai",
            "base_url": "http://169.254.169.254/latest",
            "model": "x",
            "api_key": "test",
        },
        headers=_headers(),
    )
    assert response.status_code == 422
    malformed = TestClient(main.app).post(
        "/api/v1/model-config/test",
        json={
            "provider": "openai",
            "base_url": "https://models.example:bad/v1",
            "model": "x",
            "api_key": "test",
        },
        headers=_headers(),
    )
    assert malformed.status_code == 422


def test_default_key_is_reused_for_same_endpoint_and_protocol_switch(monkeypatch):
    Base.metadata.create_all(engine)
    monkeypatch.setattr(
        model_config.socket,
        "getaddrinfo",
        lambda *_args: [(None, None, None, None, ("8.8.8.8", 443))],
    )
    monkeypatch.setattr(
        main,
        "settings",
        lambda: SimpleNamespace(
            llm_provider="ohmygpt",
            llm_base_url="https://api.ohmygpt.com/v1",
            llm_model="old-model",
            ohmygpt_api_key="default-test-key",
            llm_api_key="",
        ),
    )
    with SessionLocal() as db:
        for provider in ("ohmygpt", "openai"):
            base_url, key = main._model_candidate(
                main.ModelConfigIn(
                    provider=provider, base_url="https://api.ohmygpt.com/v1", model="new-model"
                ),
                settings().demo_email,
                db,
            )
            assert base_url == "https://api.ohmygpt.com/v1"
            assert key == "default-test-key"


def test_authentication_error_explains_which_key_failed(monkeypatch):
    Base.metadata.create_all(engine)
    monkeypatch.setattr(
        model_config.socket,
        "getaddrinfo",
        lambda *_args: [(None, None, None, None, ("8.8.8.8", 443))],
    )
    request = httpx.Request("POST", "https://models.example/v1/responses")
    error = AuthenticationError(
        "bad credentials", response=httpx.Response(401, request=request), body={}
    )
    monkeypatch.setattr(main, "generate", lambda *_args, **_kwargs: (_ for _ in ()).throw(error))
    response = TestClient(main.app).post(
        "/api/v1/model-config/test",
        json={
            "provider": "openai",
            "base_url": "https://models.example/v1",
            "model": "sample",
            "api_key": "wrong-key",
        },
        headers=_headers(),
    )
    assert response.status_code == 422
    assert "输入的 API Key" in response.json()["detail"]
    assert "wrong-key" not in response.text


def test_background_agent_uses_owner_model(monkeypatch):
    Base.metadata.create_all(engine)
    owner = "background-model@example.com"
    model_config.save_user_config(
        owner, "ollama", "http://localhost:11434/v1", "private-local-model", ""
    )
    with SessionLocal() as db:
        run = AgentRun(owner_id=owner, question="question", paper_ids=[])
        db.add(run)
        db.commit()
        run_id = run.id
    seen = []
    monkeypatch.setattr(
        worker,
        "run_react",
        lambda _run, _emit: (
            seen.append(model_config.effective_settings().llm_model) or "Answer",
            [],
        ),
    )
    monkeypatch.setattr(worker, "number_citations", lambda answer, evidence: (answer, evidence))
    worker.run_agent.run(run_id)
    assert seen == ["private-local-model"]
    with SessionLocal() as db:
        db.execute(delete(RunEvent).where(RunEvent.run_id == run_id))
        db.delete(db.get(AgentRun, run_id))
        db.delete(db.get(UserModelConfig, owner))
        db.commit()


def test_activity_hides_internal_task_details():
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        paper = Paper(owner_id=settings().demo_email, title="A Research Paper")
        db.add(paper)
        db.flush()
        version = Version(paper_id=paper.id, filename="paper.pdf", file_key="file", sha256="a" * 64)
        db.add(version)
        db.flush()
        task = BackgroundTask(
            owner_id=paper.owner_id,
            type="parse_pdf",
            status="running",
            progress=0.6,
            args={"version_id": version.id},
            result={"secret": "internal"},
        )
        db.add(task)
        db.commit()
        task_id = task.id
    response = TestClient(main.app).get("/api/v1/activity", headers=_headers())
    assert response.status_code == 200
    item = next(item for item in response.json()["items"] if item["id"] == task_id)
    assert "A Research Paper" in item["title"]
    assert item["progress"] == 0.6
    assert "args" not in item and "result" not in item and "error" not in item
    with SessionLocal() as db:
        db.delete(db.get(BackgroundTask, task_id))
        db.delete(db.scalar(select(Version).where(Version.id == version.id)))
        db.delete(db.get(Paper, paper.id))
        db.commit()
