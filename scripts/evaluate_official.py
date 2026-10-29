"""Run the supplied PDF parsing acceptance cases and print a reproducible summary."""

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from services.api.app.parsing import identify_pdf, parse_pdf

SAMPLES = ROOT / "科研小助手" / "pdf"


def main() -> None:
    required = [
        "1706.03762v7.pdf",
        "1706.03762v1.pdf",
        "1706.03762v1_img.pdf",
        "UnderstandingDeepLearning_02_09_26_C.pdf",
    ]
    missing = [name for name in required if not (SAMPLES / name).exists()]
    if missing:
        raise SystemExit(f"Missing official samples: {missing}")
    results = {}
    identities = {}
    for name in required:
        data = (SAMPLES / name).read_bytes()
        identity = identify_pdf(data, name)
        identities[name] = identity
        started = time.monotonic()
        try:
            blocks = parse_pdf(data)
            results[name] = {
                "status": "completed",
                "pages": identity["page_count"],
                "blocks": len(blocks),
                "characters": sum(len(x["text"]) for x in blocks),
                "heading_blocks": sum(x["type"] == "heading" for x in blocks),
                "equation_blocks": sum(x["type"] == "equation" for x in blocks),
                "table_blocks": sum(x["type"] == "table" for x in blocks),
                "normalized_bbox_ratio": round(
                    sum(
                        len(x["bbox"]) == 4 and all(0 <= value <= 1 for value in x["bbox"])
                        for x in blocks
                    )
                    / max(len(blocks), 1),
                    3,
                ),
                "located_bbox_ratio": round(
                    sum(
                        x["bbox"] != [0, 0, 1, 1]
                        and (x["bbox"][2] - x["bbox"][0]) < 0.98
                        and (x["bbox"][3] - x["bbox"][1]) < 0.98
                        for x in blocks
                    )
                    / max(len(blocks), 1),
                    3,
                ),
                "parser_counts": {
                    parser: sum(x["parser"] == parser for x in blocks)
                    for parser in {x["parser"] for x in blocks}
                },
                "seconds": round(time.monotonic() - started, 2),
            }
        except Exception as exc:
            results[name] = {
                "status": "failed",
                "error": str(exc),
                "seconds": round(time.monotonic() - started, 2),
            }
        print(json.dumps({name: results[name]}, ensure_ascii=False), flush=True)
    results["version_identity"] = {
        "same_paper": identities[required[0]]["arxiv_id"] == identities[required[1]]["arxiv_id"],
        "version_order": [identities[required[0]]["label"], identities[required[1]]["label"]],
    }
    print(json.dumps(results["version_identity"], ensure_ascii=False))
    path = ROOT / "reports" / "official_evaluation.json"
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Saved {path}")


if __name__ == "__main__":
    main()
