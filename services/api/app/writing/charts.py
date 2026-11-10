"""Safe CSV inspection and chart intent validation for Flint/ECharts."""

import csv
import json
import logging
from io import BytesIO

import pandas as pd

from ..ai import generate

CHART_TYPES = {"line", "bar", "scatter", "radar", "table"}
logger = logging.getLogger(__name__)


def inspect_csv(data: bytes) -> tuple[pd.DataFrame, dict]:
    if not data or len(data) > 5_000_000:
        raise ValueError("CSV must be nonempty and at most 5 MB")
    frame = None
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            frame = pd.read_csv(BytesIO(data), encoding=encoding, on_bad_lines="error")
            break
        except UnicodeDecodeError:
            continue
        except pd.errors.ParserError as exc:
            raise ValueError("Invalid CSV rows") from exc
    if frame is None:
        raise ValueError("Unsupported CSV encoding")
    header = next(csv.reader(data.decode(encoding).splitlines()), [])
    if len(header) != len(set(header)) or any(not name.strip() for name in header):
        raise ValueError("CSV has invalid or duplicate columns")
    if frame.empty or len(frame.columns) < 2 or len(frame) > 50_000:
        raise ValueError("CSV needs 1–50,000 rows and at least two columns")
    if (
        len(frame.columns) > 80
        or frame.columns.astype(str).str.startswith("Unnamed:").any()
        or frame.columns.duplicated().any()
    ):
        raise ValueError("CSV has invalid or duplicate columns")
    if any(not str(c).strip() or len(str(c)) > 100 for c in frame.columns):
        raise ValueError("CSV has an empty or oversized column name")
    columns = []
    for name in frame.columns:
        series = frame[name]
        kind = "numeric" if pd.api.types.is_numeric_dtype(series) else "text"
        columns.append(
            {
                "name": str(name),
                "type": kind,
                "nulls": int(series.isna().sum()),
                "examples": [str(x)[:60] for x in series.dropna().head(3)],
            }
        )
    return frame, {"row_count": len(frame), "columns": columns}


def validate_intent(intent: dict, frame: pd.DataFrame) -> dict:
    kind = intent.get("type")
    if kind not in CHART_TYPES:
        raise ValueError("Unsupported chart type")
    x, y, group = (intent.get(key) for key in ("x", "y", "group"))
    if x not in frame.columns or (kind != "table" and y not in frame.columns):
        raise ValueError("Chart field does not exist")
    if group and group not in frame.columns:
        raise ValueError("Grouping field does not exist")
    if kind != "table" and not pd.api.types.is_numeric_dtype(frame[y]):
        raise ValueError("Y field must be numeric")
    if kind == "scatter" and not pd.api.types.is_numeric_dtype(frame[x]):
        raise ValueError("Scatter X field must be numeric")
    if kind == "radar" and (len(frame) > 30 or frame[x].nunique() > 20):
        raise ValueError("Radar chart requires at most 30 observations and 20 categories")
    transform = intent.get("transform", "none")
    if transform not in ("none", "mean", "sum", "count"):
        raise ValueError("Unsupported transformation")
    if transform != "none" and kind in ("scatter", "radar", "table"):
        raise ValueError("Transformation is incompatible with chart type")
    return {
        "type": kind,
        "x": x,
        "y": y if kind != "table" else None,
        "group": group or None,
        "title": str(intent.get("title") or f"{y} by {x}")[:120],
        "unit": str(intent.get("unit") or "")[:40],
        "transform": transform,
    }


def choose_chart(frame: pd.DataFrame, overview: dict, requested: str = "auto") -> dict:
    numeric = [c["name"] for c in overview["columns"] if c["type"] == "numeric"]
    if requested not in CHART_TYPES | {"auto"}:
        raise ValueError("Unsupported chart type")
    if requested != "table" and not numeric:
        raise ValueError("CSV needs a numeric column for a chart")
    schema = {
        "columns": overview["columns"],
        "rows": overview["row_count"],
        "requested_type": requested,
    }
    try:
        raw = generate(
            "Return JSON with type (line/bar/scatter/radar/table), x, y, group, title, unit, transform (none/mean/sum/count). Use only provided fields. No code. Prefer the requested type if not auto.",
            json.dumps(schema, ensure_ascii=False),
            max_tokens=300,
            json_mode=True,
        )
        proposal = json.loads(raw)
        if requested != "auto":
            proposal["type"] = requested
        return validate_intent(proposal, frame)
    except Exception:
        logger.exception("Chart intent generation failed; using validated field defaults")
        x = str(frame.columns[0])
        y = next((c for c in numeric if c != x), numeric[0] if numeric else None)
        fallback = {
            "type": requested if requested != "auto" else ("line" if y else "table"),
            "x": x,
            "y": y,
            "title": f"{y} by {x}" if y else "Data table",
            "transform": "none",
        }
        return validate_intent(fallback, frame)


def chart_data(frame: pd.DataFrame, intent: dict) -> tuple[list[dict], str, dict]:
    spec = validate_intent(intent, frame)
    x, y, transform = spec["x"], spec["y"], spec["transform"]
    chart_frame = frame
    if transform != "none":
        keys = [x] + ([spec["group"]] if spec["group"] else [])
        if transform == "count":
            chart_frame = frame.groupby(keys, dropna=False).size().reset_index(name="count")
            spec["y"] = y = "count"
        else:
            chart_frame = frame.groupby(keys, dropna=False)[y].agg(transform).reset_index()
    if len(chart_frame) > 5000:
        raise ValueError("Chart has more than 5,000 plotted rows; aggregate the data first")
    rows = json.loads(
        chart_frame.where(pd.notnull(chart_frame), None).to_json(
            orient="records", date_format="iso"
        )
    )
    if spec["type"] == "table":
        caption = f"Table. {len(frame)} observations and {len(frame.columns)} fields; first {min(len(rows), 50)} rows displayed."
        rows = rows[:50]
    else:
        values = pd.to_numeric(chart_frame[y], errors="coerce").dropna()
        if values.empty:
            raise ValueError("Selected metric has no valid values")
        first, last = float(values.iloc[0]), float(values.iloc[-1])
        trend = "increased" if last > first else "decreased" if last < first else "was unchanged"
        caption = (
            f"Figure. {y} by {x} across {len(values)} valid observations. {y} {trend} from {first:g} to {last:g}; observed range {float(values.min()):g}–{float(values.max()):g}."
            if spec["type"] == "line"
            else f"Figure. {y} by {x} across {len(values)} valid observations. Observed {y} range: {float(values.min()):g}–{float(values.max()):g}; mean: {float(values.mean()):g}."
        )
    return rows, caption, spec
