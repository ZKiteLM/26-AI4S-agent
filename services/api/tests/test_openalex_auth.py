from types import SimpleNamespace

import httpx

from services.api.app import discovery


def test_openalex_search_uses_configured_key_in_header(monkeypatch):
    monkeypatch.setattr(discovery, "settings", lambda: SimpleNamespace(openalex_api_key="test-key"))
    captured = {}

    def fake_get(url, **kwargs):
        captured.update(kwargs)
        return httpx.Response(200, json={"results": []}, request=httpx.Request("GET", url))

    monkeypatch.setattr(discovery.httpx, "get", fake_get)
    assert discovery.search_external("agent") == []
    assert captured["headers"] == {"Authorization": "Bearer test-key"}
    assert "api_key" not in captured["params"]


def test_openalex_citation_uses_configured_key(monkeypatch):
    monkeypatch.setattr(discovery, "settings", lambda: SimpleNamespace(openalex_api_key="test-key"))
    captured = {}

    def fake_json_get(url, **kwargs):
        captured.update(kwargs)
        return {"id": "https://openalex.org/W123", "cited_by_count": 0}

    monkeypatch.setattr("services.api.app.external_http.json_get", fake_json_get)
    result = discovery.citation_metrics(None, None, "https://openalex.org/W123")
    assert result["citation_count"] == 0
    assert captured["headers"] == {"Authorization": "Bearer test-key"}
