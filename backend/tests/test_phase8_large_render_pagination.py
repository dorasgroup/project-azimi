from unittest.mock import patch
from reportlab.lib.styles import getSampleStyleSheet
from app.reports.generator import _pdf_paragraphs


def test_short_legacy_paragraph_keeps_one_flowable():
    with patch("app.reports.generator._pdf_text", side_effect=lambda value: value) as transform:
        paragraphs = _pdf_paragraphs("short unchanged text", getSampleStyleSheet()["Normal"])
    assert len(paragraphs) == 1
    assert transform.call_args.args == ("short unchanged text",)


def test_large_body_keeps_all_words_in_bounded_layout_chunks():
    text = " ".join(f"word{index}" for index in range(5000))
    with patch("app.reports.generator._pdf_text", side_effect=lambda value: value) as transform:
        paragraphs = _pdf_paragraphs(text, getSampleStyleSheet()["Normal"])
    chunks = [call.args[0] for call in transform.call_args_list]
    assert len(paragraphs) == len(chunks) > 1
    assert all(len(chunk) <= 2000 for chunk in chunks)
    assert " ".join(chunks).split() == text.split()
