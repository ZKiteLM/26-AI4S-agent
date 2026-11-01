from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from services.api.app import chat_agent, lexical, retrieval
from services.api.app.db import Base
from services.api.app.models import Block, ChatMessage, ChatSession, Paper, Version


@pytest.fixture
def agent_db(monkeypatch):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(chat_agent, "SessionLocal", sessions)
    return sessions


def _source(paper_id="paper"):
    block_id = str(uuid4())
    return {
        "evidence_id": block_id,
        "paper_id": paper_id,
        "paper_title": "Research paper",
        "paper_version_id": "version",
        "block_id": block_id,
        "page_index": 0,
        "bbox": [0, 0, 1, 1],
        "quote": "The method uses a transformer encoder.",
        "start": 4,
        "length": 1,
    }


def test_tool_calls_results_and_followup_use_persisted_messages(agent_db, monkeypatch):
    sessions = agent_db
    source = _source()
    calls = []

    def model(messages, tools, *, thinking=False):
        calls.append(messages)
        if len(calls) == 1:
            return {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "call-a",
                        "type": "function",
                        "function": {
                            "name": "search_bm25",
                            "arguments": '{"query":"transformer encoder"}',
                        },
                    },
                    {
                        "id": "call-b",
                        "type": "function",
                        "function": {
                            "name": "search_semantic",
                            "arguments": '{"query":"model architecture"}',
                        },
                    },
                ],
            }
        assert {m.get("tool_call_id") for m in messages if m["role"] == "tool"} == {
            "call-a",
            "call-b",
        }
        return {
            "role": "assistant",
            "content": f"The method uses an encoder [{source['evidence_id']}].",
            "tool_calls": [],
        }

    monkeypatch.setattr(chat_agent, "chat_with_tools", model)
    monkeypatch.setattr(
        chat_agent,
        "paper_outline",
        lambda *args: [
            {
                "paper_id": "paper",
                "version_id": "version",
                "title": "Research paper",
                "block_count": 9,
                "sections": [],
            }
        ],
    )
    monkeypatch.setattr(chat_agent, "search_bm25", lambda *args, **kwargs: [source])
    monkeypatch.setattr(chat_agent, "search_semantic", lambda *args, **kwargs: [source])
    with sessions() as db:
        db.add(ChatSession(id="session", owner_id="owner", paper_ids=["paper"]))
        db.add(
            ChatMessage(
                session_id="session", run_id="run", role="user", content="What architecture?"
            )
        )
        db.commit()
    run = SimpleNamespace(
        id="run",
        session_id="session",
        question="What architecture?",
        paper_ids=["paper"],
        owner_id="owner",
    )
    events = []
    answer, evidence = chat_agent.run_react(run, lambda name, data: events.append((name, data)))
    assert source["evidence_id"] in answer and len(evidence) == 1
    assert len([x for x in events if x[0] == "tool_started"]) == 2
    with sessions() as db:
        rows = db.query(ChatMessage).filter(ChatMessage.session_id == "session").all()
        assert [row.role for row in rows] == ["user", "assistant", "tool", "tool"]
        assert [row.tool_call_id for row in rows[2:]] == ["call-a", "call-b"]
        db.add(
            ChatMessage(
                session_id="session", role="assistant", content="Previous answer", evidence=[]
            )
        )
        db.add(
            ChatMessage(
                session_id="session", role="user", content="Follow-up question", run_id="next"
            )
        )
        db.commit()
    inherited = []
    monkeypatch.setattr(
        chat_agent,
        "chat_with_tools",
        lambda messages, tools, **kwargs: (
            inherited.extend(messages)
            or {
                "content": f"The method uses an encoder [{source['evidence_id']}].",
                "tool_calls": [],
            }
        ),
    )
    followup_answer, followup_evidence = chat_agent.run_react(
        SimpleNamespace(
            id="next",
            session_id="session",
            question="Follow-up question",
            paper_ids=["paper"],
            owner_id="owner",
        ),
        lambda *_: None,
    )
    assert any(message.get("tool_calls") for message in inherited)
    assert any(message["role"] == "tool" for message in inherited)
    assert any(message.get("content") == "Follow-up question" for message in inherited)
    assert source["evidence_id"] in followup_answer and len(followup_evidence) == 1


def test_read_uses_contiguous_block_indices_and_complete_text(agent_db):
    with agent_db() as db:
        paper = Paper(id="paper", owner_id="owner", title="Paper", preferred_version_id="version")
        version = Version(
            id="version",
            paper_id="paper",
            filename="paper.pdf",
            file_key="file",
            sha256="a" * 64,
            parse_status="completed",
        )
        db.add_all([paper, version])
        db.add_all(
            [
                Block(
                    version_id="version",
                    page_index=0,
                    order=i,
                    text=("X" * 5000 if i == 2 else f"Block {i}"),
                )
                for i in range(5)
            ]
        )
        db.commit()
        items = retrieval.read_paper_fragment(db, "owner", "paper", 1, 3)
        assert [item["start"] for item in items] == [1, 2, 3]
        assert len(items[1]["quote"]) == 5000
        assert all(item["paper_version_id"] == "version" for item in items)
        assert retrieval.read_paper_fragment(db, "other", "paper", 1, 3) == []


def test_plan_enables_reasoning_and_search_requires_english(agent_db, monkeypatch):
    run = SimpleNamespace(owner_id="owner", paper_ids=["paper"])
    seen = []
    monkeypatch.setattr(
        chat_agent,
        "chat_with_tools",
        lambda messages, tools, *, thinking=False, json_mode=False: (
            seen.append((messages, tools, thinking, json_mode))
            or {
                "content": '{"steps":[{"tool":"read","paper_id":"paper","start":0,"length":4}]}',
                "tool_calls": [],
            }
        ),
    )
    result = chat_agent._execute(
        "plan",
        {"question": "Compare the methods"},
        run,
        [
            {
                "paper_id": "paper",
                "title": "Paper",
                "block_count": 10,
                "sections": [{"index": 0, "title": "Title"}],
            }
        ],
    )
    assert result["plan"] == "1. read(paper, 0, 4)"
    assert seen[0][2:] == (True, True) and seen[0][1] == []
    monkeypatch.setattr(
        chat_agent, "chat_with_tools", lambda *args, **kwargs: {"content": "", "tool_calls": []}
    )
    with pytest.raises(RuntimeError, match="no visible plan"):
        chat_agent._execute("plan", {"question": "Compare the methods"}, run, [])
    with pytest.raises(ValueError, match="English"):
        chat_agent._execute("search_bm25", {"query": "模型架构"}, run, [])
    with pytest.raises(ValueError, match="selected"):
        chat_agent._execute("read", {"paper_id": "other", "start": 0, "length": 1}, run, [])


def test_history_trimming_preserves_tool_pairs():
    messages = [{"role": "system", "content": "instructions"}, {"role": "user", "content": "old"}]
    messages += [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": "call", "function": {"name": "read", "arguments": "{}"}}],
        },
        {"role": "tool", "tool_call_id": "call", "content": "X" * 6000},
    ]
    messages += [{"role": "user", "content": "current question"}]
    trimmed = chat_agent._trim_messages(messages, budget=200)
    assert trimmed[-1]["content"] == "current question"
    assert not any(message["role"] == "tool" for message in trimmed)


def test_elasticsearch_indexes_once_and_searches_persisted_passages(monkeypatch):
    monkeypatch.setattr(
        lexical,
        "settings",
        lambda: SimpleNamespace(elasticsearch_url="http://es:9200", elasticsearch_api_key=""),
    )
    requests = []

    def fake_request(method, url, **kwargs):
        requests.append((method, url, kwargs))
        status = 404 if method == "HEAD" else 200
        body = (
            {
                "hits": {
                    "hits": [
                        {"_source": {"text": "encoder", "block_id": "b", "start": 0}, "_score": 2.5}
                    ]
                }
            }
            if url.endswith("_search")
            else {"errors": False}
        )
        import httpx

        return httpx.Response(status, json=body, request=httpx.Request(method, url))

    monkeypatch.setattr(lexical.httpx, "request", fake_request)
    lexical.index_passages(
        [
            {
                "id": "passage",
                "owner_id": "owner",
                "paper_id": "paper",
                "version_id": "v",
                "block_id": "b",
                "start": 0,
                "text": "encoder",
            }
        ]
    )
    hits = lexical.search("encoder", "owner", ["paper"], ["v"], 4)
    assert hits[0]["score"] == 2.5
    assert any("_bulk" in url for _, url, _ in requests)
    search_body = next(
        kwargs["json"] for method, url, kwargs in requests if url.endswith("_search")
    )
    assert search_body["query"]["bool"]["filter"][1] == {"terms": {"paper_id": ["paper"]}}
    assert not any(
        "_bulk" in url
        for method, url, kwargs in requests
        if method == "POST" and url.endswith("_search")
    )


def test_citation_numbers_use_only_real_evidence():
    source = _source()
    answer, sources = chat_agent.number_citations(f"Claim [{source['evidence_id']}]", [source])
    assert answer == "Claim [1]" and sources[0]["number"] == 1


def test_distinct_passages_in_one_block_keep_distinct_evidence_ids():
    first = _source()
    second = {**first, "evidence_id": str(uuid4()), "quote": "Another passage in this Block"}
    pool = {}
    chat_agent._merge_evidence(pool, [first, second, first])
    assert len(pool) == 2


def test_missing_citation_gets_a_react_correction_round(agent_db, monkeypatch):
    source = _source()
    seen = []
    monkeypatch.setattr(
        chat_agent,
        "paper_outline",
        lambda *args: [{"paper_id": "paper", "version_id": "version", "sections": []}],
    )

    def model(messages, tools, **kwargs):
        seen.append(messages)
        if len(seen) == 1:
            return {
                "content": "",
                "tool_calls": [
                    {
                        "id": "call",
                        "type": "function",
                        "function": {
                            "name": "read",
                            "arguments": '{"paper_id":"paper","start":0,"length":1}',
                        },
                    }
                ],
            }
        if len(seen) == 2:
            return {"content": "The method uses an encoder.", "tool_calls": []}
        return {
            "content": f"The method uses an encoder [{source['evidence_id']}].",
            "tool_calls": [],
        }

    monkeypatch.setattr(chat_agent, "chat_with_tools", model)
    monkeypatch.setattr(chat_agent, "read_paper_fragment", lambda *args: [source])
    run = SimpleNamespace(
        id="run",
        session_id=None,
        question="Which architecture?",
        paper_ids=["paper"],
        owner_id="owner",
    )
    answer, evidence = chat_agent.run_react(run, lambda *_: None)
    assert source["evidence_id"] in answer
    assert len(evidence) == 1
    assert "Internal citation check" in seen[2][-1]["content"]


def test_comparison_fetches_evidence_from_both_selected_papers(agent_db, monkeypatch):
    first, second = _source("p1"), _source("p2")
    seen = []
    monkeypatch.setattr(
        chat_agent,
        "paper_outline",
        lambda *args: [
            {"paper_id": "p1", "version_id": "version", "title": "First", "sections": []},
            {"paper_id": "p2", "version_id": "version", "title": "Second", "sections": []},
        ],
    )

    def call(tool, args):
        return {
            "id": str(uuid4()),
            "type": "function",
            "function": {"name": tool, "arguments": args},
        }

    def model(messages, tools, **kwargs):
        seen.append(messages)
        if len(seen) == 1:
            return {
                "content": "",
                "tool_calls": [call("search_bm25", '{"query":"encoder","paper_id":"p1"}')],
            }
        if len(seen) == 2:
            return {
                "content": f"First differs from second [{first['evidence_id']}].",
                "tool_calls": [],
            }
        if len(seen) == 3:
            return {
                "content": "",
                "tool_calls": [call("read", '{"paper_id":"p2","start":0,"length":1}')],
            }
        return {
            "content": f"First [{first['evidence_id']}] differs from second [{second['evidence_id']}].",
            "tool_calls": [],
        }

    monkeypatch.setattr(chat_agent, "chat_with_tools", model)
    monkeypatch.setattr(chat_agent, "search_bm25", lambda *args, **kwargs: [first])
    monkeypatch.setattr(chat_agent, "read_paper_fragment", lambda *args: [second])
    run = SimpleNamespace(
        id="run",
        session_id=None,
        question="Compare the two methods",
        paper_ids=["p1", "p2"],
        owner_id="owner",
    )
    answer, evidence = chat_agent.run_react(run, lambda *_: None)
    assert first["evidence_id"] in answer and second["evidence_id"] in answer
    assert len(evidence) == 2
    assert "Missing paper" in seen[2][-1]["content"]
