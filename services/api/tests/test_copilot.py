from threading import Barrier

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from services.api.app.db import Base
from services.api.app.models import GraphInsight, GraphView
from services.api.app.writing.charts import chart_data, inspect_csv, validate_intent
from services.api.app.writing.copilot_diagram import generate_mermaid
from services.api.app.writing.copilot_draft import (
    assemble_draft,
    create_draft,
    discover_references,
    review_claims,
)
from services.api.app.writing.copilot_graph import (
    future_ideas,
    graph_options,
    graph_writing_context,
)
from services.api.app.writing.copilot_idea import analyze_idea, complete_fields, normalize_fields
from services.api.app.writing.copilot_references import verify_reference


def test_csv_validation_and_caption():
    frame, overview = inspect_csv(b"epoch,score\n1,0.3\n2,0.5\n3,0.7\n")
    assert overview["row_count"] == 3
    rows, caption, spec = chart_data(frame, {"type": "line", "x": "epoch", "y": "score"})
    assert len(rows) == 3 and "0.3 to 0.7" in caption and spec["type"] == "line"
    with pytest.raises(ValueError, match="field"):
        validate_intent({"type": "line", "x": "missing", "y": "score"}, frame)
    with pytest.raises(ValueError, match="Unsupported"):
        validate_intent({"type": "3d", "x": "epoch", "y": "score"}, frame)
    with pytest.raises(ValueError):
        inspect_csv(b"epoch,score\n")
    with pytest.raises(ValueError, match="numeric"):
        f, _ = inspect_csv(b"name,label\na,x\nb,y\n")
        validate_intent({"type": "line", "x": "name", "y": "label"}, f)


def test_caption_recalculates_for_changed_metric():
    frame, _ = inspect_csv(b"step,accuracy,loss\n1,0.2,0.9\n2,0.8,0.1\n")
    _, caption, _ = chart_data(frame, {"type": "line", "x": "step", "y": "loss"})
    assert "loss decreased from 0.9 to 0.1" in caption
    assert "accuracy" not in caption


def test_reference_numbering_and_invalid_citations():
    refs = [
        {
            "title": "Transformer Attention",
            "abstract": "Transformer attention improves translation quality.",
            "authors": ["A Author"],
            "year": 2020,
            "doi": "10.1/test",
            "verification": "verified",
        }
    ]
    result = assemble_draft(
        "## Abstract\nTransformer attention improves translation quality [R1]. Unknown fact [R9].\n\n## Introduction\nText\n\n## Related Work\nText",
        refs,
    )
    assert "[1]" in result["markdown"] and "[R9]" not in result["markdown"]
    assert len(result["references"]) == 1
    assert result["markdown"].count("https://doi.org/") == 1
    assert "\n\n## Introduction" in result["markdown"]


def test_unsupported_claim_is_not_presented_as_verified(monkeypatch):
    monkeypatch.setattr(
        "services.api.app.writing.copilot_draft.generate",
        lambda *_args, **_kwargs: '{"unsupported_indices":[0]}',
    )
    text = review_claims(
        "Paper proves a causal effect [R1].",
        [{"title": "Paper", "abstract": "An observational survey."}],
    )
    assert "[R1]" not in text and "[引用依据待核实]" in text


def test_failed_claim_review_does_not_certify_citations(monkeypatch):
    monkeypatch.setattr(
        "services.api.app.writing.copilot_draft.generate",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("model unavailable")),
    )
    text = review_claims(
        "A claimed result [R1].", [{"title": "Paper", "abstract": "Unrelated abstract."}]
    )
    assert "[R1]" not in text and "[引用依据待核实]" in text


def test_paraphrased_citation_is_not_rejected_by_word_overlap():
    reference = {
        "title": "Graph Signals",
        "abstract": "We study networks using neighborhood representations.",
        "authors": ["A Author"],
        "arxiv_id": "2607.05394",
        "verification": "verified_preprint",
    }
    result = assemble_draft(
        "## Abstract\n\n## Introduction\n\n## Related Work\n\n"
        "Neighborhood representations help model connected entities [R1].",
        [reference],
    )
    assert len(result["references"]) == 1
    assert "connected entities [1]" in result["markdown"]


def test_all_rejected_citations_trigger_grounded_rewrite(monkeypatch):
    reference = {
        "title": "Visual Reasoning Benchmark",
        "abstract": "We introduce a benchmark for visual mathematical reasoning.",
        "authors": ["A Author"],
        "doi": "10.1000/visual",
        "verification": "verified",
    }
    monkeypatch.setattr(
        "services.api.app.writing.copilot_draft.discover_references",
        lambda *_args: ([reference], [], [reference]),
    )
    calls = iter(
        [
            (
                "## Abstract\n\n## Introduction\n\n## Related Work\n\n"
                "This benchmark proves all models are reliable [R1]."
            ),
            '{"unsupported_indices":[0]}',
            "The paper introduces a benchmark for visual mathematical reasoning [R1].",
            '{"unsupported_indices":[]}',
        ]
    )
    monkeypatch.setattr(
        "services.api.app.writing.copilot_draft.generate",
        lambda *_args, **_kwargs: next(calls),
    )
    result = create_draft("A sufficiently long visual reasoning IDEA", normalize_fields({}))
    assert "proves all models are reliable [1]" not in result["markdown"]
    assert "introduces a benchmark for visual mathematical reasoning [1]" in result["markdown"]
    assert len(result["references"]) == 1
    assert result["markdown"].count("https://doi.org/10.1000/visual") == 1


def test_failed_rewrite_lists_only_verified_sources(monkeypatch):
    reference = {
        "title": "Visual Reasoning Benchmark",
        "abstract": "We introduce a benchmark for visual mathematical reasoning.",
        "authors": ["A Author"],
        "doi": "10.1000/visual",
        "verification": "verified",
    }
    monkeypatch.setattr(
        "services.api.app.writing.copilot_draft.discover_references",
        lambda *_args: ([reference], [], [reference]),
    )
    calls = 0

    def model(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return (
                "## Abstract\n\n## Introduction\n\n## Related Work\n\n"
                "Unsupported claim [R1].\n\n## References\n\nInvented bibliography"
            )
        if calls == 2:
            return '{"unsupported_indices":[0]}'
        raise RuntimeError("model unavailable")

    monkeypatch.setattr("services.api.app.writing.copilot_draft.generate", model)
    result = create_draft("A sufficiently long visual reasoning IDEA", normalize_fields({}))
    assert "Unsupported claim [1]" not in result["markdown"]
    assert "### Verified sources to review" in result["markdown"]
    assert "Invented bibliography" not in result["markdown"]
    assert len(result["references"]) == 1
    assert any("人工核读" in warning for warning in result["warnings"])


def test_arxiv_identity_and_missing_doi(monkeypatch):
    paper = {
        "title": "A Verified Preprint",
        "arxiv_id": "2607.05394",
        "abstract": "Test",
        "authors": ["A"],
    }
    monkeypatch.setattr(
        "services.api.app.writing.copilot_references.search_arxiv",
        lambda identifier, limit: [paper],
    )
    assert verify_reference(paper)["verification"] == "verified_preprint"
    assert verify_reference({"title": "No ID"})["verification"] == "unverified"


def test_missing_doi_does_not_become_a_reference(monkeypatch):
    response = httpx.Response(
        404, request=httpx.Request("GET", "https://api.crossref.org/works/10.1000/missing")
    )

    def missing(_url):
        raise httpx.HTTPStatusError("not found", request=response.request, response=response)

    monkeypatch.setattr("services.api.app.writing.copilot_references.request_json", missing)
    result = verify_reference({"title": "Imaginary Work", "doi": "10.1000/missing"})
    assert result["verification"] == "unverified"
    assert "404" in result["failure_reason"]


def test_crossref_doi_title_verification(monkeypatch):
    monkeypatch.setattr(
        "services.api.app.writing.copilot_references.OpenAlexProvider.resolve_paper",
        lambda _self, _metadata: {"doi": "10.1000/real"},
    )
    monkeypatch.setattr(
        "services.api.app.writing.copilot_references.request_json",
        lambda _url: {
            "message": {
                "DOI": "10.1000/real",
                "title": ["Real Academic Paper"],
                "author": [{"given": "Jane", "family": "Doe"}],
            }
        },
    )
    result = verify_reference({"title": "Real Academic Paper", "doi": "10.1000/real"})
    assert result["verification"] == "verified"
    assert result["verification_method"] == "crossref_doi+openalex"
    assert result["authors"] == ["Jane Doe"]


def test_duplicate_discovery_and_service_error(monkeypatch):
    paper = {"title": "A Study", "doi": "10.1000/x", "abstract": "some abstract"}
    queries = []

    def search(term, _limit, **_kwargs):
        queries.append(term)
        return [paper]

    monkeypatch.setattr("services.api.app.writing.copilot_draft.search_external", search)
    monkeypatch.setattr(
        "services.api.app.writing.copilot_draft.search_arxiv",
        lambda term, limit: (_ for _ in ()).throw(RuntimeError("down")),
    )
    monkeypatch.setattr(
        "services.api.app.writing.copilot_draft.verify_reference",
        lambda item: {**item, "verification": "verified", "verification_method": "test"},
    )
    refs, errors, checked = discover_references("a study", ["term1", "term2"])
    assert len(refs) == len(checked) == 1 and len(errors) == 2
    assert queries == ["term1", "term2"]


def test_mermaid_repair_and_rejection(monkeypatch):
    monkeypatch.setattr(
        "services.api.app.writing.copilot_diagram.generate",
        lambda *args, **kwargs: "```mermaid\nflowchart LR\nA-->B\n```",
    )
    assert "A-->B" in generate_mermaid("A sends data to B", "bad", "parse failure")
    monkeypatch.setattr(
        "services.api.app.writing.copilot_diagram.generate",
        lambda *args, **kwargs: "flowchart LR\nclick A evil",
    )
    with pytest.raises(ValueError):
        generate_mermaid("A sends data to B")


def test_mermaid_normalizes_label_breaks_but_rejects_html(monkeypatch):
    monkeypatch.setattr(
        "services.api.app.writing.copilot_diagram.generate",
        lambda *args, **kwargs: "flowchart TD\nEval[准确率<br/>有效监督比例<br />训练开销]",
    )
    source = generate_mermaid("Draw evaluation metrics")
    assert "Eval[准确率 / 有效监督比例 / 训练开销]" in source
    assert "<" not in source

    monkeypatch.setattr(
        "services.api.app.writing.copilot_diagram.generate",
        lambda *args, **kwargs: "flowchart TD\nA[<script>alert(1)</script>]",
    )
    with pytest.raises(ValueError, match="Invalid or unsafe Mermaid source"):
        generate_mermaid("Draw evaluation metrics")


def test_reference_sources_search_in_parallel(monkeypatch):
    barrier = Barrier(2)

    def search(_query, _limit, **_kwargs):
        barrier.wait(timeout=5)
        return [{"title": "Relevant Paper", "abstract": "A study of the research idea.", "doi": "10.1/x"}]

    monkeypatch.setattr("services.api.app.writing.copilot_draft.search_external", search)
    monkeypatch.setattr("services.api.app.writing.copilot_draft.search_arxiv", search)
    monkeypatch.setattr(
        "services.api.app.writing.copilot_draft.verify_reference",
        lambda item: {**item, "verification": "verified"},
    )
    references, errors, _ = discover_references("research idea", ["research idea"])
    assert len(references) == 1 and errors == []


def test_idea_completion_keeps_existing_fields(monkeypatch):
    base = normalize_fields(
        {"research_problem": "Original problem", "related_work_keywords": ["graph learning"]}
    )
    monkeypatch.setattr(
        "services.api.app.writing.copilot_idea.generate",
        lambda *_args, **_kwargs: (
            '{"research_problem":"Changed","core_motivation":"New motive","solution":"","expected_contribution":"","related_work_keywords":["other"]}'
        ),
    )
    result = complete_fields("Original IDEA", base)
    assert result["research_problem"] == "Original problem"
    assert result["core_motivation"] == "New motive"
    assert result["related_work_keywords"] == ["graph learning"]


def test_idea_analysis_retries_truncated_json(monkeypatch):
    limits = []

    def model(_system, _prompt, **kwargs):
        limits.append(kwargs["max_tokens"])
        return (
            '{"research_problem":"An unclosed string'
            if len(limits) == 1
            else '{"research_problem":"A question","related_work_keywords":["graph retrieval"]}'
        )

    monkeypatch.setattr("services.api.app.writing.copilot_idea.generate", model)
    result = analyze_idea("A sufficiently long original research idea")
    assert result["research_problem"] == "A question"
    assert limits == [1200, 1800]


def test_graph_draft_survives_repeated_truncated_analysis(monkeypatch):
    monkeypatch.setattr(
        "services.api.app.writing.copilot_idea.generate",
        lambda *_args, **_kwargs: '{"research_problem":"unfinished',
    )
    monkeypatch.setattr(
        "services.api.app.writing.copilot_draft.discover_references", lambda *_args: ([], [], [])
    )
    monkeypatch.setattr(
        "services.api.app.writing.copilot_draft.generate",
        lambda *_args, **_kwargs: (
            "## Abstract\nLimited outline.\n\n## Introduction\nText.\n\n## Related Work\nNo verified sources."
        ),
    )
    result = create_draft("A sufficiently long graph IDEA", review="Saved review")
    assert "## References" in result["markdown"]
    assert any("结构化分析未完成" in warning for warning in result["warnings"])


def test_draft_uses_selected_related_work_keywords(monkeypatch):
    captured = {}

    def fake_discovery(idea, keywords, candidates):
        captured["keywords"] = keywords
        return [], [], []

    monkeypatch.setattr(
        "services.api.app.writing.copilot_draft.discover_references", fake_discovery
    )
    monkeypatch.setattr(
        "services.api.app.writing.copilot_draft.generate",
        lambda *_args, **_kwargs: (
            "## Abstract\nLimited draft.\n\n## Introduction\nText.\n\n## Related Work\nNo verified sources."
        ),
    )
    fields = normalize_fields(
        {
            "research_problem": "Question",
            "related_work_keywords": ["neural graph search", "graph retrieval"],
        }
    )
    result = create_draft("Original long research IDEA", fields)
    assert captured["keywords"] == ["neural graph search", "graph retrieval"]
    assert "## References" in result["markdown"]


def test_graph_idea_selection_and_owner_isolation(monkeypatch):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        view = GraphView(owner_id="writer@example.com", status="completed", version=2)
        db.add(view)
        db.flush()
        db.add_all(
            [
                GraphInsight(
                    graph_id=view.id,
                    kind="review",
                    graph_version=2,
                    content="Stored review [1].",
                    sources=[{"work_id": "work-1"}],
                ),
                GraphInsight(
                    graph_id=view.id,
                    kind="future",
                    graph_version=2,
                    content="Intro\n\n### Idea A\n\nA method.\n\n### Idea B\n\nAnother method.",
                    sources=[],
                ),
            ]
        )
        db.commit()
        monkeypatch.setattr(
            "services.api.app.writing.copilot_graph.ranked_material",
            lambda *_args: [{"work_id": "work-1", "title": "Paper", "abstract": "An abstract"}],
        )
        assert len(future_ideas("### Idea A\nBody")) == 1
        assert len(graph_options(db, "writer@example.com")) == 1
        context = graph_writing_context(db, "writer@example.com", view.id, 1)
        assert "Idea B" in context["idea"] and context["review"] == "Stored review [1]."
        assert len(context["candidates"]) == 1
        with pytest.raises(LookupError):
            graph_writing_context(db, "other@example.com", view.id, 0)
    engine.dispose()
