"""Bounded OpenAlex citation queries with Semantic Scholar fallback."""

import logging
import time
from dataclasses import dataclass
from typing import Protocol

import httpx

from .citation_identity import normalize_identifier
from .config import settings
from .discovery import openalex_headers

logger = logging.getLogger(__name__)


@dataclass
class CitationPage:
    works: list[dict]
    next_cursor: str | None
    total_count: int | None
    provider: str


class CitationProvider(Protocol):
    name: str

    def resolve_paper(self, metadata: dict) -> dict | None: ...
    def get_paper(self, provider_id: str) -> dict | None: ...
    def get_references(self, provider_id: str, limit: int, cursor: str | None) -> CitationPage: ...
    def get_citations(self, provider_id: str, limit: int, cursor: str | None) -> CitationPage: ...
    def get_papers_batch(self, provider_ids: list[str]) -> list[dict]: ...


def request_json(url: str, *, params: dict | None = None, headers: dict | None = None) -> dict:
    for attempt in range(3):
        try:
            response = httpx.get(
                url, params=params, headers=headers, timeout=15, follow_redirects=False
            )
            if response.status_code in (429, 500, 502, 503, 504) and attempt < 2:
                retry_after = response.headers.get("retry-after", "")
                time.sleep(
                    min(5, float(retry_after)) if retry_after.isdigit() else 0.5 * 2**attempt
                )
                continue
            response.raise_for_status()
            data = response.json()
            if not isinstance(data, dict):
                raise TypeError("Invalid citation API response")
            return data
        except (httpx.RequestError, ValueError, TypeError):
            if attempt == 2:
                raise
            time.sleep(0.5 * 2**attempt)
    raise RuntimeError("Citation API request failed")


def _abstract(index: dict | None) -> str:
    if not isinstance(index, dict):
        return ""
    words = sorted((position, word) for word, positions in index.items() for position in positions)
    return " ".join(word for _, word in words)


def _openalex_metadata(raw: dict) -> dict:
    locations = raw.get("locations") or []
    arxiv = None
    for location in locations:
        for key in ("landing_page_url", "pdf_url"):
            url = location.get(key) or ""
            if "arxiv.org/" in url:
                arxiv = normalize_identifier("arxiv", url)
                if arxiv:
                    break
    return {
        "title": raw.get("display_name") or "",
        "authors": [
            a.get("author", {}).get("display_name", "") for a in raw.get("authorships") or []
        ],
        "year": raw.get("publication_year"),
        "abstract": _abstract(raw.get("abstract_inverted_index")),
        "doi": raw.get("doi"),
        "arxiv_id": arxiv,
        "openalex_id": raw.get("id"),
        "citation_count": raw.get("cited_by_count"),
    }


def _s2_metadata(raw: dict) -> dict:
    ids = raw.get("externalIds") or {}
    return {
        "title": raw.get("title") or "",
        "authors": [a.get("name", "") for a in raw.get("authors") or []],
        "year": raw.get("year"),
        "abstract": raw.get("abstract") or "",
        "doi": ids.get("DOI"),
        "arxiv_id": ids.get("ArXiv"),
        "s2_paper_id": raw.get("paperId"),
        "citation_count": raw.get("citationCount"),
    }


def _matching(candidate: dict, expected: dict) -> bool:
    import re
    from difflib import SequenceMatcher

    expected_arxiv = normalize_identifier("arxiv", expected.get("arxiv_id"))
    candidate_arxiv = normalize_identifier("arxiv", candidate.get("arxiv_id"))
    arxiv_agrees = bool(expected_arxiv and candidate_arxiv and expected_arxiv == candidate_arxiv)
    exact_identity = False
    for namespace, field in (("doi", "doi"), ("arxiv", "arxiv_id")):
        want = normalize_identifier(namespace, expected.get(field))
        got = normalize_identifier(namespace, candidate.get(field))
        if want and got and want != got and not (namespace == "doi" and arxiv_agrees):
            return False
        exact_identity = exact_identity or bool(want and got and want == got)
    title = expected.get("title") or ""
    if title and not exact_identity:
        clean = lambda value: re.sub(r"[^a-z0-9]", "", value.lower())
        if SequenceMatcher(None, clean(title), clean(candidate.get("title") or "")).ratio() < 0.92:
            return False
    if (
        not exact_identity
        and expected.get("year")
        and candidate.get("year")
        and abs(expected["year"] - candidate["year"]) > 1
    ):
        return False
    authors = {x.split()[-1].casefold() for x in expected.get("authors") or [] if x}
    found = {x.split()[-1].casefold() for x in candidate.get("authors") or [] if x}
    if not exact_identity and authors and found and not authors.intersection(found):
        return False
    return bool(
        title
        or expected.get("doi")
        or expected.get("arxiv_id")
        or expected.get("openalex_id")
        or expected.get("s2_paper_id")
    )


class OpenAlexProvider:
    name = "openalex"
    base = "https://api.openalex.org/works"

    def _get(self, suffix: str = "", params: dict | None = None) -> dict:
        return request_json(self.base + suffix, params=params, headers=openalex_headers())

    def resolve_paper(self, metadata: dict) -> dict | None:
        key = normalize_identifier("openalex", metadata.get("openalex_id"))
        doi = normalize_identifier("doi", metadata.get("doi"))
        try:
            if key or doi:
                raw = self._get("/" + (key or "doi:" + doi))
                result = _openalex_metadata(raw)
                if key:
                    return (
                        result
                        if normalize_identifier("openalex", result.get("openalex_id")) == key
                        else None
                    )
                return result if _matching(result, metadata) else None
            if metadata.get("title"):
                data = self._get(
                    params={"filter": "title.search:" + metadata["title"], "per_page": 10}
                )
                for raw in data.get("results") or []:
                    result = _openalex_metadata(raw)
                    wanted_arxiv = normalize_identifier("arxiv", metadata.get("arxiv_id"))
                    same_arxiv = wanted_arxiv and result.get("arxiv_id") == wanted_arxiv
                    shared_author = bool(
                        set(result["authors"]) & set(metadata.get("authors") or [])
                    )
                    exact_title = (
                        result["title"].casefold().strip() == metadata["title"].casefold().strip()
                    )
                    if _matching(result, metadata) and (same_arxiv or shared_author or exact_title):
                        return result
        except (httpx.HTTPError, ValueError, TypeError):
            logger.exception("OpenAlex identity lookup failed")
        return None

    def get_paper(self, provider_id: str) -> dict | None:
        return _openalex_metadata(self._get("/" + provider_id))

    def get_papers_batch(self, provider_ids: list[str]) -> list[dict]:
        if not provider_ids:
            return []
        data = self._get(
            params={
                "filter": "openalex:" + "|".join(provider_ids[:50]),
                "per_page": min(len(provider_ids), 50),
            }
        )
        return [_openalex_metadata(x) for x in data.get("results") or []]

    def get_reference_ids(self, provider_id: str) -> list[str]:
        raw = self._get("/" + provider_id)
        if not isinstance(raw.get("referenced_works"), list):
            raise TypeError("OpenAlex response has no reference list")
        return [
            item
            for value in raw.get("referenced_works") or []
            if (item := normalize_identifier("openalex", value))
        ]

    def get_references(self, provider_id: str, limit: int, cursor: str | None) -> CitationPage:
        ids = self.get_reference_ids(provider_id)
        offset = int(cursor or 0)
        page = ids[offset : offset + limit]
        works = self.get_papers_batch(page)
        return CitationPage(
            works, str(offset + limit) if offset + limit < len(ids) else None, len(ids), self.name
        )

    def get_citations(self, provider_id: str, limit: int, cursor: str | None) -> CitationPage:
        raw = self._get(
            params={
                "filter": f"cites:{provider_id}",
                "per_page": limit,
                "cursor": cursor or "*",
                "sort": "publication_date:desc",
            }
        )
        return CitationPage(
            [_openalex_metadata(x) for x in raw.get("results") or []],
            (raw.get("meta") or {}).get("next_cursor"),
            (raw.get("meta") or {}).get("count"),
            self.name,
        )


class SemanticScholarProvider:
    name = "semantic_scholar"
    base = "https://api.semanticscholar.org/graph/v1/paper/"
    fields = "title,authors,year,abstract,externalIds,citationCount"

    def _get(self, suffix: str, params: dict | None = None) -> dict:
        key = settings().semantic_scholar_api_key
        return request_json(
            self.base + suffix, params=params, headers={"x-api-key": key} if key else {}
        )

    def _identifier(self, metadata: dict) -> str | None:
        for namespace, field, prefix in (
            ("arxiv", "arxiv_id", "ARXIV:"),
            ("doi", "doi", "DOI:"),
            ("s2", "s2_paper_id", ""),
        ):
            if value := normalize_identifier(namespace, metadata.get(field)):
                return prefix + value
        return None

    def resolve_paper(self, metadata: dict) -> dict | None:
        identifier = self._identifier(metadata)
        if not identifier:
            return None
        try:
            result = _s2_metadata(self._get(identifier, {"fields": self.fields}))
            return result if _matching(result, metadata) else None
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                return None
            raise

    def get_paper(self, provider_id: str) -> dict | None:
        return _s2_metadata(self._get(provider_id, {"fields": self.fields}))

    def get_papers_batch(self, provider_ids: list[str]) -> list[dict]:
        return [self.get_paper(x) for x in provider_ids[:20]]

    def _page(
        self, provider_id: str, direction: str, limit: int, cursor: str | None
    ) -> CitationPage:
        offset = int(cursor or 0)
        raw = self._get(
            provider_id + "/" + direction,
            {
                "fields": self.fields,
                "offset": offset,
                "limit": limit,
            },
        )
        field = "citedPaper" if direction == "references" else "citingPaper"
        works = [_s2_metadata(row[field]) for row in raw.get("data") or [] if row.get(field)]
        next_offset = raw.get("next")
        return CitationPage(
            works,
            str(next_offset) if next_offset is not None else None,
            raw.get("total"),
            self.name,
        )

    def get_references(self, provider_id: str, limit: int, cursor: str | None) -> CitationPage:
        return self._page(provider_id, "references", limit, cursor)

    def get_citations(self, provider_id: str, limit: int, cursor: str | None) -> CitationPage:
        return self._page(provider_id, "citations", limit, cursor)


def resolve_paper(metadata: dict) -> tuple[dict, str] | None:
    for provider in (OpenAlexProvider(), SemanticScholarProvider()):
        try:
            if found := provider.resolve_paper(metadata):
                return found, provider.name
        except (httpx.HTTPError, ValueError, TypeError):
            logger.exception("%s identity lookup failed", provider.name)
    return None


def provider_for(work, direction: str) -> tuple[CitationProvider, str]:
    if work.openalex_id:
        return OpenAlexProvider(), work.openalex_id
    if key := SemanticScholarProvider()._identifier(vars(work)):
        return SemanticScholarProvider(), key
    raise ValueError(f"No citation provider identifier for {direction}")
