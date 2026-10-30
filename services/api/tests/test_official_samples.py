import json
import os
import tempfile
import zipfile
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

db_fd, db_path = tempfile.mkstemp(suffix=".sqlite3")
os.close(db_fd)
os.environ["DATABASE_URL"] = f"sqlite:///{db_path}"
os.environ["S3_ENDPOINT"] = ""
os.environ["LOCAL_STORAGE"] = tempfile.mkdtemp()
os.environ["PARSER_VECTOR_ENHANCE"] = "0"

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from services.api.app.charts import render_chart
from services.api.app.db import Base, SessionLocal
from services.api.app.graphs import build_graph
from services.api.app.main import app
from services.api.app.models import Block, Paper, Translation, Version
from services.api.app.parsing import _pymupdf_blocks, identify_pdf, parse_pdf
from services.api.app.translation import protect_math, restore_math
from services.api.app.worker import _compose, _verify

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


def test_version_conflict_requires_user_resolution(monkeypatch):
    monkeypatch.setattr("services.api.app.main.parse_version.apply_async", lambda **kwargs: None)
    with TestClient(app) as client:
        login = client.post(
            "/api/v1/auth/login", json={"email": "demo@example.com", "password": "change-me"}
        )
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
    image, script, caption, spec = render_chart(b"epoch,score\n1,0.3\n2,0.5\n3,0.7\n")
    assert image.startswith(b"\x89PNG")
    assert "0.3 to 0.7" in caption
    assert "pd.read_csv" in script
    assert spec["rows"] == 3


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


def test_answer_rejects_unverified_citation(monkeypatch):
    monkeypatch.setattr("services.api.app.worker.event", lambda *args: None)
    valid_id = "11111111-1111-1111-1111-111111111111"
    fake_id = "22222222-2222-2222-2222-222222222222"
    state = {
        "run_id": "run",
        "evidence": [{"evidence_id": valid_id}],
        "answer": f"Unsupported fact [{fake_id}]",
    }
    assert "未通过" in _verify(state)["answer"]


def test_short_evidence_labels_map_to_real_block_ids(monkeypatch):
    monkeypatch.setattr("services.api.app.worker.generate", lambda *args, **kwargs: "β1=0.9 [E1]")
    source_id = "11111111-1111-1111-1111-111111111111"
    state = {
        "question": "What is beta1?",
        "kind": "chat",
        "paper_ids": ["paper"],
        "evidence": [
            {
                "evidence_id": source_id,
                "paper_title": "Paper",
                "page_index": 0,
                "quote": "The optimizer uses beta1=0.9.",
            }
        ],
    }
    assert _compose(state)["answer"] == f"β1=0.9 [{source_id}]"


def test_structured_comparison_uses_selected_papers_and_real_sources(monkeypatch):
    ids = ["11111111-1111-1111-1111-111111111111", "22222222-2222-2222-2222-222222222222"]
    answer = {
        "papers": [
            {
                "architecture": "纯注意力",
                "architecture_source": "E1",
                "goal": "机器翻译",
                "goal_source": "E1",
            },
            {
                "architecture": "稀疏注意力",
                "architecture_source": "E2",
                "goal": "研究规模",
                "goal_source": "E2",
            },
        ]
    }
    monkeypatch.setattr(
        "services.api.app.worker.generate", lambda *args, **kwargs: json.dumps(answer)
    )
    state = {
        "question": "对比架构和目标",
        "kind": "chat",
        "paper_ids": ["p1", "p2"],
        "evidence": [
            {
                "evidence_id": ids[i],
                "paper_id": f"p{i + 1}",
                "paper_title": f"Paper {i + 1}",
                "page_index": 0,
                "quote": "Source text",
            }
            for i in range(2)
        ],
    }
    result = _compose(state)["answer"]
    assert all(f"[{source_id}]" in result for source_id in ids)
    assert "Paper 1" in result and "Paper 2" in result
    assert "[E1]" not in result


def test_answer_rejects_claim_not_supported_by_quoted_source(monkeypatch):
    monkeypatch.setattr("services.api.app.worker.event", lambda *args: None)
    monkeypatch.setattr(
        "services.api.app.worker.generate", lambda *args, **kwargs: '{"supported": false}'
    )
    source_id = "11111111-1111-1111-1111-111111111111"
    state = {
        "run_id": "run",
        "evidence": [{"evidence_id": source_id, "quote": "GPT-3 uses a transformer."}],
        "answer": f"GPT-3 does not use attention [{source_id}]",
    }
    assert "未通过" in _verify(state)["answer"]


def test_answer_rejects_uncited_summary_line(monkeypatch):
    monkeypatch.setattr("services.api.app.worker.event", lambda *args: None)
    source_id = "11111111-1111-1111-1111-111111111111"
    state = {
        "run_id": "run",
        "evidence": [{"evidence_id": source_id, "quote": "A uses attention."}],
        "answer": f"A uses attention [{source_id}]\nB is faster.",
    }
    assert "未通过" in _verify(state)["answer"]


def test_remote_parser_adapter_preserves_equation_and_location(monkeypatch):
    from services.api.app import parsing

    archive = BytesIO()
    content = {
        "pages": [
            {
                "page_idx": 0,
                "blocks": [{"type": "equation", "content": "E=mc^2", "bbox": [0.1, 0.2, 0.6, 0.3]}],
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
    assert blocks[0]["bbox"] == [0.1, 0.2, 0.6, 0.3]


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
            },
        )(),
    )
    calls = []

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {"message": {"content": "answer"}}

    def post(url, **kwargs):
        calls.append((url, kwargs))
        return Response()

    monkeypatch.setattr(ai.httpx, "post", post)
    assert ai.generate("system", "question", max_tokens=100) == "answer"
    assert calls[0][0] == "http://localhost:11434/api/chat"
    assert calls[0][1]["json"]["think"] is False


def test_cloud_mode_rejects_local_model_endpoints():
    from services.api.app.config import Settings

    with pytest.raises(ValidationError, match="Cloud mode requires"):
        Settings(deployment_mode="cloud", llm_provider="ollama", parser_backend="local")
