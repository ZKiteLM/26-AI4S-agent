import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from services.api.app.db import Base
from services.api.app.graph_review import (
    ReviewResearch,
    _compact_messages,
    _tool_result_json,
    _usable_abstract,
    generate_review,
)
from services.api.app.models import (
    BackgroundTask,
    Block,
    GraphReviewRun,
    GraphView,
    GraphViewNode,
    Paper,
    ScholarlyWork,
    Version,
)


@pytest.fixture
def research():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        view = GraphView(
            owner_id="owner", config={"mode": "internal"}, status="completed", version=1
        )
        task = BackgroundTask(owner_id="owner", type="graph_insight", args={})
        db.add_all([view, task])
        db.flush()
        works = []
        for index in range(25):
            work = ScholarlyWork(
                title=f"Paper {index:02d}",
                authors=[f"Author {index}"],
                year=2020 + index % 5,
                abstract=f"Branch {index % 3} studies method {index} and its limits.",
                openalex_id=f"W{index + 100}",
            )
            db.add(work)
            db.flush()
            db.add(GraphViewNode(graph_id=view.id, work_id=work.id))
            works.append(work)
        paper = Paper(owner_id="owner", title=works[0].title, work_id=works[0].id)
        db.add(paper)
        db.flush()
        version = Version(
            paper_id=paper.id,
            filename="paper.pdf",
            file_key="paper.pdf",
            sha256="a" * 64,
            parse_status="completed",
        )
        db.add(version)
        db.flush()
        paper.preferred_version_id = version.id
        block = Block(
            version_id=version.id, page_index=0, order=0, text="Actual PDF method evidence."
        )
        run = GraphReviewRun(
            graph_id=view.id, owner_id="owner", task_id=task.id, graph_version=view.version
        )
        db.add_all([block, run])
        db.commit()
        yield ReviewResearch(db, run, task, view), works, paper, block
    engine.dispose()


def _call(name, arguments, number):
    return {
        "id": f"call-{number}",
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(arguments)},
    }


def test_catalog_covers_more_than_twenty_and_mixed_sources(research):
    service, works, paper, block = research
    pages, offset = [], 0
    while offset is not None:
        page = service.list_papers(offset, 50)
        pages.extend(page["items"])
        offset = page["next_offset"]
    assert len(pages) == 25
    assert sorted(item["rank_position"] for item in pages) == list(range(1, 26))
    assert pages[1]["rank_position"] > 25 // 3
    assert len(service.list_papers(0, 50)["items"]) == 20
    abstract = service.planner_tool("read_graph_abstract", {"work_id": works[1].id})["source"]
    assert abstract["source_type"] == "abstract" and abstract["block_id"] is None
    assert abstract["quote"] == works[1].abstract
    pdf = service.planner_tool("read", {"paper_id": paper.id, "start": 0, "length": 1})["sources"][
        0
    ]
    assert pdf["source_type"] == "pdf_block" and pdf["block_id"] == block.id
    assert pdf["evidence_id"] != block.id
    assert pdf["version_id"] == paper.preferred_version_id
    assert (
        service.planner_tool("read_graph_abstract", {"work_id": works[1].id})["source"][
            "evidence_id"
        ]
        == abstract["evidence_id"]
    )
    assert len(service.evidence_map()) == 2
    with pytest.raises(ValueError, match="owned"):
        service.planner_tool("read", {"paper_id": "outside", "start": 0, "length": 1})
    with pytest.raises(ValueError, match="English"):
        service.planner_tool("search_bm25", {"query": "中文检索"})
    with pytest.raises(ValueError, match="citation syntax"):
        service.citation_ids(f"Valid [E:{pdf['evidence_id']}] and invalid [0]")
    block.text = "Changed source text"
    service.db.commit()
    with pytest.raises(ValueError, match="provenance"):
        service.validate_evidence(service.evidence_map()[pdf["evidence_id"]])


def test_dynamic_outline_checkpoint_and_non_top_coverage(research):
    service, works, _, _ = research
    ids = [
        service.planner_tool("read_graph_abstract", {"work_id": work.id})["source"]["evidence_id"]
        for work in (works[0], works[1], works[20])
    ]
    first = [
        {"id": "a", "title": "背景", "evidence_ids": ids[:2]},
        {"id": "b", "title": "方法", "evidence_ids": [ids[0]]},
    ]
    assert service.update_outline(first)["saved"]
    assert not service.finish_research()["finished"]
    with pytest.raises(ValueError, match="Unknown evidence IDs"):
        service.update_outline(
            [
                {"id": "a", "title": "背景", "evidence_ids": [works[0].id]},
                {"id": "b", "title": "方法", "evidence_ids": ids[:1]},
            ]
        )
    revised = [
        {"section_id": "b", "section_title": "技术分支比较", "evidence_ids": [ids[0], ids[2]]},
        {"id": "c", "title": "局限与演进", "evidence_ids": [ids[1], ids[2]]},
    ]
    service.update_outline(revised)
    assert [s["id"] for s in service.run.outline["sections"]] == ["b", "c"]
    assert service.finish_research()["finished"]
    service.db.expire_all()
    assert service.db.get(GraphReviewRun, service.run.id).outline["sections"][0]["id"] == "b"


def test_bibliography_is_not_abstract_and_breadth_need_not_be_cited(research):
    service, works, _, _ = research
    works[20].abstract = (
        "Alice Smith, Bob Jones, Carol Lee, David Kim. Proceedings of the Example Conference. 2025."
    )
    service.db.commit()
    service.nodes[works[20].id]["abstract"] = works[20].abstract
    assert not _usable_abstract(works[20].abstract)
    assert not next(
        item for item in service.list_papers(0, 25)["items"] if item["work_id"] == works[20].id
    )["has_abstract"]
    with pytest.raises(ValueError, match="usable research abstract"):
        service.planner_tool("read_graph_abstract", {"work_id": works[20].id})

    works[20].abstract = "A real study of a distinct research branch with methods and limitations."
    service.db.commit()
    service.nodes[works[20].id]["abstract"] = works[20].abstract
    ids = [
        service.planner_tool("read_graph_abstract", {"work_id": work.id})["source"]["evidence_id"]
        for work in (works[0], works[1], works[2], works[20])
    ]
    service.update_outline(
        [
            {"id": "a", "title": "研究背景", "evidence_ids": ids[:2]},
            {"id": "b", "title": "方法比较", "evidence_ids": [ids[2]]},
        ]
    )
    assert service.finish_research()["finished"]


def test_planner_native_calls_and_checkpointed_messages(monkeypatch, research):
    service, works, _, _ = research
    responses = 0

    def model(messages, tools, **kwargs):
        nonlocal responses
        responses += 1
        if responses == 1:
            calls = [
                _call("read_graph_abstract", {"work_id": work.id}, index)
                for index, work in enumerate((works[0], works[1], works[20]), 1)
            ]
        elif responses == 2:
            ids = list(service.evidence_map())
            calls = [
                _call(
                    "update_outline",
                    {
                        "sections": [
                            {"id": "a", "title": "背景", "evidence_ids": ids[:2]},
                            {"id": "b", "title": "分支比较", "evidence_ids": ids[1:]},
                        ]
                    },
                    4,
                )
            ]
        else:
            calls = [_call("finish_research", {}, 5)]
        return {"role": "assistant", "content": "", "tool_calls": calls}

    monkeypatch.setattr("services.api.app.graph_review.chat_with_tools", model)
    service.planner()
    assert responses == 3
    assert service.run.coverage["gaps"] == []
    assert sum(item["role"] == "tool" for item in service.run.messages) == 5
    assert service.db.get(GraphReviewRun, service.run.id).outline["sections"][1]["id"] == "b"
    service.db.expire_all()
    assert len(service.db.get(GraphReviewRun, service.run.id).messages) == 11


def test_planner_tool_results_stay_parseable_when_long(research):
    service, _, _, _ = research
    catalog = service.list_papers(0, 50)
    compact = json.loads(_tool_result_json(catalog, limit=900))
    assert compact["truncated"] is True
    assert compact["next_offset"] == len(compact["items"])
    assert len(json.dumps(compact, ensure_ascii=False)) <= 900


def test_planner_prompts_for_outline_after_sufficient_research(monkeypatch, research):
    service, works, _, _ = research
    visited = []

    def model(messages, tools, **kwargs):
        names = [tool["function"]["name"] for tool in tools]
        visited.append(names)
        if len(visited) <= 5:
            return {
                "role": "assistant",
                "content": "",
                "tool_calls": [_call("read_graph_abstract", {"work_id": works[[0, 1, 2, 3, 20][len(visited) - 1]].id}, len(visited))],
            }
        ids = list(service.evidence_map())
        if len(visited) == 6:
            assert names == ["update_outline"]
            return {
                "role": "assistant",
                "content": "",
                "tool_calls": [_call("update_outline", {"sections": [
                    {"id": "a", "title": "研究背景", "evidence_ids": ids[:3]},
                    {"id": "b", "title": "方法分支", "evidence_ids": ids[3:]},
                ]}, 6)],
            }
        return {"role": "assistant", "content": "", "tool_calls": [_call("finish_research", {}, 7)]}

    monkeypatch.setattr("services.api.app.graph_review.chat_with_tools", model)
    service.planner()
    assert len(visited) == 7
    assert service.run.coverage["gaps"] == []


def test_review_resumes_verification_from_written_checkpoint(monkeypatch, research):
    service, _, _, _ = research
    service.run.outline = {"sections": [{"id": "a", "title": "背景", "evidence_ids": []}]}
    service.run.sections = [{"id": "a", "title": "背景", "markdown": "正文", "status": "written"}]
    service.run.coverage = {"gaps": []}
    calls = []
    monkeypatch.setattr(ReviewResearch, "planner", lambda _self: calls.append("planner"))
    monkeypatch.setattr(ReviewResearch, "writer", lambda _self: calls.append("writer"))
    monkeypatch.setattr(ReviewResearch, "verify", lambda _self: calls.append("verify"))
    monkeypatch.setattr(ReviewResearch, "finalize", lambda _self: ("completed", []))
    assert generate_review(service.db, service.view, service.run, service.task) == ("completed", [])
    assert calls == ["verify"]


def test_writer_sections_evidence_and_stable_final_citations(monkeypatch, research):
    service, works, _, _ = research
    one = service.planner_tool("read_graph_abstract", {"work_id": works[0].id})["source"][
        "evidence_id"
    ]
    two = service.planner_tool("read_graph_abstract", {"work_id": works[1].id})["source"][
        "evidence_id"
    ]
    service.update_outline(
        [
            {"id": "a", "title": "背景", "evidence_ids": [one]},
            {"id": "b", "title": "方法比较", "evidence_ids": [one, two]},
        ]
    )
    calls = 0

    def model(messages, tools, **kwargs):
        nonlocal calls
        calls += 1
        if calls % 2:
            ids = [one] if calls == 1 else [one, two]
            tool = _call("retrieve_evidence", {"evidence_ids": ids}, calls)
        else:
            ids = [one] if calls == 2 else [one, two]
            cited = f"[E:{one}]" if calls == 2 else f"[E:{one}] 与 [E:{two}]"
            tool = _call(
                "write_section",
                {
                    "markdown": f"本节比较图谱中的研究路径及其证据边界。{cited} "
                    + "依据原始摘要描述研究方法。" * 5,
                    "evidence_ids": ids,
                },
                calls,
            )
        return {"role": "assistant", "content": "", "tool_calls": [tool]}

    monkeypatch.setattr("services.api.app.graph_review.chat_with_tools", model)
    service.writer()
    assert calls == 4 and len(service.run.sections) == 2
    assert len(service.run.messages) == 10  # section prompts plus tool-call/result pairs
    service.run.sections[0]["markdown"] = "## 背景\n\n" + service.run.sections[0]["markdown"]
    body, sources = service.finalize()
    assert len(sources) == 2
    assert sources[0]["evidence_ids"] == [one]
    assert body.count("[1]") >= 3 and "[2]" in body
    assert "[E:" not in body
    assert body.count("## 背景") == 1
    assert "## References" in body


def test_verifier_revises_partial_claim_and_rejects_unknown_id(monkeypatch, research):
    service, works, _, _ = research
    eid = service.planner_tool("read_graph_abstract", {"work_id": works[0].id})["source"][
        "evidence_id"
    ]
    service.update_outline(
        [
            {"id": "a", "title": "背景", "evidence_ids": [eid]},
            {"id": "b", "title": "方法", "evidence_ids": [eid]},
        ]
    )
    section = service.run.outline["sections"][0]
    service.writer_tool("retrieve_evidence", {"evidence_ids": [eid]}, section)
    with pytest.raises(ValueError, match="citation"):
        service.writer_tool(
            "write_section",
            {
                "markdown": "A claim " * 20 + "[E:00000000-0000-0000-0000-000000000000]",
                "evidence_ids": [eid],
            },
            section,
        )
    service.run.sections = [
        {
            "id": "a",
            "title": "背景",
            "markdown": "夸大的研究结论" + f"[E:{eid}]。",
            "evidence_ids": [eid],
            "status": "written",
        }
    ]
    replies = iter(
        [
            '{"assessments":[{"index":0,"status":"partial"}]}',
            f"该论文研究方法边界 [E:{eid}]。",
            '{"assessments":[{"index":0,"status":"supported"}]}',
        ]
    )
    monkeypatch.setattr(
        "services.api.app.graph_review.generate", lambda *args, **kwargs: next(replies)
    )
    service.verify()
    assert "夸大" not in service.run.sections[0]["markdown"]
    assert service.run.verification[-1]["status"] == "supported"


def test_verifier_removes_uncited_fact_when_revision_adds_no_evidence(monkeypatch, research):
    service, works, _, _ = research
    eid = service.planner_tool("read_graph_abstract", {"work_id": works[0].id})["source"][
        "evidence_id"
    ]
    unsupported = "该方法在所有数据集上都取得显著提升并且完全消除了训练成本。"
    supported = f"论文摘要讨论方法及其局限 [E:{eid}]。"
    service.run.sections = [
        {
            "id": "a",
            "title": "背景",
            "markdown": unsupported + supported,
            "evidence_ids": [eid],
            "status": "written",
        }
    ]
    replies = iter(
        [
            '{"assessments":[{"index":0,"status":"supported"}]}',
            unsupported + supported,
            '{"assessments":[{"index":0,"status":"supported"}]}',
        ]
    )
    monkeypatch.setattr(
        "services.api.app.graph_review.generate", lambda *args, **kwargs: next(replies)
    )
    service.verify()
    assert unsupported not in service.run.sections[0]["markdown"]
    assert supported in service.run.sections[0]["markdown"]


def test_verifier_discards_revision_with_unknown_evidence(monkeypatch, research):
    service, works, _, _ = research
    eid = service.planner_tool("read_graph_abstract", {"work_id": works[0].id})["source"][
        "evidence_id"
    ]
    false_claim = f"该研究已经解决全部问题 [E:{eid}]。"
    supported = f"论文摘要讨论方法及局限 [E:{eid}]。"
    service.run.sections = [
        {
            "id": "a",
            "title": "背景",
            "markdown": false_claim + supported,
            "evidence_ids": [eid],
            "status": "written",
        }
    ]
    replies = iter(
        [
            '{"assessments":[{"index":0,"status":"unsupported"},{"index":1,"status":"supported"}]}',
            false_claim + "[E:00000000-0000-0000-0000-000000000000]" + supported,
            '{"assessments":[{"index":0,"status":"supported"}]}',
        ]
    )
    monkeypatch.setattr(
        "services.api.app.graph_review.generate", lambda *args, **kwargs: next(replies)
    )
    service.verify()
    assert false_claim not in service.run.sections[0]["markdown"]
    assert supported in service.run.sections[0]["markdown"]


def test_message_trim_keeps_tool_call_with_result():
    call = _call("read_graph_abstract", {"work_id": "x"}, 1)
    messages = [
        {"role": "system", "content": "rules"},
        {"role": "user", "content": "topic"},
        {"role": "assistant", "content": "", "tool_calls": [call]},
        {"role": "tool", "tool_call_id": "call-1", "content": "X" * 1200},
        {"role": "user", "content": "continue"},
    ]
    trimmed = _compact_messages(messages, 200)
    assert all(item["role"] != "tool" for item in trimmed)
    assert trimmed[0]["role"] == "system"


def test_planner_budget_failure_preserves_checkpoint(monkeypatch, research):
    service, _, _, _ = research
    monkeypatch.setattr(
        "services.api.app.graph_review.chat_with_tools",
        lambda *_args, **_kwargs: {"role": "assistant", "content": "not done", "tool_calls": []},
    )
    with pytest.raises(RuntimeError, match="预算"):
        service.planner()
    service.db.expire_all()
    saved = service.db.get(GraphReviewRun, service.run.id)
    assert saved.coverage["gaps"] and saved.messages
    assert service.task.result["phase"] == "planning"


def test_planner_accepts_valid_last_outline_without_extra_model_round(monkeypatch, research):
    service, works, _, _ = research
    turns = 0

    def model(messages, tools, **kwargs):
        nonlocal turns
        turns += 1
        if turns == 1:
            calls = [
                _call("read_graph_abstract", {"work_id": work.id}, i)
                for i, work in enumerate((works[0], works[1], works[20]), 1)
            ]
        elif turns == 14:
            ids = list(service.evidence_map())
            calls = [
                _call(
                    "update_outline",
                    {
                        "sections": [
                            {"id": "a", "title": "背景", "evidence_ids": ids[:2]},
                            {"id": "b", "title": "分支", "evidence_ids": ids[1:]},
                        ]
                    },
                    4,
                )
            ]
        else:
            calls = []
        return {"role": "assistant", "content": "", "tool_calls": calls}

    monkeypatch.setattr("services.api.app.graph_review.chat_with_tools", model)
    service.planner()
    assert service.run.coverage["gaps"] == []


def test_changed_graph_version_prevents_publication(monkeypatch, research):
    service, _, _, _ = research

    def change_graph(self):
        self.view.version += 1

    monkeypatch.setattr(ReviewResearch, "planner", change_graph)
    monkeypatch.setattr(ReviewResearch, "writer", lambda self: None)
    monkeypatch.setattr(ReviewResearch, "verify", lambda self: None)
    with pytest.raises(ValueError, match="图谱已变化"):
        generate_review(service.db, service.view, service.run, service.task)
