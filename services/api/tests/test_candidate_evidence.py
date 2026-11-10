import base64
from datetime import timedelta

import pytest

from services.api.app.models import PaperCandidate, now
from services.api.app.paper_discovery import discovery
from services.api.app.paper_discovery import resolve_paper_code as code
from services.api.app.paper_discovery.recommender import enrich_candidate, weighted_score


@pytest.fixture
def paper():
    return PaperCandidate(
        id="sample",
        title_key="sample",
        title="Sample Paper",
        source="arXiv",
        abstract="",
        authors=["Jane Doe"],
        arxiv_id="2607.05394",
        year=2026,
    )


def test_indirect_official_code(monkeypatch):
    def api(url, **kwargs):
        if "huggingface" in url:
            return {"projectPage": "https://example.org/project"}
        if "paperswithcode" in url:
            return {"repositories": [], "project_pages": []}
        if "api.github.com/repos/" in url:
            return {
                "full_name": "team/repo",
                "html_url": "https://github.com/team/repo",
                "stargazers_count": 42,
            }
        raise AssertionError(url)

    monkeypatch.setattr(code, "json_get", api)
    monkeypatch.setattr(code, "public_url", lambda _: True)
    monkeypatch.setattr(
        code,
        "_page",
        lambda url: (
            [("https://github.com/team/repo", "Code")]
            if url == "https://example.org/project"
            else []
        ),
    )
    result = code.resolve_paper_code(title="Sample Paper", abstract="", arxiv_id="2607.05394")
    assert result["repository"] == "https://github.com/team/repo"
    assert result["verification"] == "verified"
    assert result["stars"] == 42
    assert result["source"] == "official_project_page"


def test_direct_code_skips_pages(monkeypatch):
    monkeypatch.setattr(
        code,
        "json_get",
        lambda url, **kwargs: (
            {"repositories": [], "project_pages": []}
            if "paperswithcode" in url
            else {
                "full_name": "team/repo",
                "html_url": "https://github.com/team/repo",
                "stargazers_count": 0,
            }
            if "api.github.com" in url
            else {}
        ),
    )
    monkeypatch.setattr(code, "_page", lambda _: pytest.fail("unnecessary webpage fetch"))
    result = code.resolve_paper_code(
        title="Sample Paper", abstract="Code: https://github.com/team/repo.", arxiv_id="2607.05394"
    )
    assert result["verification"] == "verified" and result["stars"] == 0


def test_third_party_readme_is_probable(monkeypatch):
    def api(url, **kwargs):
        if url.endswith("/readme"):
            return {"content": base64.b64encode(b"Implements Sample Paper").decode()}
        return {
            "full_name": "other/reproduction",
            "html_url": "https://github.com/other/reproduction",
            "stargazers_count": 10000,
        }

    monkeypatch.setattr(code, "json_get", api)
    result = code._github(
        "https://github.com/other/reproduction",
        "Sample Paper",
        None,
        None,
        None,
        "community",
        False,
    )
    assert result["verification"] == "probable"
    candidate = PaperCandidate(
        title_key="sample",
        title="Sample Paper",
        source="arXiv",
        github_stars=10000,
        github_verification="probable",
    )
    assert (
        weighted_score(candidate, {"relevance_score": 50, "value_score": 50})[1]["github_stars"]
        is None
    )


def test_invalid_repository_and_private_urls(monkeypatch):
    assert code.repository_url("https://github.com/owner/repo/issues") is None
    assert code.repository_url("https://github.com/owner") is None
    assert not code.public_url("https://127.0.0.1/secret")
    assert not code.public_url("https://169.254.169.254/latest/meta-data")
    assert not code.public_url("http://example.org")


@pytest.mark.parametrize("mode", ["openalex", "doi", "arxiv", "title", "semantic"])
def test_citation_matching(monkeypatch, mode):
    work = {
        "id": "https://openalex.org/W123",
        "display_name": "Sample Paper",
        "doi": "https://doi.org/10.1/test",
        "publication_year": 2026,
        "authorships": [{"author": {"display_name": "Jane Doe"}}],
        "locations": [{"landing_page_url": "https://arxiv.org/abs/2607.05394"}],
        "cited_by_count": 0,
    }

    def fetch(url, **kwargs):
        if "semanticscholar" in url:
            return {
                "title": "Sample Paper",
                "year": 2026,
                "authors": [{"name": "Jane Doe"}],
                "externalIds": {"ArXiv": "2607.05394"},
                "citationCount": 7,
            }
        if mode == "semantic":
            return {"results": []}
        if mode == "openalex" and url.endswith("/W123"):
            return work
        if mode == "doi" and kwargs.get("params", {}).get("filter"):
            return {"results": [work]}
        if mode == "arxiv" and kwargs.get("params", {}).get("search") == "2607.05394":
            return {"results": [work]}
        if mode == "title" and kwargs.get("params", {}).get("search") == "Sample Paper":
            return {"results": [work]}
        return {"results": []}

    monkeypatch.setattr("services.api.app.paper_discovery.external_http.json_get", fetch)
    kwargs = {
        "openalex": {"openalex_id": "https://openalex.org/W123"},
        "doi": {"doi": "10.1/test"},
        "arxiv": {"arxiv_id": "2607.05394"},
        "title": {},
        "semantic": {"arxiv_id": "2607.05394"},
    }[mode]
    result = discovery.citation_metrics(
        title="Sample Paper",
        authors=["Jane Doe"],
        year=2026,
        doi=kwargs.get("doi"),
        arxiv_id=kwargs.get("arxiv_id"),
        openalex_id=kwargs.get("openalex_id"),
    )
    assert result["citation_count"] == (7 if mode == "semantic" else 0)
    assert result["citation_source"] == ("semantic_scholar" if mode == "semantic" else "openalex")


def test_false_title_match_is_rejected(monkeypatch):
    monkeypatch.setattr(
        "services.api.app.paper_discovery.external_http.json_get",
        lambda url, **kw: {
            "results": [
                {
                    "display_name": "Sample Paper",
                    "publication_year": 2010,
                    "authorships": [{"author": {"display_name": "Someone Else"}}],
                    "cited_by_count": 900,
                }
            ]
        },
    )
    assert (
        discovery.citation_metrics(
            None, None, title="Sample Paper", authors=["Jane Doe"], year=2026
        )["status"]
        == "not_found"
    )


def test_cache_and_stars_refresh(monkeypatch, paper):
    calls = {"citation": 0, "code": 0}

    def metrics(*args, **kwargs):
        calls["citation"] += 1
        return {"status": "found", "citation_count": 0, "citation_source": "openalex"}

    def resolve(**kwargs):
        calls["code"] += 1
        return {
            "status": "found",
            "repository": "https://github.com/team/repo",
            "stars": 2,
            "source": "paperswithcode",
            "verification": "verified",
        }

    monkeypatch.setattr("services.api.app.paper_discovery.recommender.citation_metrics", metrics)
    monkeypatch.setattr("services.api.app.paper_discovery.recommender.resolve_paper_code", resolve)
    assert enrich_candidate(paper)
    assert not enrich_candidate(paper)
    assert calls == {"citation": 1, "code": 1}
    paper.code_checked_at = now() - timedelta(days=2)
    paper.citation_updated_at = now() - timedelta(days=2)
    assert not enrich_candidate(paper)
    assert calls == {"citation": 2, "code": 2}


def test_http_retry_and_response_limit(monkeypatch):
    import httpx

    from services.api.app.paper_discovery import external_http

    attempts = 0

    def handler(request):
        nonlocal attempts
        attempts += 1
        return httpx.Response(
            429 if attempts == 1 else 200, json={"ok": True}, headers={"Retry-After": "0"}
        )

    real_client = httpx.Client
    monkeypatch.setattr(
        external_http.httpx,
        "Client",
        lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs),
    )
    monkeypatch.setattr(external_http, "public_url", lambda _: True)
    monkeypatch.setattr(external_http.time, "sleep", lambda _: None)
    assert external_http.json_get("https://example.org/api") == {"ok": True}
    assert attempts == 2
    with pytest.raises(external_http.ExternalError, match="too large"):
        external_http.get("https://example.org/api", max_bytes=2)


def test_no_code_and_error_cache(monkeypatch, paper):
    monkeypatch.setattr(
        "services.api.app.paper_discovery.recommender.citation_metrics",
        lambda *a, **kw: {"status": "not_found"},
    )
    monkeypatch.setattr(
        "services.api.app.paper_discovery.recommender.resolve_paper_code",
        lambda **kw: {"status": "error", "repository": None, "stars": None},
    )
    enrich_candidate(paper)
    assert paper.github_url is None and paper.github_stars is None
    assert paper.code_check_status == "error"
    from services.api.app.paper_discovery.recommender import enrichment_due

    assert not enrichment_due(paper)
    paper.code_checked_at = now() - timedelta(hours=2)
    assert enrichment_due(paper)


def test_existing_recommendation_score_recalculated_without_llm(monkeypatch):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session

    from services.api.app.models import Base, Recommendation
    from services.api.app.paper_discovery.recommender import refresh_candidate_scores

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        candidate = PaperCandidate(
            title_key="metric-paper",
            title="Metric Paper",
            source="arXiv",
            citation_count=1,
            github_stars=10,
            github_verification="verified",
        )
        db.add(candidate)
        db.flush()
        rec = Recommendation(
            owner_id="test",
            candidate_id=candidate.id,
            score=10,
            reasons={},
            topics=["x"],
            status="pending",
            relevance_score=80,
            value_score=70,
            summary="saved",
            reason="saved reason",
        )
        db.add(rec)
        db.flush()
        candidate.citation_count = 50
        refresh_candidate_scores(db, candidate)
        assert rec.score != 10
        assert rec.summary == "saved" and rec.reason == "saved reason"


def test_missing_code_remains_unknown(monkeypatch):
    from services.api.app.paper_discovery.external_http import ExternalError

    monkeypatch.setattr(
        code, "json_get", lambda *_args, **_kwargs: (_ for _ in ()).throw(ExternalError("HTTP 404"))
    )
    monkeypatch.setattr(code, "_page", lambda _url: [])
    result = code.resolve_paper_code(title="No Code", abstract="", arxiv_id="2607.05394")
    assert result["status"] == "not_found"
    assert result["repository"] is None and result["stars"] is None
