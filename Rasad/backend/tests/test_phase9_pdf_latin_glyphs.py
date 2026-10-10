"""Persian WOL faces must not render Latin URLs or punctuation as tofu."""
from html import escape

from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import Paragraph
from app.reports.generator import _pdf_text, _pdf_url_markup, _register_latin_font, _pdf_paragraphs


def test_mixed_text_uses_latin_font_after_bidi_and_escapes_markup():
    markup = _pdf_text('مذاکرات ایران (US) <script>& داده')
    assert 'name="'+_register_latin_font()+'"' in markup
    assert "US" in markup and "<script>" not in markup
    assert "&lt;script&gt;" in markup and "&amp;" in markup
    Paragraph(markup, getSampleStyleSheet()['Normal']).wrap(400, 800)


def test_source_url_is_full_ltr_clickable_and_font_selected():
    url = 'https://example.invalid/news/2026/123?x=1&y=2'
    markup = _pdf_url_markup(url)
    assert 'href="'+escape(url, quote=True)+'"' in markup
    assert 'name="'+_register_latin_font()+'"' in markup
    assert escape(url) in markup
    Paragraph(markup, getSampleStyleSheet()['Normal']).wrap(150, 800)


def test_unicode_url_label_is_ascii_and_invalid_scheme_is_rejected():
    markup = _pdf_url_markup('https://example.invalid/خبر')
    assert '%D8%AE' in markup and 'href="https://example.invalid/خبر"' in markup
    assert _pdf_url_markup('javascript:alert(1)') == ''
    assert _pdf_url_markup('https://user@example.invalid/') == ''


def test_historical_isolate_controls_still_render_without_bidi_failure():
    markup = _pdf_text('متن\u2069 (US) \u2066خبر')
    assert '\u2069' not in markup and '\u2066' not in markup
    Paragraph(markup, getSampleStyleSheet()['Normal']).wrap(400, 800)


def test_persian_parentheses_are_mirrored_not_forced_into_latin_embedding():
    markup = _pdf_text('تحلیل (مذاکره)')
    assert markup.count('(') == markup.count(')') == 1


def test_body_wraps_in_logical_sentence_order_before_bidi_reversal():
    style = getSampleStyleSheet()['Normal'].clone('logical-body', logical_wrap_width=110)
    lines = _pdf_paragraphs('FIRST SENTENCE THEN SECOND SENTENCE AND LAST SENTENCE', style)
    assert 'FIRST' in lines[0].text.split('<br/>')[0]
    assert lines[0].text.index('FIRST') < lines[0].text.index('LAST')
