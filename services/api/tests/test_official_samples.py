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
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from services.api.app.charts import render_chart
from services.api.app.chat_agent import (
    _compact_query,
    _model_sources,
    author_order_answer,
    number_citations,
    run_react,
)
from services.api.app.db import Base, SessionLocal
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
from services.api.app.retrieval import make_chunks, paper_outline, read_paper_fragment, search
from services.api.app.translation import protect_math, restore_math, translate_text
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


def test_react_searches_multiple_queries_then_refines(monkeypatch):
    from services.api.app import chat_agent

    decisions = iter(
        [
            {"action": "search", "queries": ["optimizer beta", "training hardware"]},
            {"action": "search", "queries": ["Adam beta1 exact value"]},
            {"action": "answer", "answer": "Beta1 is 0.9 [E1]."},
        ]
    )
    queried = []
    events = []
    source_id = "11111111-1111-1111-1111-111111111111"
    monkeypatch.setattr(chat_agent, "_ask_model", lambda *_args: next(decisions))

    def fake_search(_owner, _papers, query):
        queried.append(query)
        return (
            [
                {
                    "evidence_id": source_id,
                    "block_id": source_id,
                    "paper_id": "p",
                    "paper_title": "Fixture",
                    "page_index": 2,
                    "quote": "Adam uses beta1=0.9.",
                }
            ]
            if query == "Adam beta1 exact value"
            else []
        )

    monkeypatch.setattr(chat_agent, "_search_one", fake_search)
    answer, evidence = run_react(
        SimpleNamespace(
            session_id=None, question="What is beta1?", owner_id="owner", paper_ids=["p"]
        ),
        lambda name, data: events.append((name, data)),
    )
    assert set(queried) == {"optimizer beta", "training hardware", "Adam beta1 exact value"}
    assert [data["round"] for name, data in events if name == "tool_started"] == [1, 2]
    numbered, sources = number_citations(answer, evidence)
    assert numbered == "Beta1 is 0.9 [1]."
    assert [(source["number"], source["evidence_id"]) for source in sources] == [(1, source_id)]


def test_search_query_omits_selected_paper_title():
    assert _compact_query("A Study of Widgets optimizer setting", ["A Study of Widgets"]) == (
        "optimizer setting"
    )


def test_agent_context_budget_keeps_latest_evidence():
    evidence = [{"paper_title": "Paper", "page_index": 0, "quote": "x" * 2400} for _ in range(24)]
    sources = _model_sources(evidence)
    assert sum(len(item["quote"]) for item in sources) <= 12000
    assert sources[-1]["id"] == "E24"


def test_react_reads_document_position_before_answer(monkeypatch):
    from services.api.app import chat_agent

    decisions = iter(
        [
            {"action": "read", "paper_id": "paper", "start": 0, "length": 1},
            {"action": "read", "paper_id": "paper", "start": 0, "length": 8},
            {"action": "answer", "answer": "第六作者是 Aidan Gomez [E1]。"},
        ]
    )
    source_id = "11111111-1111-1111-1111-111111111111"
    events = []
    monkeypatch.setattr(chat_agent, "_ask_model", lambda *_args: next(decisions))
    monkeypatch.setattr(
        chat_agent,
        "_read_one",
        lambda _owner, _paper, _start, length: [
            {
                "evidence_id": source_id,
                "block_id": source_id,
                "paper_id": "paper",
                "paper_title": "Fixture",
                "page_index": 0,
                "quote": (
                    "First Author\n...\nAidan Gomez · University of Toronto"
                    if length > 1
                    else "First Author"
                ),
            }
        ],
    )
    answer, evidence = run_react(
        SimpleNamespace(
            session_id=None, question="第六作者是谁？", owner_id="owner", paper_ids=["paper"]
        ),
        lambda name, data: events.append((name, data)),
    )
    assert f"[{source_id}]" in answer
    assert len(evidence) == 1
    assert [data["tool"] for name, data in events if name == "tool_started"] == [
        "read_paper",
        "read_paper",
    ]
    assert events[-1][1]["sources"][0]["quote"].startswith("First Author")
    assert events[-1][1]["expanded_sources"] == 1
    assert "Aidan Gomez" in evidence[0]["quote"]


def test_search_includes_each_selected_paper(monkeypatch):
    from services.api.app import chat_agent

    searched_papers = []

    def fake_search(_db, _owner, paper_ids, _query, limit):
        assert limit == 4
        searched_papers.extend(paper_ids)
        return [{"paper_id": paper_ids[0]}]

    monkeypatch.setattr(chat_agent, "search", fake_search)
    results = chat_agent._search_one("owner", ["paper-a", "paper-b"], "optimizer")
    assert searched_papers == ["paper-a", "paper-b"]
    assert [item["paper_id"] for item in results] == searched_papers


def test_small_passage_returns_author_list_parent_and_read_by_index(monkeypatch):
    from services.api.app import retrieval

    monkeypatch.setattr(retrieval, "settings", lambda: SimpleNamespace(qdrant_url=""))
    with SessionLocal() as db:
        paper = Paper(owner_id="reader", title="Fixture Paper")
        db.add(paper)
        db.flush()
        version = Version(
            paper_id=paper.id, filename="fixture.pdf", file_key="fixture", sha256="a" * 64
        )
        db.add(version)
        db.flush()
        paper.preferred_version_id = version.id
        authors = [
            "First Author · Institute A",
            "Second Author · Institute B",
            "Third Author · Institute C",
            "Fourth Author · Institute D",
            "Fifth Author · Institute E",
            "Aidan Gomez · † University of Toronto",
        ]
        db.add(
            Block(version_id=version.id, page_index=0, order=0, type="title", text="Fixture Paper")
        )
        db.add_all(
            [
                Block(
                    version_id=version.id,
                    page_index=0,
                    order=index,
                    type="paragraph",
                    text=f"{text} author{index}@example.org",
                )
                for index, text in enumerate(authors, 1)
            ]
        )
        db.add(Block(version_id=version.id, page_index=0, order=7, type="heading", text="Abstract"))
        db.add(
            Block(
                version_id=version.id,
                page_index=0,
                order=8,
                type="page_footnote",
                text="†Work performed while at Research Lab.",
            )
        )
        db.commit()
        make_chunks(db, version.id)
        hits = search(db, "reader", [paper.id], "Aidan Gomez Toronto", limit=2)
        assert hits and "First Author" in hits[0]["quote"]
        assert "Aidan Gomez · † University of Toronto" in hits[0]["quote"]
        assert "[Author 6] Aidan Gomez" in hits[0]["quote"]
        assert "Related first-page footnotes" in hits[0]["quote"]
        answer = author_order_answer("第 6 作者是谁，在哪个机构工作？", hits)
        assert "Aidan Gomez" in answer and "University of Toronto" in answer
        assert "Research Lab" in answer
        outline = paper_outline(db, "reader", [paper.id])
        assert outline[0]["sections"][0]["index"] == 0
        assert (
            "[Author 6] Aidan Gomez"
            in read_paper_fragment(db, "reader", paper.id, 0, 7)[0]["quote"]
        )
        assert read_paper_fragment(db, "other", paper.id, 0, 6) == []
        blocks = db.scalars(select(Block).where(Block.version_id == version.id)).all()
        passage_id = next(
            key for key, _, text in retrieval._passages(blocks) if "First Author" in text
        )
        monkeypatch.setattr(retrieval, "settings", lambda: SimpleNamespace(qdrant_url="mock"))
        monkeypatch.setattr(retrieval, "embed", lambda _texts: [[0.1, 0.2]])
        monkeypatch.setattr(
            retrieval,
            "vector_client",
            lambda: SimpleNamespace(
                collection_exists=lambda _name: True,
                query_points=lambda *_args, **_kwargs: SimpleNamespace(
                    points=[SimpleNamespace(id=passage_id)]
                ),
            ),
        )
        assert (
            "First Author"
            in search(db, "reader", [paper.id], "unmatchedtoken", limit=1)[0]["quote"]
        )


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
