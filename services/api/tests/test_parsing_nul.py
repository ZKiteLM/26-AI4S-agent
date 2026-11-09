from types import SimpleNamespace

import pymupdf
import pytest

from services.api.app import parsing


@pytest.mark.parametrize("enhanced", [False, True])
def test_parse_pdf_removes_nul_from_all_block_text(monkeypatch, enhanced):
    document = pymupdf.open()
    document.new_page()
    pdf = document.tobytes()
    document.close()

    block = {
        "page_index": 0,
        "order": 0,
        "type": "heading",
        "text": "1 Intro\x00duction",
        "markdown": "1 Intro\x00duction",
        "latex": "x\x00+y",
        "bbox": [0.1, 0.1, 0.9, 0.2],
        "section_path": [],
        "parser": "mineru" if enhanced else "pymupdf",
        "confidence": None,
    }
    monkeypatch.setattr(
        parsing,
        "settings",
        lambda: SimpleNamespace(parser_vector_enhance=enhanced, parser_vector_max_pages=50),
    )
    monkeypatch.setattr(parsing, "_pymupdf_blocks", lambda *_args: [block.copy()])
    if enhanced:
        monkeypatch.setattr(parsing, "_mineru_blocks", lambda *_args, **_kwargs: [block.copy()])

    result = parsing.parse_pdf(pdf)

    assert len(result) == 1
    assert result[0]["text"] == "1 Introduction"
    assert result[0]["markdown"] == "1 Introduction"
    assert result[0]["latex"] == "x+y"
    assert result[0]["section_path"] == ["1 Introduction"]
