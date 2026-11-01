import pymupdf

from services.api.app.parsing import identify_pdf


def test_title_skips_license_notice_before_paper_title():
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((60, 70), "Permission is granted to reproduce this paper", fontsize=12)
    page.insert_text((60, 145), "A General Method for Research", fontsize=18)
    page.insert_text((60, 230), "arXiv:2601.23456", fontsize=20)
    result = identify_pdf(doc.tobytes(), "2601.23456.pdf")
    assert result["title"] == "A General Method for Research"
    assert result["arxiv_id"] == "2601.23456"
