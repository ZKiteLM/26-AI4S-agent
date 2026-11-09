"""Constrained Mermaid generation and repair."""

import re

from .ai import generate

ALLOWED = ("flowchart", "graph", "sequenceDiagram", "classDiagram", "stateDiagram-v2", "erDiagram")


def generate_mermaid(description: str, previous: str = "", error: str = "") -> str:
    if len(description) > 5000 or len(previous) > 20000:
        raise ValueError("Diagram input too large")
    raw = generate(
        "Return only valid Mermaid source, no Markdown fence or explanation. Use flowchart for architectures. Avoid HTML, click directives, links, embedded styles and scripts. Keep node labels concise.",
        f"Description: {description}\nPrevious Mermaid: {previous[:10000]}\nParser error: {error[:1000]}",
        max_tokens=900,
    ).strip()
    source = re.sub(r"^```(?:mermaid)?\s*|\s*```$", "", raw, flags=re.I).strip()
    if (
        not source.startswith(ALLOWED)
        or len(source) > 20000
        or re.search(r"(?im)^\s*(?:click|%%\{|init\b|linkStyle|style\b)", source)
        or "<" in source
    ):
        raise ValueError("Invalid or unsafe Mermaid source")
    return source
