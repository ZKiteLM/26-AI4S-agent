import re
import xml.etree.ElementTree as ET
from datetime import date

import httpx

from .config import settings


def openalex_headers() -> dict[str, str]:
    key = settings().openalex_api_key.strip()
    return {"Authorization": f"Bearer {key}"} if key else {}


def search_external(query: str, limit: int = 10, from_date: date | None = None) -> list[dict]:
    params = {"search": query, "per-page": min(limit, 25), "sort": "publication_date:desc"}
    if from_date:
        params["filter"] = f"from_publication_date:{from_date.isoformat()}"
    response = httpx.get(
        "https://api.openalex.org/works",
        params=params,
        headers=openalex_headers(),
        timeout=20,
    )
    response.raise_for_status()
    results = []
    for work in response.json().get("results", []):
        locations = work.get("best_oa_location") or {}
        ids = work.get("ids") or {}
        results.append(
            {
                "title": work.get("display_name", ""),
                "authors": [x["author"]["display_name"] for x in work.get("authorships", [])[:12]],
                "year": work.get("publication_year"),
                "published_at": work.get("publication_date"),
                "citation_count": work.get("cited_by_count"),
                "doi": work.get("doi"),
                "openalex_id": work.get("id"),
                "arxiv_id": _arxiv_id(ids.get("arxiv") or locations.get("landing_page_url") or ""),
                "pdf_url": locations.get("pdf_url"),
                "abstract": _abstract(work.get("abstract_inverted_index")),
                "referenced_works": work.get("referenced_works", []),
                "source": "OpenAlex",
                "source_url": work.get("id"),
            }
        )
    return results


def search_arxiv(query: str, limit: int = 10, from_date: date | None = None) -> list[dict]:
    identifier = _arxiv_id(query)
    params = {"max_results": min(limit, 25), "sortBy": "submittedDate", "sortOrder": "descending"}
    if identifier and query.strip() == identifier:
        params["id_list"] = identifier
    else:
        params["search_query"] = f'all:"{query.replace(chr(34), "")}"'
    response = httpx.get("https://export.arxiv.org/api/query", params=params, timeout=30)
    response.raise_for_status()
    root = ET.fromstring(response.content)
    atom = "{http://www.w3.org/2005/Atom}"
    papers = []
    for entry in root.findall(f"{atom}entry"):
        source_url = (entry.findtext(f"{atom}id") or "").strip()
        arxiv_id = _arxiv_id(source_url)
        if not arxiv_id:
            continue
        published = entry.findtext(f"{atom}published") or ""
        if from_date and published[:10] < from_date.isoformat():
            continue
        doi = entry.findtext("{http://arxiv.org/schemas/atom}doi")
        papers.append(
            {
                "title": " ".join((entry.findtext(f"{atom}title") or "").split()),
                "authors": [
                    x.findtext(f"{atom}name") or "" for x in entry.findall(f"{atom}author")
                ],
                "year": int(published[:4]) if published[:4].isdigit() else None,
                "doi": doi,
                "published_at": published[:10] or None,
                "citation_count": None,
                "openalex_id": None,
                "arxiv_id": arxiv_id,
                "pdf_url": f"https://arxiv.org/pdf/{arxiv_id}",
                "abstract": " ".join((entry.findtext(f"{atom}summary") or "").split()),
                "referenced_works": [],
                "source": "arXiv",
                "source_url": source_url,
            }
        )
    return papers


def _same_paper(
    work: dict, title: str | None, authors: list | None, year: int | None, *, exact_id: bool = False
) -> bool:
    from difflib import SequenceMatcher

    if title:
        normalize = lambda value: re.sub(r"[^\w]+", "", value.casefold())
        if (
            SequenceMatcher(
                None,
                normalize(work.get("display_name") or work.get("title") or ""),
                normalize(title),
            ).ratio()
            < 0.94
        ):
            return False
    work_year = work.get("publication_year") or work.get("year")
    if year and work_year and abs(year - work_year) > 1:
        return False
    listed = work.get("authorships") or work.get("authors") or []
    found = {
        ((x.get("author") or {}).get("display_name") or x.get("name") or "").split()[-1].casefold()
        for x in listed
        if isinstance(x, dict)
    }
    expected = {name.split()[-1].casefold() for name in authors or [] if name.strip()}
    return not expected or (not found and exact_id) or bool(found & expected)


def _doi(value: str | None) -> str | None:
    return value.lower().removeprefix("https://doi.org/").removeprefix("doi:") if value else None


def _arxiv_location(work: dict, arxiv_id: str) -> bool:
    urls = [
        work.get("primary_location"),
        work.get("best_oa_location"),
        *(work.get("locations") or []),
    ]
    for item in urls:
        if isinstance(item, dict):
            for key in ("landing_page_url", "pdf_url"):
                if _arxiv_id(item.get(key) or "") == arxiv_id and "arxiv.org" in (
                    item.get(key) or ""
                ):
                    return True
    return _arxiv_id(str((work.get("ids") or {}).get("arxiv") or "")) == arxiv_id


def citation_metrics(
    doi: str | None,
    arxiv_id: str | None,
    openalex_id: str | None = None,
    *,
    title: str | None = None,
    authors: list | None = None,
    year: int | None = None,
) -> dict:
    """Use one validated source; distinguish actual zero, no match and API errors."""
    from .external_http import ExternalError, json_get

    errors = 0
    normalized_doi = _doi(doi)
    queries = []
    if openalex_id and re.fullmatch(r"(?:https://openalex\.org/)?W\d+", openalex_id):
        queries.append(
            (f"https://api.openalex.org/works/{openalex_id.rsplit('/', 1)[-1]}", None, "id")
        )
    if normalized_doi:
        queries.append(
            (
                "https://api.openalex.org/works",
                {"filter": f"doi:{normalized_doi}", "per-page": 5},
                "doi",
            )
        )
    if arxiv_id:
        queries.append(
            ("https://api.openalex.org/works", {"search": arxiv_id, "per-page": 10}, "arxiv")
        )
    if title:
        queries.append(
            ("https://api.openalex.org/works", {"search": title, "per-page": 10}, "title")
        )
    for url, params, mode in queries:
        try:
            data = json_get(url, params=params, headers=openalex_headers())
            works = data.get("results", []) if mode != "id" else [data]
            for work in works:
                if not isinstance(work, dict):
                    continue
                match = (
                    mode == "id"
                    or mode == "doi"
                    and _doi(work.get("doi")) == normalized_doi
                    or mode == "arxiv"
                    and _arxiv_location(work, arxiv_id)
                    or mode == "title"
                    and _same_paper(work, title, authors, year)
                )
                if match and _same_paper(
                    work, title, authors, year, exact_id=mode in ("id", "doi", "arxiv")
                ):
                    count = work.get("cited_by_count")
                    if isinstance(count, int) and count >= 0:
                        return {
                            "citation_count": count,
                            "citation_source": "openalex",
                            "openalex_id": work.get("id"),
                            "status": "found",
                        }
        except ExternalError as exc:
            if "404" not in str(exc):
                errors += 1
    for identifier in ([f"ARXIV:{arxiv_id}"] if arxiv_id else []) + (
        [f"DOI:{normalized_doi}"] if normalized_doi else []
    ):
        try:
            data = json_get(
                f"https://api.semanticscholar.org/graph/v1/paper/{identifier}",
                params={"fields": "title,year,authors,externalIds,citationCount"},
            )
            identifiers = data.get("externalIds") or {}
            matches_id = (
                identifier.startswith("ARXIV:")
                and identifiers.get("ArXiv") == arxiv_id
                or identifier.startswith("DOI:")
                and _doi(identifiers.get("DOI")) == normalized_doi
            )
            if matches_id and _same_paper(data, title, authors, year, exact_id=True):
                count = data.get("citationCount")
                if isinstance(count, int) and count >= 0:
                    return {
                        "citation_count": count,
                        "citation_source": "semantic_scholar",
                        "status": "found",
                    }
        except ExternalError as exc:
            if "404" not in str(exc):
                errors += 1
    return {"status": "error" if errors else "not_found"}


def openalex_metrics(
    doi: str | None, arxiv_id: str | None, openalex_id: str | None = None, **kwargs
) -> dict:
    return citation_metrics(doi, arxiv_id, openalex_id, **kwargs)


def _arxiv_id(value: str) -> str | None:
    match = re.search(r"(\d{4}\.\d{4,5})", value)
    return match.group(1) if match else None


def _abstract(index: dict | None) -> str:
    if not index:
        return ""
    words = [""] * (1 + max((max(x) for x in index.values() if x), default=-1))
    for word, positions in index.items():
        for position in positions:
            words[position] = word
    return " ".join(words)


def safe_pdf_download(url: str) -> bytes:
    # Only known open-access PDF hosts; no redirects to private networks.
    allowed = ("arxiv.org", "export.arxiv.org", "static.arxiv.org", "cdn.openalex.org")
    with httpx.Client(follow_redirects=False, timeout=45) as client:
        current = url
        for _ in range(3):
            parsed = httpx.URL(current)
            if parsed.scheme != "https" or parsed.host not in allowed:
                raise ValueError("PDF host is not on the open-access allowlist")
            response = client.get(current)
            if response.status_code in (301, 302, 303, 307, 308):
                current = str(httpx.URL(current).join(response.headers["location"]))
                continue
            response.raise_for_status()
            if len(response.content) > 50_000_000 or not response.content.startswith(b"%PDF-"):
                raise ValueError("Remote file is too large or is not a PDF")
            return response.content
    raise ValueError("Too many redirects")
