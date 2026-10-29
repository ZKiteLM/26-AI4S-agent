"""Run a small, source-checked RAG regression against the official papers."""

import argparse
import json
import re
import time
from pathlib import Path

import httpx

from services.api.app.config import settings

CASES = [
    {
        "name": "adam_parameters",
        "titles": ["Attention Is All You Need"],
        "question": "这篇论文训练时 Adam 优化器的 β1 和 β2 分别是多少？",
        "terms": ["0.9", "0.98"],
    },
    {
        "name": "training_hardware",
        "titles": ["Attention Is All You Need"],
        "question": "Transformer 论文训练用了多少张 NVIDIA P100 GPU？",
        "terms": ["8"],
        "source_terms": ["P100"],
    },
    {
        "name": "cross_paper_attention",
        "titles": ["Attention Is All You Need", "Language Models are Few-Shot Learners"],
        "question": "对比这两篇论文使用的注意力架构，以及各自的研究目标。",
        "terms": [["attention", "注意力"], ["稀疏", "sparse"]],
    },
    {
        "name": "unsupported_question",
        "titles": ["Attention Is All You Need"],
        "question": "作者最喜欢的颜色是什么？",
        "refuse": True,
    },
]


def run_case(client: httpx.Client, case: dict, papers: dict[str, str], timeout: int) -> dict:
    paper_ids = [papers[title] for title in case["titles"]]
    response = client.post(
        "/api/v1/chat/runs", json={"question": case["question"], "paper_ids": paper_ids}
    )
    response.raise_for_status()
    run_id = response.json()["run_id"]
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        response = client.get(f"/api/v1/chat/runs/{run_id}")
        response.raise_for_status()
        run = response.json()
        if run["status"] in ("completed", "failed"):
            break
        time.sleep(1)
    else:
        return {"case": case["name"], "passed": False, "error": "timeout", "run_id": run_id}

    answer = run.get("answer") or ""
    cited_ids = set(re.findall(r"\[([0-9a-fA-F-]{36})\]", answer))
    cited = [item for item in run.get("evidence") or [] if item["evidence_id"] in cited_ids]
    cited_papers = {item["paper_id"] for item in cited}
    if case.get("refuse"):
        passed = run["status"] == "completed" and ("无法" in answer or "不足" in answer)
    else:
        normalized = answer.lower().replace("β₁", "β1").replace("β₂", "β2")
        terms = all(
            any(
                option.lower() in normalized
                for option in (term if isinstance(term, list) else [term])
            )
            for term in case["terms"]
        )
        source_text = " ".join(item["quote"] for item in cited).lower()
        source_terms = all(term.lower() in source_text for term in case.get("source_terms", []))
        passed = (
            run["status"] == "completed"
            and terms
            and source_terms
            and cited_papers == set(paper_ids)
        )
    return {
        "case": case["name"],
        "passed": passed,
        "status": run["status"],
        "answer": answer,
        "cited_papers": sorted(cited_papers),
        "evidence_pages": sorted({(item["paper_title"], item["page_index"] + 1) for item in cited}),
        "error": run.get("error"),
        "run_id": run_id,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--timeout", type=int, default=180, help="Seconds per question")
    parser.add_argument("--output", type=Path, default=Path("reports/rag_evaluation.json"))
    args = parser.parse_args()
    cfg = settings()
    with httpx.Client(base_url=args.base_url, timeout=30) as client:
        login = client.post(
            "/api/v1/auth/login", json={"email": cfg.demo_email, "password": cfg.demo_password}
        )
        login.raise_for_status()
        client.headers["Authorization"] = "Bearer " + login.json()["access_token"]
        library = client.get("/api/v1/papers")
        library.raise_for_status()
        papers = {item["title"]: item["id"] for item in library.json()["items"]}
        missing = {title for case in CASES for title in case["titles"]} - papers.keys()
        if missing:
            raise SystemExit(f"Import and parse the official papers first: {sorted(missing)}")
        results = []
        for case in CASES:
            result = run_case(client, case, papers, args.timeout)
            results.append(result)
            print(f"{case['name']}: {'PASS' if result['passed'] else 'FAIL'}")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps({"model": cfg.llm_model, "cases": results}, ensure_ascii=False, indent=2)
    )
    print(f"Report: {args.output}")
    if not all(result["passed"] for result in results):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
