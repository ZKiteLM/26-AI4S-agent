"""Persistent Elasticsearch BM25 index for PDF passages."""

import json

import httpx

from .config import settings

INDEX = "paper_passages_v1"


def _request(method: str, path: str, *, data=None, ndjson: str | None = None) -> dict:
    cfg = settings()
    if not cfg.elasticsearch_url:
        raise RuntimeError("Elasticsearch is not configured")
    headers = (
        {"Authorization": f"ApiKey {cfg.elasticsearch_api_key}"}
        if cfg.elasticsearch_api_key
        else {}
    )
    if ndjson is not None:
        headers["Content-Type"] = "application/x-ndjson"
    response = httpx.request(
        method,
        cfg.elasticsearch_url.rstrip("/") + path,
        headers=headers,
        json=data if ndjson is None else None,
        content=ndjson,
        timeout=30,
    )
    response.raise_for_status()
    return response.json() if response.content else {}


def ensure_index() -> None:
    cfg = settings()
    if not cfg.elasticsearch_url:
        return
    headers = (
        {"Authorization": f"ApiKey {cfg.elasticsearch_api_key}"}
        if cfg.elasticsearch_api_key
        else {}
    )
    response = httpx.request(
        "HEAD", cfg.elasticsearch_url.rstrip("/") + f"/{INDEX}", headers=headers, timeout=10
    )
    if response.status_code == 200:
        return
    if response.status_code != 404:
        response.raise_for_status()
    _request(
        "PUT",
        f"/{INDEX}",
        data={
            "mappings": {
                "properties": {
                    "text": {"type": "text", "similarity": "BM25"},
                    "owner_id": {"type": "keyword"},
                    "paper_id": {"type": "keyword"},
                    "version_id": {"type": "keyword"},
                    "block_id": {"type": "keyword"},
                    "start": {"type": "integer"},
                    "page_index": {"type": "integer"},
                }
            }
        },
    )


def index_passages(passages: list[dict]) -> None:
    if not passages or not settings().elasticsearch_url:
        return
    ensure_index()
    for offset in range(0, len(passages), 100):
        rows = passages[offset : offset + 100]
        lines = []
        for row in rows:
            lines.append(json.dumps({"index": {"_id": row["id"]}}))
            lines.append(json.dumps(row, ensure_ascii=False))
        result = _request(
            "POST", f"/{INDEX}/_bulk?refresh=wait_for", ndjson="\n".join(lines) + "\n"
        )
        if result.get("errors"):
            raise RuntimeError("Elasticsearch bulk indexing failed")


def remove_version(version_id: str) -> None:
    if not settings().elasticsearch_url:
        return
    ensure_index()
    result = _request(
        "POST",
        f"/{INDEX}/_delete_by_query?refresh=true&conflicts=proceed",
        data={"query": {"term": {"version_id": version_id}}},
    )
    if result.get("failures"):
        raise RuntimeError("Elasticsearch index deletion failed")


def search(
    query: str, owner_id: str, paper_ids: list[str], version_ids: list[str], limit: int
) -> list[dict]:
    if not settings().elasticsearch_url:
        raise RuntimeError("Elasticsearch is not configured")
    result = _request(
        "POST",
        f"/{INDEX}/_search",
        data={
            "size": limit,
            "query": {
                "bool": {
                    "must": [{"match": {"text": query}}],
                    "filter": [
                        {"term": {"owner_id": owner_id}},
                        {"terms": {"paper_id": paper_ids}},
                        {"terms": {"version_id": version_ids}},
                    ],
                }
            },
        },
    )
    return [{**hit["_source"], "score": hit["_score"]} for hit in result["hits"]["hits"]]
