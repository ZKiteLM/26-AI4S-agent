"""Identity checks for references used by the writing copilot."""

import re
from difflib import SequenceMatcher
from urllib.parse import quote

import httpx

from .citation_identity import normalize_identifier
from .citation_providers import OpenAlexProvider, request_json
from .discovery import search_arxiv


def _same_title(left: str, right: str) -> bool:
    clean = lambda value: re.sub(r"[^\w]+", "", value.casefold())
    return SequenceMatcher(None, clean(left), clean(right)).ratio() >= 0.9


def verify_reference(candidate: dict) -> dict:
    """Return evidence of identity; a service error remains distinct from no match."""
    result = dict(candidate)
    result.update(verification="unverified", verification_method=None, failure_reason=None)
    doi = normalize_identifier("doi", candidate.get("doi"))
    arxiv = normalize_identifier("arxiv", candidate.get("arxiv_id"))
    if doi:
        try:
            raw = request_json(f"https://api.crossref.org/works/{quote(doi, safe='/')}")
            work = raw.get("message") or {}
            title = (work.get("title") or [""])[0]
            if normalize_identifier("doi", work.get("DOI")) == doi and _same_title(
                title, candidate.get("title") or ""
            ):
                authors = [
                    " ".join(filter(None, (a.get("given"), a.get("family"))))
                    for a in work.get("author") or []
                ]
                second = OpenAlexProvider().resolve_paper(
                    {
                        "doi": doi,
                        "title": title,
                        "authors": authors or candidate.get("authors") or [],
                    }
                )
                method = (
                    "crossref_doi+openalex"
                    if second and normalize_identifier("doi", second.get("doi")) == doi
                    else "crossref_doi"
                )
                result.update(
                    verification="verified",
                    verification_method=method,
                    authors=authors or candidate.get("authors") or [],
                    doi=doi,
                )
                return result
            result["failure_reason"] = "Crossref DOI 与候选论文标题不一致"
        except httpx.HTTPStatusError as exc:
            result["failure_reason"] = f"Crossref HTTP {exc.response.status_code}"
        except (httpx.HTTPError, ValueError, TypeError, KeyError) as exc:
            result["failure_reason"] = f"Crossref 暂时不可用：{type(exc).__name__}"
    if arxiv:
        try:
            matches = search_arxiv(arxiv, 1)
            match = next(
                (
                    x
                    for x in matches
                    if normalize_identifier("arxiv", x.get("arxiv_id")) == arxiv
                    and _same_title(x.get("title") or "", candidate.get("title") or "")
                ),
                None,
            )
            if match:
                result.update(
                    verification="verified_preprint",
                    verification_method="arxiv_id",
                    authors=match.get("authors") or result.get("authors") or [],
                    abstract=match.get("abstract") or result.get("abstract") or "",
                    arxiv_id=arxiv,
                    doi=None,
                )
                return result
            result["failure_reason"] = "arXiv ID 与候选论文不匹配"
        except (httpx.HTTPError, ValueError, TypeError) as exc:
            result["failure_reason"] = f"arXiv 暂时不可用：{type(exc).__name__}"
    if not doi and not arxiv:
        result["failure_reason"] = "缺少 DOI 或 arXiv ID"
    return result
