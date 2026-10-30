import hashlib
import json
import logging
import re
import zipfile
from collections.abc import Callable
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory

import httpx
import pymupdf

from .config import settings

ARXIV_RE = re.compile(r"(?<!\d)(\d{4}\.\d{4,5})(?:v(\d+))?(?!\d)")
logger = logging.getLogger(__name__)


def identify_pdf(data: bytes, filename: str) -> dict:
    doc = pymupdf.open(stream=data, filetype="pdf")
    sample = " ".join(doc[i].get_text() for i in range(min(len(doc), 5)))
    title = (doc.metadata or {}).get("title") or ""
    first_lines = [x.strip() for x in sample.splitlines() if x.strip()]
    if not title or title.lower() in {"untitled", "microsoft word"}:
        title = first_lines[0] if first_lines else Path(filename).stem
    match = ARXIV_RE.search(filename) or ARXIV_RE.search(sample[:2000])
    normalized = re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", sample.lower())).strip()
    shingles = {normalized[i : i + 60] for i in range(0, max(len(normalized) - 59, 0), 30)}
    fingerprint = hashlib.sha256("|".join(sorted(shingles)).encode()).hexdigest()
    return {
        "title": title[:500],
        "arxiv_id": match.group(1) if match else None,
        "label": f"v{match.group(2)}" if match and match.group(2) else "unknown",
        "page_count": len(doc),
        "fingerprint": fingerprint,
        "sample_text": normalized[:12000],
        "shingles": shingles,
    }


def similarity(a: str, b: str) -> float:
    def tokens(text: str) -> set[str]:
        return set(re.findall(r"[a-z0-9]+|[\u4e00-\u9fff]", text.lower()))

    x, y = tokens(a), tokens(b)
    return len(x & y) / len(x | y) if x and y else 0.0


def _pymupdf_blocks(page: pymupdf.Page, page_index: int, start_order: int) -> list[dict]:
    output = []
    title_candidates = []
    data = page.get_text("dict", sort=True)
    width, height = page.rect.width, page.rect.height
    for raw in data["blocks"]:
        if "lines" not in raw:
            continue
        lines = raw["lines"]
        spans = [span for line in lines for span in line["spans"]]
        content = " ".join(span["text"].strip() for span in spans if span["text"].strip())
        if not content:
            continue
        kind = "paragraph"
        numbered_heading = re.match(r"^\s*(\d+(?:\.\d+){0,2})\s+[A-Za-z].{0,90}$", content)
        font_size = max(span["size"] for span in spans)
        if (
            page_index == 0
            and font_size >= 14
            and len(content) <= 180
            and raw["bbox"][1] < height / 2
            and not re.search(r"arxiv|permission|attribution|copyright|@", content, re.I)
        ):
            title_candidates.append((len(output), font_size))
        if re.match(
            r"^\s*(abstract|references|bibliography|appendix|摘要|参考文献)\s*$", content, re.I
        ) or (numbered_heading and font_size >= (9.5 if "." in numbered_heading.group(1) else 11)):
            kind = "heading"
        elif re.match(r"^\s*\[\d+\]", content):
            kind = "reference"
        x0, y0, x1, y1 = raw["bbox"]
        output.append(
            {
                "page_index": page_index,
                "order": start_order + len(output),
                "type": kind,
                "text": content,
                "markdown": content,
                "bbox": [x0 / width, y0 / height, x1 / width, y1 / height],
                "section_path": [],
                "parser": "pymupdf",
                "confidence": None,
            }
        )
    if page_index == 0 and start_order == 0 and output:
        index = max(title_candidates, key=lambda item: item[1])[0] if title_candidates else 0
        output[index]["type"] = "title"
    return output


def _mineru_blocks(pdf_data: bytes, start: int, end: int, ocr_mode: str = "ocr") -> list[dict]:
    cfg = settings()
    if cfg.parser_backend == "remote":
        if not cfg.mineru_api_url:
            raise RuntimeError("MINERU_API_URL is required for remote parsing")
        response = httpx.post(
            cfg.mineru_api_url.rstrip("/") + "/v1/file_parse",
            params={
                "tier": "basic",
                "ocr_mode": ocr_mode,
                "page_range": f"{start + 1}-{end}",
                "response_format": "zip",
                "wait_timeout": 600,
            },
            files={"files": ("document.pdf", pdf_data, "application/pdf")},
            headers={"Authorization": f"Bearer {cfg.mineru_api_key}"} if cfg.mineru_api_key else {},
            timeout=650,
        )
        response.raise_for_status()
        if response.status_code == 202:
            raise RuntimeError("Remote MinerU did not finish within 600 seconds")
        with zipfile.ZipFile(BytesIO(response.content)) as archive:
            name = next(
                (x for x in archive.namelist() if x.endswith("structured_content.json")), None
            )
            if not name:
                raise RuntimeError("MinerU response has no structured_content.json")
            content = json.loads(archive.read(name))
    elif cfg.parser_backend == "local":
        try:
            from mineru.parser import parse
        except ImportError as exc:
            raise RuntimeError(
                "Scanned PDF needs the parser extra: uv sync --extra parser"
            ) from exc
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "document.pdf"
            path.write_bytes(pdf_data)
            result = parse(path, tier="basic", ocr_mode=ocr_mode, page_range=f"{start + 1}-{end}")
            content = result.structured_content()
    else:
        raise RuntimeError(f"Unknown parser backend: {cfg.parser_backend}")
    output = []
    for page in content.get("pages", []):
        index = page.get("page_idx", start)
        for item in page.get("blocks", []):
            raw = item.get("content", "")
            text = raw if isinstance(raw, str) else str(raw)
            if not text.strip():
                continue
            kind = item.get("type", "text")
            kind = {
                "text": "paragraph",
                "doc_title": "title",
                "paragraph_title": "heading",
                "ref_text": "reference",
            }.get(kind, kind)
            output.append(
                {
                    "page_index": index,
                    "order": len(output),
                    "type": kind,
                    "text": text,
                    "markdown": text,
                    "latex": text if kind == "equation" else None,
                    "bbox": item.get("bbox") or [0, 0, 1, 1],
                    "section_path": [],
                    "parser": "mineru",
                    "confidence": None,
                }
            )
    return output


def parse_pdf(data: bytes, progress: Callable[[int, int], None] | None = None) -> list[dict]:
    doc = pymupdf.open(stream=BytesIO(data), filetype="pdf")
    enhance_vector = settings().parser_vector_enhance and len(doc) <= 50
    blocks = []
    current_section: list[str] = []
    for start in range(0, len(doc), 10):
        end = min(start + 10, len(doc))
        page_blocks_by_index = {}
        scanned_pages = []
        for page_index in range(start, end):
            page = doc[page_index]
            page_blocks = _pymupdf_blocks(page, page_index, len(blocks))
            image_coverage = sum(
                rect.get_area()
                for image in page.get_images()
                for rect in page.get_image_rects(image)
            ) / max(page.rect.get_area(), 1)
            if len("".join(x["text"] for x in page_blocks)) < 40 and image_coverage > 0.5:
                scanned_pages.append(page_index)
            else:
                page_blocks_by_index[page_index] = page_blocks
        if enhance_vector and not scanned_pages:
            try:
                enhanced = _mineru_blocks(data, start, end, ocr_mode="auto")
                for page_index in range(start, end):
                    located = [block for block in enhanced if block["page_index"] == page_index]
                    if located:
                        page_blocks_by_index[page_index] = located
            except Exception:
                logger.exception("Vector layout enhancement failed for pages %s-%s", start + 1, end)
        if scanned_pages:
            ocr_blocks = _mineru_blocks(data, min(scanned_pages), max(scanned_pages) + 1)
            for page_index in scanned_pages:
                page_blocks_by_index[page_index] = [
                    block for block in ocr_blocks if block["page_index"] == page_index
                ]
        for page_index in range(start, end):
            for block in page_blocks_by_index.get(page_index, []):
                if block["type"] == "heading":
                    numbered = re.match(r"^\s*(\d+(?:\.\d+)*)\s", block["text"])
                    level = numbered.group(1).count(".") + 1 if numbered else 1
                    current_section = current_section[: level - 1] + [block["text"]]
                block["section_path"] = current_section.copy()
                blocks.append(block)
        if progress:
            progress(end, len(doc))
    return blocks
