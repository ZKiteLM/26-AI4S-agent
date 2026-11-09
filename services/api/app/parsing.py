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
SPLIT_SCIENTIFIC_CELLS_RE = re.compile(
    r"<td>(\s*[\d.]+\s*[·×]\s*)</td>\s*<td>(\s*10<sup>[-+]?\d+</sup>\s*)</td>", re.I
)
logger = logging.getLogger(__name__)


def identify_pdf(data: bytes, filename: str) -> dict:
    doc = pymupdf.open(stream=data, filetype="pdf")
    sample = "\n".join(doc[i].get_text() for i in range(min(len(doc), 5)))
    title = (doc.metadata or {}).get("title") or ""
    first_lines = [x.strip() for x in sample.splitlines() if x.strip()]
    if not title or title.lower() in {"untitled", "microsoft word"}:
        title = _first_page_title(doc[0]) or (first_lines[0] if first_lines else Path(filename).stem)
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


def _first_page_title(page: pymupdf.Page) -> str | None:
    candidates = []
    for block in page.get_text("dict", sort=True)["blocks"]:
        if "lines" not in block or block["bbox"][1] > page.rect.height * 0.6:
            continue
        spans = [span for line in block["lines"] for span in line["spans"]]
        text = " ".join(span["text"].strip() for span in spans if span["text"].strip())
        if (
            not 8 <= len(text) <= 180
            or re.search(r"arxiv|copyright|permission|attribution|license|@|^abstract$", text, re.I)
        ):
            continue
        size = max(span["size"] for span in spans)
        if size >= 14:
            candidates.append((size, -block["bbox"][1], text))
    return max(candidates)[2] if candidates else None


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
        spans = [span for line in raw["lines"] for span in line["spans"]]
        for part in _split_columns(spans, width):
            _append_pymupdf_block(
                part, output, title_candidates, page_index, start_order, width, height
            )
    if page_index == 0 and start_order == 0 and output:
        index = max(title_candidates, key=lambda item: item[1])[0] if title_candidates else 0
        output[index]["type"] = "title"
    output = _reading_order(output)
    for index, block in enumerate(output):
        block["order"] = start_order + index
    return output


def _split_columns(spans: list[dict], page_width: float) -> list[list[dict]]:
    left = [span for span in spans if span["bbox"][2] <= page_width * 0.46]
    right = [span for span in spans if span["bbox"][0] >= page_width * 0.54]
    if left and right and len(left) + len(right) == len(spans):
        return [left, right]
    return [spans]


def _append_pymupdf_block(
    spans: list[dict],
    output: list[dict],
    title_candidates: list[tuple[int, float]],
    page_index: int,
    start_order: int,
    width: float,
    height: float,
) -> None:
    content = " ".join(span["text"].strip() for span in spans if span["text"].strip())
    if not content:
        return
    kind = "paragraph"
    numbered_heading = re.match(r"^\s*(\d+(?:\.\d+){0,2})\s+[A-Za-z].{0,90}$", content)
    font_size = max(span["size"] for span in spans)
    if (
        page_index == 0
        and font_size >= 14
        and len(content) <= 180
        and min(span["bbox"][1] for span in spans) < height / 2
        and not re.search(r"arxiv|permission|attribution|copyright|@", content, re.I)
    ):
        title_candidates.append((len(output), font_size))
    if re.match(
        r"^\s*(abstract|references|bibliography|appendix|摘要|参考文献)\s*$", content, re.I
    ) or (numbered_heading and font_size >= (9.5 if "." in numbered_heading.group(1) else 11)):
        kind = "heading"
    elif re.match(r"^\s*\[\d+\]", content):
        kind = "reference"
    x0 = min(span["bbox"][0] for span in spans)
    y0 = min(span["bbox"][1] for span in spans)
    x1 = max(span["bbox"][2] for span in spans)
    y1 = max(span["bbox"][3] for span in spans)
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


def _reading_order(blocks: list[dict]) -> list[dict]:
    """Use the center gutter when PyMuPDF's top-to-bottom order interleaves two columns."""
    left = [block for block in blocks if block["bbox"][2] <= 0.52]
    right = [block for block in blocks if block["bbox"][0] >= 0.48]
    if len(left) < 3 or len(right) < 3:
        return blocks
    middle = [block for block in blocks if block not in left and block not in right]
    ordered = []
    previous_y = -1.0
    for separator in sorted(middle, key=lambda block: block["bbox"][1]):
        next_y = separator["bbox"][1]
        ordered.extend(
            sorted(
                (block for block in left if previous_y <= block["bbox"][1] < next_y),
                key=lambda block: (block["bbox"][1], block["bbox"][0]),
            )
        )
        ordered.extend(
            sorted(
                (block for block in right if previous_y <= block["bbox"][1] < next_y),
                key=lambda block: (block["bbox"][1], block["bbox"][0]),
            )
        )
        ordered.append(separator)
        previous_y = next_y
    ordered.extend(
        sorted(
            (block for block in left if block["bbox"][1] >= previous_y),
            key=lambda block: block["bbox"][1],
        )
    )
    ordered.extend(
        sorted(
            (block for block in right if block["bbox"][1] >= previous_y),
            key=lambda block: block["bbox"][1],
        )
    )
    return ordered


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
            kind = item.get("type", "text")
            kind = {
                "text": "paragraph",
                "doc_title": "title",
                "paragraph_title": "heading",
                "ref_text": "reference",
                "interline_equation": "equation",
                "equation_interline": "equation",
            }.get(kind, kind)
            raw = item.get("content", "")
            markdown = _mineru_markdown(raw)
            if kind == "table":
                markdown = _merge_split_scientific_cells(markdown)
            latex = None
            if kind == "equation":
                latex = str(
                    item.get("latex") or item.get("math_content") or _mineru_formula(raw)
                ).strip()
                latex = _strip_math_delimiters(latex)
                markdown = f"$$\n{latex}\n$$" if latex else markdown
            text = latex or markdown
            if not text.strip():
                continue
            bbox = _normalized_bbox(item.get("bbox"))
            captions = []
            if kind == "table":
                for caption in item.get("captions") or []:
                    caption_text = _mineru_markdown(caption.get("content", ""))
                    if caption_text.strip():
                        captions.append(
                            {
                                "page_index": index,
                                "type": "caption",
                                "text": caption_text,
                                "markdown": f"**{caption_text}**",
                                "latex": None,
                                "bbox": _normalized_bbox(caption.get("bbox")),
                                "section_path": [],
                                "parser": "mineru",
                                "confidence": None,
                            }
                        )
            for caption in captions:
                if caption["bbox"][1] <= bbox[1]:
                    caption["order"] = len(output)
                    output.append(caption)
            output.append(
                {
                    "page_index": index,
                    "order": len(output),
                    "type": kind,
                    "text": text,
                    "markdown": markdown,
                    "latex": latex,
                    "bbox": bbox,
                    "section_path": [],
                    "parser": "mineru",
                    "confidence": None,
                }
            )
            for caption in captions:
                if caption["bbox"][1] > bbox[1]:
                    caption["order"] = len(output)
                    output.append(caption)
    return output


def _normalized_bbox(value: object) -> list[float]:
    bbox = value if isinstance(value, list) and len(value) == 4 else [0, 0, 1, 1]
    return [coordinate / 1000 for coordinate in bbox] if max(bbox) > 1 else bbox


def _merge_split_scientific_cells(markdown: str) -> str:
    return SPLIT_SCIENTIFIC_CELLS_RE.sub(
        lambda match: f'<td colspan="2">{match.group(1).strip()} {match.group(2).strip()}</td>',
        markdown,
    )


def _strip_math_delimiters(value: str) -> str:
    for left, right in (("$$", "$$"), (r"\[", r"\]"), ("$", "$"), (r"\(", r"\)")):
        if value.startswith(left) and value.endswith(right):
            value = value[len(left) : -len(right)].strip()
            break
    return re.sub(r"\\\\(?=[A-Za-z])", r"\\", value)


def _mineru_formula(value: object) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        direct = value.get("latex") or value.get("math_content")
        if isinstance(direct, str):
            return direct
        return _mineru_formula(value.get("content") or value.get("spans") or "")
    if isinstance(value, list):
        return " ".join(filter(None, (_mineru_formula(part) for part in value)))
    return ""


def _mineru_markdown(value: object) -> str:
    if isinstance(value, str):
        return re.sub(
            r"\$([^$\n]+)\$",
            lambda match: "$" + _strip_math_delimiters(match.group(1)) + "$",
            value,
        )
    if isinstance(value, list):
        return " ".join(filter(None, (_mineru_markdown(part) for part in value)))
    if isinstance(value, dict):
        kind = value.get("type")
        if kind in ("inline_equation", "equation_inline"):
            formula = _strip_math_delimiters(_mineru_formula(value))
            return f"${formula}$" if formula else ""
        direct = value.get("text") or value.get("content") or value.get("spans")
        return _mineru_markdown(direct) if direct is not None else ""
    return ""


def parse_pdf(data: bytes, progress: Callable[[int, int], None] | None = None) -> list[dict]:
    doc = pymupdf.open(stream=BytesIO(data), filetype="pdf")
    cfg = settings()
    enhance_vector = cfg.parser_vector_enhance and len(doc) <= cfg.parser_vector_max_pages
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
                # PDF extractors may emit NUL glyphs, which PostgreSQL text fields reject.
                for field in ("text", "markdown", "latex"):
                    if block.get(field):
                        block[field] = block[field].replace("\x00", "")
                if not block["text"].strip():
                    continue
                if block["type"] == "heading":
                    numbered = re.match(r"^\s*(\d+(?:\.\d+)*)\s", block["text"])
                    level = numbered.group(1).count(".") + 1 if numbered else 1
                    current_section = current_section[: level - 1] + [block["text"]]
                block["section_path"] = current_section.copy()
                blocks.append(block)
        if progress:
            progress(end, len(doc))
    return blocks
