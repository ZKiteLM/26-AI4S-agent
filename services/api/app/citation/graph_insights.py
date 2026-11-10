"""One-pass graph review and evidence-grounded future-work screening."""

import json
import logging
import math
import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..ai import generate
from ..config import settings
from ..models import Block, GraphInsight, GraphView, Paper, Version, now
from ..paper_discovery.discovery import openalex_headers
from .citation_graph_service import serialize_view
from .citation_providers import _abstract as _openalex_abstract
from .citation_providers import request_json

RELATED = re.compile(r"related work|prior work|previous work|background", re.I)
FUTURE = re.compile(
    r"future|limitation|discussion|conclusion|outlook|open question|open problem|challenge",
    re.I,
)
MAX_CONTEXT_CHARS = 22_000
logger = logging.getLogger(__name__)


def _blocks(db: Session, paper: Paper | None) -> list[Block]:
    if not paper:
        return []
    version = db.get(Version, paper.preferred_version_id) if paper.preferred_version_id else None
    if not version or version.parse_status != "completed":
        version = db.scalar(
            select(Version)
            .where(Version.paper_id == paper.id, Version.parse_status == "completed")
            .order_by(Version.created_at.desc())
        )
    if not version:
        return []
    return db.scalars(
        select(Block).where(Block.version_id == version.id).order_by(Block.page_index, Block.order)
    ).all()


def _section(blocks: list[Block], pattern: re.Pattern, limit: int) -> str:
    lines = []
    for block in blocks:
        if block.type in ("heading", "page_number", "page_footnote", "figure", "table"):
            continue
        if any(pattern.search(part) for part in block.section_path):
            lines.append(block.markdown or block.text)
        if sum(map(len, lines)) >= limit:
            break
    return "\n".join(lines)[:limit]


def _paper_abstract(blocks: list[Block], fallback: str) -> str:
    if fallback:
        return fallback[:1100]
    return _section(blocks, re.compile(r"^abstract$", re.I), 1100)


def ranked_material(db: Session, view: GraphView, kind: str) -> list[dict]:
    graph = serialize_view(db, view)
    nodes = graph["nodes"]
    if not nodes:
        return []
    max_rank = max((node["pagerank"] for node in nodes), default=0) or 1
    max_degree = max((node["in_degree"] for node in nodes), default=0)
    max_citations = max((node["citation_count"] or 0 for node in nodes), default=0)

    def score(node):
        local = 0.65 * node["pagerank"] / max_rank
        local += 0.2 * math.log1p(node["in_degree"]) / math.log1p(max_degree or 1)
        if node["citation_count"] is None:
            return local / 0.85
        global_cited = math.log1p(node["citation_count"]) / math.log1p(max_citations or 1)
        return local + 0.15 * global_cited

    ordered = sorted(nodes, key=lambda node: (-score(node), node["title"].casefold()))
    papers = {
        p.work_id: p
        for p in db.scalars(
            select(Paper).where(
                Paper.owner_id == view.owner_id, Paper.work_id.in_([node["id"] for node in ordered])
            )
        )
    }
    material = []
    for node in ordered:
        paper = papers.get(node["id"])
        blocks = _blocks(db, paper)
        abstract = _paper_abstract(blocks, node["abstract"] or (paper.abstract if paper else ""))
        related = _section(blocks, RELATED, 1200)
        future = _section(blocks, FUTURE, 1400)
        if kind == "review" and not (abstract or related):
            continue
        if kind == "future" and not (abstract or future):
            continue
        material.append(
            {
                "work_id": node["id"],
                "paper_id": node["paper_id"],
                "title": node["title"],
                "year": node["year"],
                "doi": node["doi"],
                "arxiv_id": node["arxiv_id"],
                "openalex_id": node["openalex_id"],
                "score": round(score(node), 4),
                "pagerank": round(node["pagerank"], 4),
                "citation_count": node["citation_count"],
                "abstract": abstract,
                "related": related,
                "future": future,
            }
        )
        if len(material) == 20:
            break
    return material


def _context(items: list[dict], kind: str, review: str = "") -> tuple[str, list[dict]]:
    def render(chosen, field_cap: int | None = None):
        fields = ["abstract", "related"] if kind == "review" else ["abstract", "future"]
        return "\n\n".join(
            f"[{index}] {item['title']} ({item['year'] or 'year unknown'})\n"
            + "\n".join(
                f"{field}: {(item[field][:field_cap] if field_cap else item[field]) or 'not available'}"
                for field in fields
            )
            for index, item in enumerate(chosen, 1)
        )

    chosen = items[:20]
    budget = min(MAX_CONTEXT_CHARS, max(8000, (settings().ollama_num_ctx - 5000) * 2))
    if len(render(chosen)) + len(review) > budget:
        chosen = chosen[:10]
    context = render(chosen, 700 if len(chosen) <= 10 else None)
    return context, chosen


def _sources(items: list[dict]) -> list[dict]:
    return [
        {
            key: item[key]
            for key in (
                "work_id",
                "paper_id",
                "title",
                "year",
                "doi",
                "arxiv_id",
                "openalex_id",
                "score",
                "pagerank",
                "citation_count",
            )
        }
        for item in items
    ]


def _parsed_json(raw: str) -> dict:
    result = json.loads(raw.strip().removeprefix("```json").removesuffix("```").strip())
    if not isinstance(result, dict):
        raise TypeError("模型没有返回 JSON 对象")
    return result


def _novelty_search(query: str) -> list[dict]:
    data = request_json(
        "https://api.openalex.org/works",
        params={"search": query, "per_page": 5},
        headers=openalex_headers(),
    )
    if not isinstance(data.get("results"), list):
        raise TypeError("OpenAlex search returned no results list")
    return [
        {
            "title": item.get("display_name") or "",
            "year": item.get("publication_year"),
            "abstract": _openalex_abstract(item.get("abstract_inverted_index"))[:350],
            "url": item.get("id")
            if re.fullmatch(r"https://openalex\.org/W\d+", str(item.get("id", "")))
            else None,
        }
        for item in data.get("results") or []
    ]


def _close_title_match(query: str, title: str) -> bool:
    """Conservatively reject a direction when a retrieved title shares its core terms."""
    stop = {"based", "using", "method", "methods", "model", "models", "paper", "study"}

    def terms(value: str) -> set[str]:
        return {
            word.removesuffix("s")
            for word in re.findall(r"[a-z]{4,}", value.lower())
            if word not in stop
        }

    query_terms = terms(query)
    return len(query_terms) >= 2 and len(query_terms & terms(title)) >= max(
        2, math.ceil(len(query_terms) / 2)
    )


def generate_future(db: Session, view: GraphView) -> tuple[str, list[dict]]:
    review = db.scalar(
        select(GraphInsight).where(
            GraphInsight.graph_id == view.id,
            GraphInsight.kind == "review",
            GraphInsight.graph_version == view.version,
        )
    )
    review_text = review.content[:5000] if review else ""
    if review:
        review_text = re.sub(
            r"\[(\d+)\]",
            lambda match: (
                f"【{review.sources[int(match.group(1)) - 1]['title']}】"
                if 1 <= int(match.group(1)) <= len(review.sources)
                else match.group(0)
            ),
            review_text,
        )
    context, chosen = _context(ranked_material(db, view, "future"), "future", review_text)
    if not chosen:
        raise ValueError("当前图谱没有可用的摘要或已解析论文内容")
    system = (
        "你是科研方向分析员。根据给定综述、论文摘要及其 Future Work、Limitations、"
        "Discussion、Conclusion 等片段，最多提出 3 个具体、可检验的后续研究方案。"
        "必须提出明确的新机制或实验设计、说明相对已有工作的具体增量和可验证的评估方式。"
        "泛泛的研究目标（如提升性能、安全性或效率）、论文已经完成或明确提出的同一方案一律不要输出。"
        "若证据不足以提出具体方案，返回空 ideas。"
        '只返回 JSON：{"ideas":[{"title":...,"rationale":...,"method":...,'
        '"evaluation":...,"evidence":[1,2],'
        '"search_queries":["English query","English query"]}]}。'
        "每个方案须有来源编号；英文检索词应覆盖方案的关键机制，用于核对相近论文。"
    )
    db.commit()
    raw = generate(
        system,
        f"已有综述：\n{review_text or '未生成'}\n\n论文资料：\n{context}",
        max_tokens=2100,
        json_mode=True,
    )
    ideas = []
    for item in _parsed_json(raw).get("ideas", [])[:3]:
        if not isinstance(item, dict):
            continue
        evidence = [
            n for n in item.get("evidence", []) if isinstance(n, int) and 1 <= n <= len(chosen)
        ]
        queries = [
            q.strip()
            for q in item.get("search_queries", [])
            if isinstance(q, str) and 8 <= len(q.strip()) <= 120 and q.isascii()
        ][:2]
        if (
            item.get("title")
            and item.get("rationale")
            and item.get("method")
            and item.get("evaluation")
            and evidence
            and queries
        ):
            ideas.append(
                {
                    "title": str(item["title"])[:120],
                    "rationale": str(item["rationale"])[:500],
                    "method": str(item["method"])[:500],
                    "evaluation": str(item["evaluation"])[:350],
                    "evidence": evidence,
                    "queries": queries,
                }
            )
    if not ideas:
        return "根据当前论文资料，尚不能谨慎提出具体且可核查的新研究方向。", _sources(chosen)
    candidates = []
    for idea in ideas:
        try:
            found = [
                (query, paper) for query in idea["queries"] for paper in _novelty_search(query)
            ]
        except Exception:
            candidates.append({**idea, "status": "search_failed", "related_papers": []})
            continue
        related = []
        seen = set()
        for query, paper in found:
            title = paper.get("title", "").strip()
            if not title or title.casefold() in seen:
                continue
            seen.add(title.casefold())
            related.append({**paper, "keyword_match": _close_title_match(query, title)})
        related.sort(key=lambda paper: not paper["keyword_match"])
        candidates.append({**idea, "status": "unchecked", "related_papers": related[:8]})
    verifier = (
        "你是科研文献核查员。逐项比较候选研究方向与检索到的论文标题和摘要。"
        "只有研究问题、应用领域及关键方法确实接近，才标为高度相近；"
        "仅共享几个关键词、跨领域应用或同属宽泛主题都不算高度相近。"
        "不确定或论文缺少足够信息时不要标记。不要生成新方向或推断新颖性。"
        '仅返回 JSON：{"assessments":[{"candidate_index":0,"similar_paper_indices":[1,3]}]}。'
        "索引从 0 开始；每个候选都要返回，允许空列表。"
    )
    searchable = [candidate for candidate in candidates if candidate["status"] != "search_failed"]
    if searchable:
        try:
            payload = [
                {
                    "candidate_index": index,
                    "idea": {key: candidate[key] for key in ("title", "rationale", "method")},
                    "papers": [
                        {"title": paper["title"], "abstract": paper.get("abstract", "")[:250]}
                        for paper in candidate["related_papers"]
                    ],
                }
                for index, candidate in enumerate(searchable)
            ]
            verdict = _parsed_json(
                generate(
                    verifier,
                    json.dumps(payload, ensure_ascii=False),
                    max_tokens=600,
                    json_mode=True,
                )
            )
            for assessment in verdict.get("assessments", []):
                if not isinstance(assessment, dict):
                    continue
                index = assessment.get("candidate_index")
                if not isinstance(index, int) or not 0 <= index < len(searchable):
                    continue
                candidate = searchable[index]
                matches = assessment.get("similar_paper_indices", [])
                if not isinstance(matches, list):
                    continue
                matches = [
                    n
                    for n in matches
                    if isinstance(n, int) and 0 <= n < len(candidate["related_papers"])
                ]
                if matches:
                    candidate["status"] = "close"
                    candidate["related_papers"] = [
                        candidate["related_papers"][n] for n in dict.fromkeys(matches)
                    ][:3]
                else:
                    candidate["status"] = "possible"
        except Exception:
            logger.warning("Future-work similarity verification failed", exc_info=True)
    labels = {
        "close": "模型初筛发现可能高度相近的研究；是否属于同一问题与方法仍需核对全文",
        "possible": "未确认高度相近的研究；新颖性仍需核查",
        "unchecked": "相近研究核查未完成",
        "search_failed": "外部检索失败；无法判断是否已有相近研究",
    }
    lines = [
        (
            "以下是基于图谱论文提出的候选研究方向，并非已确认的新颖成果。"
            "相近程度由模型结合论文标题和摘要初筛，实施前请核对全文。"
        )
    ]
    for idea in candidates:
        cited = "、".join(f"[{n}]" for n in idea["evidence"])
        section = (
            f"### {idea['title']}\n\n**核查结果：**{labels[idea['status']]}。\n\n"
            f"{idea['rationale']}\n\n**方法：**{idea['method']}\n\n"
            f"**验证：**{idea['evaluation']}\n\n**图谱依据：**{cited}。"
        )
        if idea["related_papers"]:
            heading = (
                "可能高度相近的研究（待核对）"
                if idea["status"] == "close"
                else "检索到的论文（相关性未确认）"
            )
            section += f"\n\n**{heading}：**"
            for paper in idea["related_papers"][:3]:
                title = re.sub(r"[\[\]]", "", paper["title"])
                label = f"[{title}]({paper['url']})" if paper.get("url") else title
                year = f"（{paper['year']}）" if paper.get("year") else ""
                abstract = f" — {paper['abstract'][:220]}" if paper.get("abstract") else ""
                section += f"\n- {label}{year}{abstract}"
        elif idea["status"] != "search_failed":
            section += "\n\nOpenAlex 未返回相近论文；这不能证明该方向新颖。"
        lines.append(section)
    return "\n\n".join(lines), _sources(chosen)


def save_insight(
    db: Session, view: GraphView, kind: str, content: str, sources: list[dict]
) -> None:
    record = db.scalar(
        select(GraphInsight).where(GraphInsight.graph_id == view.id, GraphInsight.kind == kind)
    )
    if not record:
        record = GraphInsight(
            graph_id=view.id,
            kind=kind,
            graph_version=view.version,
            content=content,
            sources=sources,
        )
        db.add(record)
    else:
        record.graph_version = view.version
        record.content = content
        record.sources = sources
        record.updated_at = now()
