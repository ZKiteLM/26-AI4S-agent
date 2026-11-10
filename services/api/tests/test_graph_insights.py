import json

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from services.api.app.citation import graph_insights as insights
from services.api.app.db import Base
from services.api.app.main import (
    delete_graph,
    get_graph_insights,
    get_graph_review_research,
    queue_graph_insight,
)
from services.api.app.models import (
    BackgroundTask,
    GraphInsight,
    GraphReviewRun,
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


def test_future_shows_ideas_and_related_work_with_review_status(monkeypatch):
    material = [
        {
            "work_id": "w1",
            "paper_id": None,
            "title": "Source Paper",
            "year": 2024,
            "doi": None,
            "arxiv_id": None,
            "openalex_id": "W1",
            "score": 0.9,
            "pagerank": 0.5,
            "citation_count": 10,
            "abstract": "Existing method.",
            "related": "",
            "future": "A limitation remains.",
        }
    ]
    monkeypatch.setattr(insights, "ranked_material", lambda *args: material)
    replies = iter(
        [
            json.dumps(
                {
                    "ideas": [
                        {
                            "title": "Existing idea",
                            "rationale": "Known",
                            "method": "Existing method",
                            "evaluation": "Benchmark",
                            "evidence": [1],
                            "search_queries": ["existing method"],
                        },
                        {
                            "title": "Candidate idea",
                            "rationale": "Specific test",
                            "method": "A new intervention",
                            "evaluation": "Controlled comparison",
                            "evidence": [1],
                            "search_queries": ["new experiment"],
                        },
                    ]
                }
            ),
            json.dumps(
                {
                    "assessments": [
                        {"candidate_index": 0, "similar_paper_indices": [0]},
                        {"candidate_index": 1, "similar_paper_indices": []},
                    ]
                }
            ),
        ]
    )
    monkeypatch.setattr(insights, "generate", lambda *args, **kwargs: next(replies))
    monkeypatch.setattr(
        insights,
        "_novelty_search",
        lambda query: (
            [{"title": "Existing Method", "abstract": "Known", "year": 2023}]
            if query == "existing method"
            else []
        ),
    )
    with database() as db:
        view = GraphView(owner_id="owner", config={})
        db.add(view)
        db.commit()
        answer, sources = insights.generate_future(db, view)
        assert "Candidate idea" in answer
        assert "Existing idea" in answer
        assert "Existing Method" in answer
        assert "高度相近的研究" in answer
        assert sources[0]["title"] == "Source Paper"


def test_future_does_not_publish_vague_goals(monkeypatch):
    monkeypatch.setattr(
        insights,
        "ranked_material",
        lambda *args: [
            {
                "work_id": "w1",
                "paper_id": None,
                "title": "Source Paper",
                "year": 2024,
                "doi": None,
                "arxiv_id": None,
                "openalex_id": "W1",
                "score": 1,
                "pagerank": 1,
                "citation_count": 1,
                "abstract": "Existing work.",
                "related": "",
                "future": "An open question.",
            }
        ],
    )
    monkeypatch.setattr(
        insights,
        "generate",
        lambda *args, **kwargs: json.dumps(
            {
                "ideas": [
                    {
                        "title": "Improve accuracy",
                        "rationale": "Improve the model",
                        "evidence": [1],
                        "search_queries": ["model accuracy"],
                    }
                ]
            }
        ),
    )
    with database() as db:
        view = GraphView(owner_id="owner", config={})
        db.add(view)
        db.commit()
        answer, _ = insights.generate_future(db, view)
        assert "尚不能谨慎提出" in answer


def test_close_existing_paper_title_blocks_novelty_claim():
    assert insights._close_title_match(
        "human feedback few-shot instruction following",
        "Training language models to follow instructions with human feedback",
    )
    assert not insights._close_title_match(
        "human feedback few-shot instruction following", "An unrelated vision benchmark"
    )


def test_future_shows_idea_when_external_paper_matches(monkeypatch):
    monkeypatch.setattr(
        insights,
        "ranked_material",
        lambda *args: [
            {
                "work_id": "w1",
                "paper_id": None,
                "title": "Source",
                "year": 2024,
                "doi": None,
                "arxiv_id": None,
                "openalex_id": "W1",
                "score": 1,
                "pagerank": 1,
                "citation_count": 1,
                "abstract": "Abstract.",
                "related": "",
                "future": "Open question.",
            }
        ],
    )
    replies = iter(
        [
            json.dumps(
                {
                    "ideas": [
                        {
                            "title": "Human feedback for instruction following",
                            "rationale": "Research gap",
                            "method": "An intervention",
                            "evaluation": "Controlled comparison",
                            "evidence": [1],
                            "search_queries": ["human feedback instruction following"],
                        }
                    ]
                }
            ),
            json.dumps({"assessments": [{"candidate_index": 0, "similar_paper_indices": [0]}]}),
        ]
    )
    monkeypatch.setattr(insights, "generate", lambda *args, **kwargs: next(replies))
    monkeypatch.setattr(
        insights,
        "_novelty_search",
        lambda query: [
            {
                "title": "Training language models to follow instructions with human feedback",
                "url": "https://openalex.org/W123",
            },
            {"title": "Enhancing Zero-shot Text-to-Speech Synthesis with Human Feedback"},
        ],
    )
    with database() as db:
        view = GraphView(owner_id="owner", config={})
        db.add(view)
        db.commit()
        answer, _ = insights.generate_future(db, view)
        assert "Human feedback for instruction following" in answer
        assert "高度相近的研究" in answer
        assert "Training language models to follow instructions with human feedback" in answer
        assert "https://openalex.org/W123" in answer
        assert "Text-to-Speech" not in answer


def test_graph_delete_removes_view_and_insights_but_preserves_papers_and_edges():
    with database() as db:
        a = ScholarlyWork(title="A")
        b = ScholarlyWork(title="B")
        db.add_all([a, b])
        db.flush()
        paper = Paper(owner_id="owner", title="A", work_id=a.id)
        graph = GraphView(owner_id="owner", config={})
        other = GraphView(owner_id="someone_else", config={})
        db.add_all([paper, graph, other])
        db.flush()
        db.add_all(
            [
                GraphViewNode(graph_id=graph.id, work_id=a.id),
                GraphInsight(
                    graph_id=graph.id, kind="review", graph_version=0, content="Summary", sources=[]
                ),
                ScholarlyCitationEdge(citing_work_id=a.id, cited_work_id=b.id, sources=["test"]),
            ]
        )
        db.commit()
        with pytest.raises(HTTPException) as forbidden:
            delete_graph(graph.id, "someone_else", db)
        assert forbidden.value.status_code == 404
        assert get_graph_insights(graph.id, "owner", db)["review"]["content"] == "Summary"
        assert delete_graph(graph.id, "owner", db) == {"deleted": True}
        assert db.get(GraphView, graph.id) is None
        assert db.get(GraphView, other.id) is not None
        assert db.get(Paper, paper.id) is not None
        assert len(db.scalars(select(ScholarlyWork)).all()) == 2
        assert len(db.scalars(select(ScholarlyCitationEdge)).all()) == 1


def test_insight_task_is_deduplicated_and_blocks_deletion_while_running(monkeypatch):
    monkeypatch.setattr("services.api.app.main.generate_graph_insight.delay", lambda *args: None)
    with database() as db:
        work = ScholarlyWork(title="Paper")
        view = GraphView(owner_id="owner", config={}, status="completed")
        db.add_all([work, view])
        db.flush()
        db.add(GraphViewNode(graph_id=view.id, work_id=work.id))
        db.commit()
        first = queue_graph_insight(view.id, "review", "owner", db)
        progress = get_graph_review_research(view.id, "owner", db)["run"]
        assert progress["task_id"] == first["task_id"]
        assert progress["phase"] == "planning"
        with pytest.raises(HTTPException):
            get_graph_review_research(view.id, "someone_else", db)
        again = queue_graph_insight(view.id, "review", "owner", db)
        assert first["task_id"] == again["task_id"]
        with pytest.raises(HTTPException) as active:
            delete_graph(view.id, "owner", db)
        assert active.value.status_code == 409
        db.get(BackgroundTask, first["task_id"]).status = "completed"
        db.commit()
        assert delete_graph(view.id, "owner", db) == {"deleted": True}
        assert not db.query(GraphReviewRun).filter_by(graph_id=view.id).count()


def test_failed_review_with_written_sections_resumes_checkpoint(monkeypatch):
    monkeypatch.setattr("services.api.app.main.generate_graph_insight.delay", lambda *args: None)
    with database() as db:
        work = ScholarlyWork(title="Paper")
        view = GraphView(owner_id="owner", config={}, status="completed")
        db.add_all([work, view])
        db.flush()
        db.add(GraphViewNode(graph_id=view.id, work_id=work.id))
        db.commit()
        first = queue_graph_insight(view.id, "review", "owner", db)
        run = db.scalar(select(GraphReviewRun).where(GraphReviewRun.graph_id == view.id))
        run.outline = {"sections": [{"id": "a", "title": "背景", "evidence_ids": []}]}
        run.sections = [{"id": "a", "title": "背景", "markdown": "正文", "status": "written"}]
        run.coverage = {"gaps": []}
        run.status = "failed"
        db.get(BackgroundTask, first["task_id"]).status = "failed"
        db.commit()

        second = queue_graph_insight(view.id, "review", "owner", db)
        db.refresh(run)
        assert second["task_id"] != first["task_id"]
        assert run.task_id == second["task_id"]
        assert run.status == "queued" and run.phase == "verifying"
        assert db.query(GraphReviewRun).filter_by(graph_id=view.id).count() == 1
