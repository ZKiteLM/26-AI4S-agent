"""Grounded paper outline generation with program-owned bibliography."""

import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor

from .ai import generate
from .citation_identity import normalize_identifier
from .copilot_idea import analyze_idea, complete_fields, normalize_fields
from .copilot_references import verify_reference
from .discovery import search_arxiv, search_external

logger = logging.getLogger(__name__)


def _key(item: dict) -> str:
    for namespace, field in (("doi", "doi"), ("arxiv", "arxiv_id"), ("openalex", "openalex_id")):
        value = normalize_identifier(namespace, item.get(field))
        if value:
            return f"{namespace}:{value}"
    return "title:" + re.sub(r"\W+", "", (item.get("title") or "").casefold())


def discover_references(
    idea: str, keywords: list[str] | None = None, seed_candidates: list[dict] | None = None
) -> tuple[list[dict], list[str], list[dict]]:
    terms = list(dict.fromkeys(keywords or []))[:8]
    if not terms:
        terms = [idea[:90]]
    found: dict[str, dict] = {}
    titles: set[str] = set()
    errors = []
    for item in seed_candidates or []:
        if item.get("title") and item.get("abstract"):
            found[_key(item)] = item
            titles.add(re.sub(r"\W+", "", item["title"].casefold()))
    for term in terms:
        for name, search in (
            ("OpenAlex", lambda query, limit: search_external(query, limit, sort=None)),
            ("arXiv", search_arxiv),
        ):
            try:
                for item in search(term, 5):
                    if item.get("title") and item.get("abstract"):
                        key = _key(item)
                        old = found.get(key)
                        title_key = re.sub(r"\W+", "", item["title"].casefold())
                        if title_key in titles and key not in found:
                            continue
                        if not old or len(item.get("abstract") or "") > len(
                            old.get("abstract") or ""
                        ):
                            found[key] = item
                            titles.add(title_key)
            except Exception as exc:
                errors.append(f"{name}: {type(exc).__name__}")
    # Ranking uses topic overlap and citation count, never publication date alone.
    words = {
        x
        for x in re.findall(r"[a-zA-Z]{4,}", (idea + " " + " ".join(terms)).casefold())
        if x not in {"with", "from", "that", "using"}
    }
    ranked = sorted(
        found.values(),
        key=lambda x: (
            len(
                words
                & set(
                    re.findall(
                        r"[a-zA-Z]{4,}", (x["title"] + " " + x.get("abstract", "")).casefold()
                    )
                )
            ),
            min(x.get("citation_count") or 0, 1000),
        ),
        reverse=True,
    )
    with ThreadPoolExecutor(max_workers=4) as executor:
        checked = list(executor.map(verify_reference, ranked[:8]))
    return [x for x in checked if x["verification"].startswith("verified")][:8], errors, checked


def _sentences(text: str) -> list[str]:
    return re.split(r"(?<=[.!?。])(?=\s)", text)


def review_claims(body: str, references: list[dict]) -> str:
    """Ask a separate model pass to reject citations not supported by abstracts."""
    pieces = _sentences(body)
    claims = []
    for index, piece in enumerate(pieces):
        ids = [int(x) for x in re.findall(r"\[R(\d+)\]", piece)]
        if ids:
            claims.append(
                {
                    "index": index,
                    "text": piece.strip(),
                    "sources": [
                        {
                            "id": n,
                            "title": references[n - 1]["title"],
                            "abstract": (references[n - 1].get("abstract") or "")[:1200],
                        }
                        for n in ids
                        if 0 < n <= len(references)
                    ],
                }
            )
    if not claims:
        return body
    try:
        raw = generate(
            'Return JSON {"unsupported_indices": [integer indices]}. Check each cited claim against its supplied title and abstract. Accept faithful paraphrases and source-attribution sentences even when wording differs. Reject only claims whose specific factual content is absent, contradicted, or overstated, especially quantitative and causal claims. Do not use outside knowledge.',
            json.dumps({"claims": claims}, ensure_ascii=False),
            max_tokens=400,
            json_mode=True,
        )
        rejected = {
            index
            for index in json.loads(raw).get("unsupported_indices") or []
            if isinstance(index, int) and 0 <= index < len(pieces)
        }
        return "".join(
            re.sub(r"\[R\d+\]", "[引用依据待核实]", piece) if index in rejected else piece
            for index, piece in enumerate(pieces)
        )
    except Exception as exc:
        logger.warning("Citation claim review failed: %s", type(exc).__name__)
        return re.sub(r"\[R\d+\]", "[引用依据待核实]", body)


def assemble_draft(body: str, references: list[dict]) -> dict:
    """Reject invalid IDs and number only verified references used in the body."""
    body = re.split(r"^#{1,3}\s+References\b", body, maxsplit=1, flags=re.I | re.M)[0].strip()
    numeric_citations = len(re.findall(r"\[\d+\]", body))
    body = re.sub(r"\[\d+\]", "[引用依据待核实]", body)
    allowed = {f"R{i}": item for i, item in enumerate(references, 1)}
    uncertain = []
    if numeric_citations:
        uncertain.append(f"{numeric_citations} 处数字引用缺少可核验的文献 ID")
    kept = []
    for sentence in _sentences(body):
        tokens = re.findall(r"\[(R\d+)\]", sentence)
        invalid = [token for token in tokens if token not in allowed]
        if invalid:
            sentence = re.sub(r"\[R\d+\]", "", sentence).rstrip() + " [引用依据待核实]"
            uncertain.append("无效引用：" + ", ".join(invalid))
        kept.append(sentence)
    body = "".join(kept)
    if not re.search(r"(?im)^##\s+(Abstract|Introduction|Related Work)\s*$", body):
        body = (
            "## Abstract\n\n[待完善]\n\n## Introduction\n\n"
            + body
            + "\n\n## Related Work\n\n[待补充可核验文献]"
        )
    else:
        for heading in ("Abstract", "Introduction", "Related Work"):
            if not re.search(rf"(?im)^##\s+{heading}\s*$", body):
                body += f"\n\n## {heading}\n\n[待完善]"
    used = list(dict.fromkeys(re.findall(r"\[(R\d+)\]", body)))
    numbered = {token: i for i, token in enumerate(used, 1)}
    body = re.sub(r"\[(R\d+)\]", lambda m: f"[{numbered[m.group(1)]}]", body)
    bibliography = []
    output = []
    for token in used:
        ref = allowed[token]
        i = numbered[token]
        identifier = (
            f"https://doi.org/{ref['doi']}"
            if ref.get("doi")
            else f"https://arxiv.org/abs/{ref['arxiv_id']}"
        )
        bibliography.append(
            f"[{i}] {', '.join(ref.get('authors') or [])}. {ref['title']} ({ref.get('year') or 'n.d.'}). {identifier}"
        )
        output.append({**ref, "number": i, "url": identifier})
    return {
        "markdown": body
        + "\n\n## References\n\n"
        + ("\n".join(bibliography) if bibliography else "暂无可核验且已被正文引用的文献。"),
        "references": output,
        "warnings": uncertain,
    }


def _recover_references(body: str, references: list[dict], initial: dict) -> dict:
    """Recover useful citations without turning unverified claims into evidence."""
    body = re.split(r"^#{1,3}\s+References\b", body, maxsplit=1, flags=re.I | re.M)[0].strip()
    catalog = "\n".join(
        f"[R{i}] {item['title']}. Abstract: {(item.get('abstract') or '')[:1200]}"
        for i, item in enumerate(references[:4], 1)
    )
    try:
        paragraph = generate(
            "Write only a concise Related Work paragraph in academic English. Every factual statement about a paper must be directly supported by its supplied abstract and cite its [R#] ID. Do not add headings, a bibliography, unverified methods, results, or claims about the proposed work. If no substantive claim is supported, return an empty string.",
            f"Verified sources:\n{catalog}",
            max_tokens=550,
        ).strip()
        paragraph = re.sub(r"(?i)^#{1,3}\s+Related Work\s*", "", paragraph).strip()
        if paragraph:
            reviewed = review_claims(paragraph, references)
            recovered = assemble_draft(body + "\n\n" + reviewed, references)
            if recovered["references"]:
                recovered["warnings"] = list(
                    dict.fromkeys(initial["warnings"] + recovered["warnings"])
                )
                recovered["warnings"].append(
                    "原草稿的引用未通过依据核验；已补充重新核验的相关工作段落"
                )
                return recovered
    except Exception as exc:
        logger.warning("Related-work citation recovery failed: %s", type(exc).__name__)
    # Bibliographic entries assert only verified identity, not unsupported findings.
    if not re.search(r"(?im)^##\s+Related Work\s*$", body):
        body += "\n\n## Related Work"
    notes = []
    for i, item in enumerate(references[:3], 1):
        title = item["title"].replace("\n", " ")
        notes.append(
            f"- “{title}” [R{i}] — verified source; its connection to this proposal needs review."
        )
    recovered = assemble_draft(
        body + "\n\n### Verified sources to review\n" + "\n".join(notes), references
    )
    recovered["warnings"] = list(dict.fromkeys(initial["warnings"] + recovered["warnings"]))
    recovered["warnings"].append("未找到可直接支持具体论断的摘要证据；已列出已核验文献供人工核读")
    return recovered


def create_draft(
    idea: str,
    fields: dict | None = None,
    *,
    review: str = "",
    seed_candidates: list[dict] | None = None,
) -> dict:
    preparation_warnings = []
    if fields is not None:
        structured = normalize_fields(fields)
    else:
        try:
            structured = analyze_idea(idea)
        except ValueError as exc:
            structured = normalize_fields({})
            preparation_warnings.append(f"IDEA 结构化分析未完成：{exc}")
        try:
            structured = complete_fields(idea, structured, review)
        except ValueError as exc:
            preparation_warnings.append(f"IDEA 缺失字段未补全：{exc}")
    references, errors, checked = discover_references(
        idea, structured["related_work_keywords"], seed_candidates
    )
    catalog = "\n".join(
        f"[R{i}] {x['title']} ({x.get('year')}). Abstract: {(x.get('abstract') or '')[:1200]}"
        for i, x in enumerate(references, 1)
    )
    body = generate(
        "Write only Markdown sections ## Abstract, ## Introduction, ## Related Work. Base the proposed research on the original IDEA and its structured fields. The review is background, not a source of bibliographic identity. Use only supplied verified abstracts for literature claims. Cite only [R1], [R2] etc supplied below; never copy citation numbers from the review, invent a paper, finding, or References section. If evidence is insufficient, write a limited uncited framework and mark claims for validation. Do not present expected contributions as completed experimental results.",
        f"Original IDEA: {idea}\nStructured fields: {json.dumps(structured, ensure_ascii=False)}\nSaved graph review (background only): {review[:7000]}\nVerified literature:\n{catalog or 'None verified. Do not cite any paper.'}",
        max_tokens=1800,
    )
    proposed_citations = len(re.findall(r"\[R\d+\]", body))
    body = review_claims(body, references)
    removed_citations = proposed_citations - len(re.findall(r"\[R\d+\]", body))
    result = assemble_draft(body, references)
    if references and not result["references"]:
        result = _recover_references(body, references, result)
    if removed_citations:
        result["warnings"].append(f"{removed_citations} 处原始引用未通过摘要依据校验")
    result["fields"] = structured
    result["warnings"].extend(preparation_warnings)
    result["warnings"].extend(errors)
    result["candidates"] = [
        {
            "title": x["title"],
            "source": x.get("source"),
            "verification": x["verification"],
            "verification_method": x.get("verification_method"),
            "failure_reason": x.get("failure_reason"),
            "doi": x.get("doi"),
            "arxiv_id": x.get("arxiv_id"),
        }
        for x in checked
    ]
    return result
