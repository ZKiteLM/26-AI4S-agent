import re
import xml.etree.ElementTree as ET
from datetime import date

import httpx

from .config import settings


def search_external(query: str, limit: int = 10, from_date: date | None = None) -> list[dict]:
    params = {"search": query, "per-page": min(limit, 25), "sort": "publication_date:desc"}
    if from_date:
        params["filter"] = f"from_publication_date:{from_date.isoformat()}"
    response = httpx.get(
        "https://api.openalex.org/works",
        params=params,
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


def openalex_metrics(doi: str | None, arxiv_id: str | None, openalex_id: str | None = None) -> dict:
    """Find an OpenAlex match for arXiv metadata without inventing a citation count."""
    if not doi and not arxiv_id and not openalex_id:
        return {}
    try:
        if openalex_id and re.fullmatch(r"https://openalex\.org/W\d+", openalex_id):
            response = httpx.get(
                "https://api.openalex.org/works/" + openalex_id.rsplit("/", 1)[-1],
                timeout=15,
            )
            response.raise_for_status()
            work = response.json()
            return {"openalex_id": work.get("id"), "citation_count": work.get("cited_by_count")}
        params = {"filter": f"doi:{doi}"} if doi else {"search": f"arxiv {arxiv_id}"}
        response = httpx.get(
            "https://api.openalex.org/works", params={**params, "per-page": 5}, timeout=15
        )
        response.raise_for_status()
        for work in response.json().get("results", []):
            ids = work.get("ids") or {}
            matched = (doi and (work.get("doi") or "").lower() == doi.lower()) or (
                arxiv_id and arxiv_id in str(ids.get("arxiv") or "")
            )
            if matched:
                return {"openalex_id": work.get("id"), "citation_count": work.get("cited_by_count")}
    except httpx.HTTPError:
        pass
    return {}


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


def inspect_github(text: str) -> dict:
    match = re.search(r"https?://github\.com/([\w.-]+)/([\w.-]+)", text)
    if not match:
        return {"repository": None, "verified": False}
    owner, repo = match.group(1), match.group(2).rstrip(".,;:)]}").removesuffix(".git")
    headers = (
        {"Authorization": f"Bearer {settings().github_token}"} if settings().github_token else {}
    )
    try:
        response = httpx.get(
            f"https://api.github.com/repos/{owner}/{repo}", headers=headers, timeout=15
        )
        response.raise_for_status()
        metadata = response.json()
        return {
            "repository": metadata.get("html_url"),
            "verified": True,
            "stars": metadata.get("stargazers_count", 0),
            "updated_at": metadata.get("updated_at"),
        }
    except httpx.HTTPError:
        return {"repository": f"https://github.com/{owner}/{repo}", "verified": False}
