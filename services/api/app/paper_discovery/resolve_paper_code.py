"""Find and verify paper code with bounded, deterministic link discovery."""

import base64
import html
import logging
import re
from urllib.parse import urljoin, urlsplit, urlunsplit

from bs4 import BeautifulSoup

from ..config import settings
from .external_http import ExternalError, get, json_get, public_url

logger = logging.getLogger(__name__)
URL_RE = re.compile(r"https?://[^\s<>\"']+", re.I)
ARXIV_RE = re.compile(r"^\d{4}\.\d{4,5}$")
PROJECT_WORDS = re.compile(
    r"project\s*(page|website)|home\s*page|website|code|source|implementation|supplementary", re.I
)
BAD_REPOS = {
    "issues",
    "pull",
    "pulls",
    "actions",
    "wiki",
    "discussions",
    "releases",
    "tree",
    "blob",
}


def clean_url(value: str) -> str | None:
    if not isinstance(value, str):
        return None
    value = html.unescape(value).strip().rstrip(".,;:)]}>'\"")
    try:
        parsed = urlsplit(value)
        if parsed.scheme not in ("https", "http") or not parsed.hostname:
            return None
        if parsed.username or parsed.password:
            return None
        return urlunsplit(("https", parsed.netloc.lower(), parsed.path.rstrip("/"), "", ""))
    except ValueError:
        return None


def repository_url(value: str) -> str | None:
    url = clean_url(value)
    if not url:
        return None
    parsed = urlsplit(url)
    if parsed.hostname not in ("github.com", "www.github.com"):
        return None
    parts = parsed.path.strip("/").split("/")
    if len(parts) != 2 or any(not re.fullmatch(r"[\w.-]+", part) for part in parts):
        return None
    owner, repo = parts
    if owner.lower() in ("topics", "orgs", "users", "settings", "marketplace"):
        return None
    return f"https://github.com/{owner}/{repo.removesuffix('.git')}"


def links(document: str, base: str) -> list[tuple[str, str]]:
    soup = BeautifulSoup(document, "html.parser")
    found = []
    for anchor in soup.select("a[href]"):
        url = clean_url(urljoin(base, anchor["href"]))
        if url:
            found.append(
                (
                    url,
                    anchor.get_text(" ", strip=True)
                    + " "
                    + str(anchor.parent.get_text(" ", strip=True))[:120],
                )
            )
    for match in URL_RE.findall(soup.get_text(" ", strip=True)):
        url = clean_url(match)
        if url:
            found.append((url, "text"))
    return found


def _page(url: str) -> list[tuple[str, str]]:
    if not public_url(url):
        raise ExternalError("Blocked project page")
    response = get(url, max_bytes=600_000)
    if "html" not in response.headers.get("content-type", "").lower():
        return []
    return links(response.text, url)


def _github(
    url: str,
    title: str,
    arxiv_id: str | None,
    doi: str | None,
    project: str | None,
    source: str,
    official: bool,
) -> dict | None:
    repo = repository_url(url)
    if not repo:
        return None
    owner, name = repo.rsplit("/", 2)[-2:]
    headers = (
        {"Authorization": f"Bearer {settings().github_token}"} if settings().github_token else {}
    )
    data = json_get(f"https://api.github.com/repos/{owner}/{name}", headers=headers)
    if str(data.get("full_name") or "").casefold() != f"{owner}/{name}".casefold():
        return None
    canonical = repository_url(data.get("html_url", ""))
    if not canonical:
        return None
    stars = data.get("stargazers_count")
    if not isinstance(stars, int) or stars < 0:
        stars = None
    readme_match = False
    if not official:
        try:
            readme = json_get(
                f"https://api.github.com/repos/{owner}/{name}/readme", headers=headers
            )
            body = base64.b64decode(readme.get("content", "")).decode("utf-8", "ignore").casefold()
            markers = [arxiv_id, doi, project, title]
            readme_match = any(marker and marker.casefold() in body for marker in markers)
        except ExternalError:
            pass
    return {
        "repository": canonical,
        "stars": stars,
        "source": source,
        "verification": "verified" if official else "probable" if readme_match else "unverified",
        "description": data.get("description"),
        "homepage": data.get("homepage"),
    }


def resolve_paper_code(
    *,
    title: str,
    abstract: str,
    arxiv_id: str | None,
    doi: str | None = None,
    source_url: str | None = None,
) -> dict:
    """Return the strongest validated repository, project URL and check status."""
    repos: list[tuple[str, str, bool]] = []
    projects: list[tuple[str, bool]] = []
    errors = 0
    validated: dict[str, dict | None] = {}
    if arxiv_id and ARXIV_RE.fullmatch(arxiv_id):
        try:
            hf = json_get(f"https://huggingface.co/api/papers/{arxiv_id}")
            if repository_url(hf.get("githubRepo") or ""):
                repos.append((hf["githubRepo"], "huggingface", True))
            if clean_url(hf.get("projectPage") or ""):
                projects.append((clean_url(hf["projectPage"]), True))
        except ExternalError as exc:
            if "404" not in str(exc):
                errors += 1
        try:
            pwc = json_get(
                f"https://paperswithcode.co/api/v1/papers/arxiv/{arxiv_id}",
                params={"include_resources": "true"},
            )
            for item in (
                pwc.get("repositories") if isinstance(pwc.get("repositories"), list) else []
            ):
                if isinstance(item, dict) and repository_url(item.get("url") or ""):
                    repos.append((item["url"], "paperswithcode", item.get("is_official") is True))
            for item in (
                pwc.get("project_pages") if isinstance(pwc.get("project_pages"), list) else []
            ):
                if isinstance(item, dict) and clean_url(item.get("url") or ""):
                    projects.append((clean_url(item["url"]), item.get("is_official") is True))
        except ExternalError as exc:
            if "404" not in str(exc):
                errors += 1
    for value in URL_RE.findall(abstract + " " + (source_url or "")):
        repo = repository_url(value)
        if repo:
            repos.append((repo, "paper_abstract", True))
        elif PROJECT_WORDS.search(
            abstract[max(0, abstract.find(value) - 50) : abstract.find(value) + len(value) + 30]
        ):
            url = clean_url(value)
            if url:
                projects.append((url, True))

    # Direct repository evidence avoids unnecessary webpage requests.
    def validate_repos() -> list[dict]:
        nonlocal errors
        unique = {}
        for url, source, official in repos[:12]:
            repo = repository_url(url)
            if repo and (repo not in unique or official and not unique[repo][2]):
                unique[repo] = (url, source, official)
        for repo, (url, source, official) in unique.items():
            if repo in validated:
                if official and validated[repo] and validated[repo]["verification"] != "verified":
                    validated[repo]["verification"] = "verified"
                    validated[repo]["source"] = source
                continue
            try:
                validated[repo] = _github(
                    url,
                    title,
                    arxiv_id,
                    doi,
                    projects[0][0] if projects else None,
                    source,
                    official,
                )
            except ExternalError as exc:
                if "404" not in str(exc):
                    errors += 1
                validated[repo] = None
        return [item for item in validated.values() if item]

    checked = validate_repos()
    if not any(item["verification"] == "verified" for item in checked):
        if arxiv_id and ARXIV_RE.fullmatch(arxiv_id):
            for page in (f"https://arxiv.org/abs/{arxiv_id}", f"https://arxiv.org/html/{arxiv_id}"):
                try:
                    for url, label in _page(page):
                        if repository_url(url):
                            repos.append((url, "arxiv_page", True))
                        elif PROJECT_WORDS.search(label) and urlsplit(url).hostname not in (
                            "arxiv.org",
                            "export.arxiv.org",
                        ):
                            projects.append((url, True))
                except ExternalError as exc:
                    if "404" not in str(exc):
                        errors += 1
        checked = validate_repos()
    if not any(item["verification"] == "verified" for item in checked):
        seen_pages = set()
        for project, official in projects[:5]:
            if project in seen_pages:
                continue
            seen_pages.add(project)
            try:
                for url, _ in _page(project):
                    if repository_url(url):
                        repos.append(
                            (url, "official_project_page" if official else "project_page", official)
                        )
            except ExternalError:
                errors += 1
        checked = validate_repos()
    rank = {"verified": 2, "probable": 1, "unverified": 0}
    checked.sort(
        key=lambda item: (rank[item["verification"]], item["source"] == "paperswithcode"),
        reverse=True,
    )
    result = (
        checked[0]
        if checked
        else {"repository": None, "stars": None, "source": None, "verification": None}
    )
    result["project_page_url"] = projects[0][0] if projects else None
    result["status"] = "found" if checked else "error" if errors else "not_found"
    return result
