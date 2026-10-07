from __future__ import annotations

import html
import re
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence
from uuid import UUID
from urllib.parse import quote, urlsplit

import arabic_reshaper
from bidi.algorithm import get_display
from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt
from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm as rl_cm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    ListFlowable,
    ListItem,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from app.processing.text import BIDI_CONTROL_TRANSLATION


class RenderableAnalysisReport(Protocol):
    id: UUID
    title: str
    summary: str | None
    full_report: Mapping[str, Any]
    sources_json: Sequence[Mapping[str, Any]]


DEFAULT_STORAGE_ROOT = Path("/storage")
FONT_PATHS = [
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    Path("/usr/local/share/fonts/DejaVuSans.ttf"),
]
REPORTS_DIR = Path(__file__).parent
FONTS_DIR = REPORTS_DIR / "fonts"
SAHEL_REGULAR_PATH = FONTS_DIR / "Sahel-FD-WOL.ttf"
SAHEL_BOLD_PATH = FONTS_DIR / "Sahel-Bold-FD-WOL.ttf"
_REGISTERED_PDF_FONT: str | None = None
_REGISTERED_LATIN_FONT: str | None = None


def _register_latin_font() -> str:
    global _REGISTERED_LATIN_FONT
    if _REGISTERED_LATIN_FONT is None:
        _REGISTERED_LATIN_FONT = "Helvetica"
        for path in FONT_PATHS:
            if path.exists():
                pdfmetrics.registerFont(TTFont("RasadLatin", str(path)))
                _REGISTERED_LATIN_FONT = "RasadLatin"
                break
    return _REGISTERED_LATIN_FONT


def _plain(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return str(value).strip()


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _slug(value: str) -> str:
    cleaned = re.sub(r"[^\w\u0600-\u06FF-]+", "-", value, flags=re.UNICODE).strip("-")
    return cleaned[:80] or "report"


def _pdf_text(value: Any) -> str:
    # Reports can include historical rows saved before ingestion sanitization or
    # model output containing an unmatched directional isolate. python-bidi raises
    # AssertionError for those controls, so remove them at the rendering boundary.
    text = _plain(value).translate(BIDI_CONTROL_TRANSLATION)
    if not text:
        return ""
    # Isolate complete ASCII runs with supported bidi embeddings, including
    # brackets/ampersands. Otherwise bidi can swap a closing angle bracket with
    # a neighbouring parenthesis before HTML escaping.
    text = re.sub(
        r"[\x20-\x7e]+",
        lambda match: "\u202a" + match[0] + "\u202c"
        if re.search(r"[A-Za-z0-9]", match[0]) else match[0],
        text,
    )
    displayed = get_display(arabic_reshaper.reshape(text))
    # Sahel's bundled WOL face deliberately excludes Latin. Escape AFTER bidi
    # shaping so entity delimiters cannot be reordered into visible garbage.
    parts = re.split(r"([\x20-\x7e\u2022\u25cf]+)", displayed)
    latin_font = _register_latin_font()
    return "".join(
        f'<font name="{latin_font}">{html.escape(part)}</font>'
        if index % 2 else html.escape(part)
        for index, part in enumerate(parts) if part
    )


def _pdf_link(value: Any) -> str:
    url = _plain(value)
    try:
        parsed = urlsplit(url)
    except ValueError:
        return ""
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username:
        return ""
    return html.escape(url, quote=True)


def _pdf_url_markup(value: Any) -> str:
    href = _pdf_link(value)
    if not href:
        return ""
    # URL display is an unshaped LTR run. Percent-encode non-ASCII path text;
    # preserve the exact escaped href and never abbreviate away the evidence.
    label = html.escape(quote(_plain(value), safe=";/?:@&=+$,-_.!~*'()#%"))
    return f'<a href="{href}"><font name="{_register_latin_font()}" color="#1a2b4c">{label}</font></a>'


def _pdf_paragraphs(value: Any, style: ParagraphStyle) -> list[Paragraph]:
    """Bound layout work per flowable without dropping long report content.

    Short legacy paragraphs keep their exact rendering path. ReportLab repeatedly
    splits overflowing paragraphs across pages; one run-wide summary can make
    that work quadratic. Only long bodies are paginated in bounded word chunks.
    """
    text = _plain(value)
    width = getattr(style, "logical_wrap_width", None)
    if width:
        # ReportLab wraps visually ordered text from its first character. A
        # paragraph-wide bidi reversal therefore prints its last sentence first.
        # Wrap logical words to the actual body width BEFORE shaping each line.
        lines = []
        for logical_line in text.splitlines() or [""]:
            current = ""
            for word in logical_line.split():
                candidate = f"{current} {word}".strip()
                parsed = Paragraph(_pdf_text(candidate), style)
                measured = sum(pdfmetrics.stringWidth(f.text, f.fontName, f.fontSize) for f in parsed.frags)
                if current and measured > width:
                    lines.append(current)
                    current = word
                else:
                    current = candidate
            lines.append(current)
        # Bounded flowables retain the existing pagination work limit.
        return [Paragraph("<br/>".join(_pdf_text(line) for line in lines[i:i + 20]), style)
                for i in range(0, len(lines), 20)]
    if len(text) <= 2000:
        return [Paragraph(_pdf_text(text), style)]
    paragraphs = []
    while text:
        boundary = min(2000, len(text))
        if boundary < len(text):
            whitespace = text.rfind(" ", 0, boundary)
            if whitespace > 0:
                boundary = whitespace
        paragraphs.append(Paragraph(_pdf_text(text[:boundary]), style))
        text = text[boundary:].lstrip()
    return paragraphs


def _set_paragraph_rtl(paragraph) -> None:
    paragraph.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    p_pr = paragraph._p.get_or_add_pPr()
    bidi = OxmlElement("w:bidi")
    bidi.set(qn("w:val"), "1")
    p_pr.append(bidi)


def _set_run_rtl(run, font_name: str = "Sahel") -> None:
    run.font.name = font_name
    run._element.rPr.rFonts.set(qn("w:cs"), font_name)
    rPr = run._r.get_or_add_rPr()
    rtl = OxmlElement("w:rtl")
    rPr.append(rtl)


def _add_docx_paragraph(document: Document, text: str, *, style: str | None = None) -> None:
    paragraph = document.add_paragraph(style=style)
    _set_paragraph_rtl(paragraph)
    run = paragraph.add_run(text)
    _set_run_rtl(run)
    run.font.size = Pt(11)


def _add_docx_heading(document: Document, text: str, level: int = 2) -> None:
    paragraph = document.add_paragraph(style=f"Heading {level}")
    _set_paragraph_rtl(paragraph)
    run = paragraph.add_run(text)
    _set_run_rtl(run)
    run.bold = True
    run.font.size = Pt(14 if level == 2 else 12)


def _set_table_rtl(table) -> None:
    tblPr = table._tbl.tblPr
    bidiVisual = OxmlElement("w:bidiVisual")
    tblPr.append(bidiVisual)


def _set_cell_text_rtl(
    cell, text: str, font_name: str = "Sahel", bold: bool = False, size_pt: float = 10
) -> None:
    cell.text = ""  # Clear default paragraph
    p = cell.paragraphs[0]
    _set_paragraph_rtl(p)
    run = p.add_run(text)
    _set_run_rtl(run, font_name)
    run.bold = bold
    run.font.size = Pt(size_pt)


def _register_pdf_font() -> str:
    global _REGISTERED_PDF_FONT
    if _REGISTERED_PDF_FONT is not None:
        return _REGISTERED_PDF_FONT
    if SAHEL_REGULAR_PATH.exists():
        pdfmetrics.registerFont(TTFont("Sahel", str(SAHEL_REGULAR_PATH)))
        if SAHEL_BOLD_PATH.exists():
            pdfmetrics.registerFont(TTFont("Sahel-Bold", str(SAHEL_BOLD_PATH)))
            from reportlab.pdfbase.pdfmetrics import registerFontFamily

            registerFontFamily("Sahel", normal="Sahel", bold="Sahel-Bold")
        _REGISTERED_PDF_FONT = "Sahel"
        return _REGISTERED_PDF_FONT
    for path in FONT_PATHS:
        if path.exists():
            pdfmetrics.registerFont(TTFont("DejaVuSans", str(path)))
            _REGISTERED_PDF_FONT = "DejaVuSans"
            return _REGISTERED_PDF_FONT
    _REGISTERED_PDF_FONT = "Helvetica"
    return _REGISTERED_PDF_FONT


class ReportExportGenerator:
    def __init__(self, storage_root: Path | str = DEFAULT_STORAGE_ROOT) -> None:
        self.storage_root = Path(storage_root)
        self.reports_dir = self.storage_root / "reports"

    def generate_all(self, report: RenderableAnalysisReport) -> tuple[str, str]:
        self.reports_dir.mkdir(parents=True, exist_ok=True)
        pdf_path = self.generate_pdf(report)
        docx_path = self.generate_docx(report)
        return str(pdf_path), str(docx_path)

    def generate_pdf(self, report: RenderableAnalysisReport) -> Path:
        path = self.reports_dir / f"{report.id}-{_slug(report.title)}.pdf"
        font_name = _register_pdf_font()
        styles = getSampleStyleSheet()

        # Base styles
        body = ParagraphStyle(
            "RasadBody",
            parent=styles["Normal"],
            fontName=font_name,
            fontSize=10,
            leading=16,
            alignment=TA_RIGHT,
            rightIndent=0,
            # SimpleDocTemplate's frame reserves 6pt padding on each side.
            logical_wrap_width=A4[0] - 3.4 * rl_cm - 12,
        )

        body_left = ParagraphStyle(
            "RasadBodyLeft",
            parent=body,
            alignment=0,  # Left align
            fontSize=9,
            leading=13,
            textColor=colors.HexColor("#4b5563"),
        )

        title_style = ParagraphStyle(
            "RasadTitle",
            parent=body,
            fontName="Sahel-Bold" if font_name == "Sahel" else font_name,
            fontSize=18,
            leading=24,
            spaceAfter=12,
            textColor=colors.HexColor("#1a2b4c"),
            alignment=TA_RIGHT,
        )

        heading_style = ParagraphStyle(
            "RasadHeading",
            parent=body,
            fontName="Sahel-Bold" if font_name == "Sahel" else font_name,
            fontSize=14,
            leading=20,
            spaceBefore=14,
            spaceAfter=8,
            keepWithNext=True,
            textColor=colors.HexColor("#c2272d"),
            alignment=TA_RIGHT,
        )

        subheading_style = ParagraphStyle(
            "RasadSubHeading",
            parent=body,
            fontName="Sahel-Bold" if font_name == "Sahel" else font_name,
            fontSize=11,
            leading=16,
            spaceBefore=8,
            spaceAfter=4,
            keepWithNext=True,
            textColor=colors.HexColor("#1a2b4c"),
            alignment=TA_RIGHT,
        )

        metric_value = ParagraphStyle(
            "RasadMetricValue",
            parent=body,
            fontName="Sahel-Bold" if font_name == "Sahel" else font_name,
            fontSize=15,
            leading=18,
            alignment=1,  # Center
            textColor=colors.HexColor("#ef4444"),
        )

        metric_label = ParagraphStyle(
            "RasadMetricLabel",
            parent=body,
            fontName=font_name,
            fontSize=8,
            leading=11,
            alignment=1,  # Center
            textColor=colors.white,
        )

        url_style = ParagraphStyle(
            "RasadSourceUrl",
            parent=body_left,
            fontName=font_name,
            fontSize=7,
            leading=10,
            alignment=0,  # Left align
        )

        table_cell_style = ParagraphStyle(
            "RasadTableCell",
            parent=body,
            fontName=font_name,
            fontSize=8,
            leading=11,
            alignment=TA_RIGHT,
        )

        table_header_style = ParagraphStyle(
            "RasadTableHeader",
            parent=body,
            fontName="Sahel-Bold" if font_name == "Sahel" else font_name,
            fontSize=9,
            leading=12,
            alignment=TA_RIGHT,
        )

        document = SimpleDocTemplate(
            str(path),
            pagesize=A4,
            rightMargin=1.7 * rl_cm,
            leftMargin=1.7 * rl_cm,
            topMargin=1.7 * rl_cm,
            bottomMargin=1.7 * rl_cm,
            title=report.title,
            author="Rasad Analysis",
        )

        story = [Paragraph(_pdf_text(report.title), title_style), Spacer(1, 0.2 * rl_cm)]

        data = report.full_report or {}

        # 1. خلاصه اجرایی
        story.append(Paragraph(_pdf_text("۱. خلاصه اجرایی"), heading_style))
        exec_summary = data.get("executive_summary") or {}
        if isinstance(exec_summary, str):
            summary_text = exec_summary
            metrics = []
        else:
            summary_text = exec_summary.get("text") or report.summary or ""
            metrics = exec_summary.get("metrics") or []
        story.extend(_pdf_paragraphs(summary_text, body))
        story.append(Spacer(1, 0.15 * rl_cm))

        # Metrics Cards
        if metrics:
            num_metrics = len(metrics)
            col_width = (17.6 / num_metrics) * rl_cm
            col_widths = [col_width] * num_metrics

            row_cells = []
            for metric in metrics:
                val = _pdf_text(metric.get("value") or "")
                lbl = _pdf_text(metric.get("label") or "")
                cell_flowables = [
                    Spacer(1, 0.1 * rl_cm),
                    Paragraph(val, metric_value),
                    Spacer(1, 0.05 * rl_cm),
                    Paragraph(lbl, metric_label),
                    Spacer(1, 0.1 * rl_cm),
                ]
                row_cells.append(cell_flowables)

            metrics_table = Table([row_cells], colWidths=col_widths, hAlign="CENTER")
            metrics_table.setStyle(
                TableStyle(
                    [
                        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#1a2b4c")),
                        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                        ("TOPPADDING", (0, 0), (-1, -1), 6),
                        ("LEFTPADDING", (0, 0), (-1, -1), 4),
                        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                    ]
                )
            )
            story.append(metrics_table)
            story.append(Spacer(1, 0.25 * rl_cm))

        # 2. نقشه زمانی تحولات
        story.append(Paragraph(_pdf_text("۲. نقشه زمانی تحولات"), heading_style))
        timeline = data.get("timeline") or []
        if timeline:
            timeline_rows = []
            for event in timeline:
                date_str = _pdf_text(event.get("date") or "")
                desc_str = _pdf_text(event.get("description") or "")

                date_p = Paragraph(f"<font name='{_register_latin_font()}' color='#c2272d'>●</font> {date_str}", body)
                desc_p = Paragraph(desc_str, body)
                timeline_rows.append([desc_p, date_p])

            timeline_table = Table(
                timeline_rows, colWidths=[13.6 * rl_cm, 4.0 * rl_cm], hAlign="RIGHT"
            )
            timeline_table.setStyle(
                TableStyle(
                    [
                        ("VALIGN", (0, 0), (-1, -1), "TOP"),
                        ("ALIGN", (0, 0), (-1, -1), "RIGHT"),
                        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                        ("TOPPADDING", (0, 0), (-1, -1), 6),
                        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                        ("LEFTPADDING", (0, 0), (-1, -1), 4),
                    ]
                )
            )
            story.append(timeline_table)
        else:
            story.append(Paragraph(_pdf_text("رویدادی ثبت نشده است."), body))
        story.append(Spacer(1, 0.25 * rl_cm))

        # 3. اخبار و تحلیل‌ها از منابع
        story.append(Paragraph(_pdf_text("۳. اخبار و تحلیل‌ها از منابع"), heading_style))
        sources_analysis = data.get("sources_analysis") or []
        if sources_analysis:
            sub_idx = 1
            for group in sources_analysis:
                source_name = group.get("source_name") or ""
                articles = group.get("articles") or []
                if not articles:
                    continue
                story.append(
                    Paragraph(_pdf_text(f"۳.{sub_idx} منبع: {source_name}"), subheading_style)
                )
                sub_idx += 1

                for art in articles:
                    art_title = art.get("title") or ""
                    art_date = art.get("date") or ""
                    art_url = _pdf_url_markup(art.get("url") or "")
                    art_summary = art.get("summary") or ""

                    # Title
                    story.append(
                        Paragraph(
                            f"<font color='#1a2b4c'><b>{_pdf_text(art_title)}</b></font>", body
                        )
                    )
                    # Date & URL
                    url_html = art_url
                    meta_text = f"<font color='#6b7280'>{_pdf_text(art_date)}</font>"
                    if url_html:
                        meta_text += f" <font name='{_register_latin_font()}'>|</font> {url_html}"
                    story.append(Paragraph(meta_text, body_left))
                    # Summary
                    story.extend(_pdf_paragraphs(art_summary, body))
                    story.append(Spacer(1, 0.15 * rl_cm))
        else:
            story.append(Paragraph(_pdf_text("تحلیلی برای منابع ثبت نشده است."), body))
        story.append(Spacer(1, 0.2 * rl_cm))

        # 4. تحلیل شناختی گفتمان رسانه‌ای
        story.append(Paragraph(_pdf_text("۴. تحلیل شناختی گفتمان رسانه‌ای"), heading_style))
        cognitive = data.get("cognitive_analysis") or {}
        if isinstance(cognitive, str):
            story.extend(_pdf_paragraphs(cognitive, body))
        else:
            sub_sections = cognitive.get("sub_sections") or []
            for sub in sub_sections:
                sub_title = sub.get("title") or ""
                sub_content = sub.get("content") or ""
                story.append(Paragraph(_pdf_text(sub_title), subheading_style))
                story.extend(_pdf_paragraphs(sub_content, body))
                story.append(Spacer(1, 0.1 * rl_cm))

            key_findings = cognitive.get("key_findings") or []
            if key_findings:
                story.append(Paragraph(_pdf_text("یافته‌های کلیدی شناختی:"), subheading_style))
                items = [
                    ListItem(
                        Paragraph(_pdf_text(finding), body), bulletColor=colors.HexColor("#c2272d")
                    )
                    for finding in key_findings
                    if _plain(finding)
                ]
                story.append(
                    ListFlowable(items, bulletType="bullet", start="circle", rightIndent=12)
                )
                story.append(Spacer(1, 0.15 * rl_cm))
        story.append(Spacer(1, 0.2 * rl_cm))

        # 5. تحلیل آینده‌پژوهی و سناریوها
        story.append(Paragraph(_pdf_text("۵. تحلیل آینده‌پژوهی و سناریوها"), heading_style))
        future = data.get("future_analysis") or {}
        if isinstance(future, str):
            story.extend(_pdf_paragraphs(future, body))
        else:
            scenarios = future.get("scenarios") or []
            sc_idx = 1
            for sc in scenarios:
                sc_title = sc.get("title") or ""
                sc_prob = sc.get("probability") or ""
                sc_desc = sc.get("description") or ""
                sc_imp = sc.get("implications") or ""

                story.append(
                    Paragraph(
                        _pdf_text(f"سناریو {sc_idx}: {sc_title} (احتمال: {sc_prob})"),
                        subheading_style,
                    )
                )
                sc_idx += 1
                story.extend(_pdf_paragraphs(sc_desc, body))
                if sc_imp:
                    story.append(
                        Paragraph(
                            f"<i>{_pdf_text('پیامدها برای اسرائیل/منطقه:')}</i> {_pdf_text(sc_imp)}",
                            body,
                        )
                    )
                story.append(Spacer(1, 0.15 * rl_cm))

            key_vars = future.get("key_variables") or []
            if key_vars:
                story.append(Paragraph(_pdf_text("متغیرهای کلیدی آینده:"), subheading_style))
                for var in key_vars:
                    var_name = var.get("name") or ""
                    var_desc = var.get("description") or ""
                    story.append(
                        Paragraph(f"<b>{_pdf_text(var_name)}:</b> {_pdf_text(var_desc)}", body)
                    )
                    story.append(Spacer(1, 0.08 * rl_cm))
        story.append(Spacer(1, 0.2 * rl_cm))

        # 6. جمع‌بندی و نتیجه‌گیری
        story.append(Paragraph(_pdf_text("۶. جمع‌بندی و نتیجه‌گیری"), heading_style))
        conclusion_text = data.get("conclusion") or report.full_report.get("conclusion") or ""
        story.extend(_pdf_paragraphs(conclusion_text, body))
        story.append(Spacer(1, 0.25 * rl_cm))
        # Sources table
        source_rows = [
            [
                Paragraph(_pdf_text("منبع"), table_header_style),
                Paragraph(_pdf_text("تاریخ نشر"), table_header_style),
                Paragraph(_pdf_text("نشانی"), table_header_style),
            ]
        ]
        for src in report.sources_json:
            url_val = _pdf_url_markup(src.get("url"))
            url_paragraph = (
                Paragraph(
                    url_val, url_style
                )
                if url_val
                else Paragraph("", url_style)
            )
            source_rows.append(
                [
                    Paragraph(
                        _pdf_text(src.get("title") or src.get("source_name")), table_cell_style
                    ),
                    Paragraph(_pdf_text(src.get("published_at_jalali") or ""), table_cell_style),
                    url_paragraph,
                ]
            )
        if len(source_rows) > 1:
            table = Table(
                source_rows, colWidths=[5 * rl_cm, 3 * rl_cm, 9.6 * rl_cm], hAlign="RIGHT", repeatRows=1
            )
            table.setStyle(
                TableStyle(
                    [
                        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f3e7dc")),
                        ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#d7c1af")),
                        ("ALIGN", (0, 0), (-1, -1), "RIGHT"),
                        ("VALIGN", (0, 0), (-1, -1), "TOP"),
                        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                        ("TOPPADDING", (0, 0), (-1, -1), 6),
                        ("LEFTPADDING", (0, 0), (-1, -1), 6),
                        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                    ]
                )
            )
            story.append(Paragraph(_pdf_text("منابع و مراجع"), heading_style))
            story.append(table)
        document.build(story)
        return path

    def generate_docx(self, report: RenderableAnalysisReport) -> Path:
        path = self.reports_dir / f"{report.id}-{_slug(report.title)}.docx"
        document = Document()
        section = document.sections[0]
        section.right_margin = Cm(1.7)
        section.left_margin = Cm(1.7)

        # Document Title
        title_p = document.add_paragraph(style="Title")
        _set_paragraph_rtl(title_p)
        run = title_p.add_run(report.title)
        _set_run_rtl(run)
        run.bold = True
        run.font.size = Pt(18)

        data = report.full_report or {}

        # 1. خلاصه اجرایی
        _add_docx_heading(document, "۱. خلاصه اجرایی", 2)
        exec_summary = data.get("executive_summary") or {}
        if isinstance(exec_summary, str):
            summary_text = exec_summary
            metrics = []
        else:
            summary_text = exec_summary.get("text") or report.summary or ""
            metrics = exec_summary.get("metrics") or []
        _add_docx_paragraph(document, summary_text)

        if metrics:
            table = document.add_table(rows=1, cols=len(metrics))
            table.style = "Table Grid"
            _set_table_rtl(table)
            for idx, metric in enumerate(metrics):
                cell = table.rows[0].cells[idx]
                val = _plain(metric.get("value") or "")
                lbl = _plain(metric.get("label") or "")

                cell_p = cell.paragraphs[0]
                _set_paragraph_rtl(cell_p)
                val_run = cell_p.add_run(f"{val}\n")
                _set_run_rtl(val_run)
                val_run.bold = True
                val_run.font.size = Pt(14)

                lbl_run = cell_p.add_run(lbl)
                _set_run_rtl(lbl_run)
                lbl_run.font.size = Pt(9)
            document.add_paragraph()  # Spacer

        # 2. نقشه زمانی تحولات
        _add_docx_heading(document, "۲. نقشه زمانی تحولات", 2)
        timeline = data.get("timeline") or []
        for event in timeline:
            date_str = _plain(event.get("date") or "")
            desc_str = _plain(event.get("description") or "")
            _add_docx_paragraph(document, f"• {date_str}: {desc_str}")

        # 3. اخبار و تحلیل‌ها از منابع
        _add_docx_heading(document, "۳. اخبار و تحلیل‌ها از منابع", 2)
        sources_analysis = data.get("sources_analysis") or []
        sub_idx = 1
        for group in sources_analysis:
            source_name = group.get("source_name") or ""
            articles = group.get("articles") or []
            if not articles:
                continue
            _add_docx_heading(document, f"۳.{sub_idx} منبع: {source_name}", 3)
            sub_idx += 1

            for art in articles:
                art_title = _plain(art.get("title") or "")
                art_date = _plain(art.get("date") or "")
                art_url = _plain(art.get("url") or "")
                art_summary = _plain(art.get("summary") or "")

                p = document.add_paragraph()
                _set_paragraph_rtl(p)
                run = p.add_run(art_title)
                _set_run_rtl(run)
                run.bold = True
                run.font.size = Pt(11)

                meta_p = document.add_paragraph()
                _set_paragraph_rtl(meta_p)
                meta_run = meta_p.add_run(f"{art_date} | {art_url}")
                _set_run_rtl(meta_run)
                meta_run.font.size = Pt(9)
                meta_run.italic = True

                _add_docx_paragraph(document, art_summary)
                document.add_paragraph()  # Spacer

        # 4. تحلیل شناختی گفتمان رسانه‌ای
        _add_docx_heading(document, "۴. تحلیل شناختی گفتمان رسانه‌ای", 2)
        cognitive = data.get("cognitive_analysis") or {}
        if isinstance(cognitive, str):
            _add_docx_paragraph(document, cognitive)
        else:
            sub_sections = cognitive.get("sub_sections") or []
            for sub in sub_sections:
                sub_title = _plain(sub.get("title") or "")
                sub_content = _plain(sub.get("content") or "")
                _add_docx_heading(document, sub_title, 3)
                _add_docx_paragraph(document, sub_content)

            key_findings = cognitive.get("key_findings") or []
            if key_findings:
                _add_docx_heading(document, "یافته‌های کلیدی شناختی", 3)
                for finding in key_findings:
                    _add_docx_paragraph(document, f"• {_plain(finding)}")

        # 5. تحلیل آینده‌پژوهی و سناریوها
        _add_docx_heading(document, "۵. تحلیل آینده‌پژوهی و سناریوها", 2)
        future = data.get("future_analysis") or {}
        if isinstance(future, str):
            _add_docx_paragraph(document, future)
        else:
            scenarios = future.get("scenarios") or []
            sc_idx = 1
            for sc in scenarios:
                sc_title = _plain(sc.get("title") or "")
                sc_prob = _plain(sc.get("probability") or "")
                sc_desc = _plain(sc.get("description") or "")
                sc_imp = _plain(sc.get("implications") or "")

                _add_docx_heading(document, f"سناریو {sc_idx}: {sc_title} (احتمال: {sc_prob})", 3)
                sc_idx += 1
                _add_docx_paragraph(document, sc_desc)
                if sc_imp:
                    _add_docx_paragraph(document, f"پیامدهای سناریو: {sc_imp}")

            key_vars = future.get("key_variables") or []
            if key_vars:
                _add_docx_heading(document, "متغیرهای کلیدی آینده", 3)
                for var in key_vars:
                    var_name = _plain(var.get("name") or "")
                    var_desc = _plain(var.get("description") or "")
                    _add_docx_paragraph(document, f"• {var_name}: {var_desc}")

        # 6. جمع‌بندی و نتیجه‌گیری
        _add_docx_heading(document, "۶. جمع‌بندی و نتیجه‌گیری", 2)
        conclusion_text = data.get("conclusion") or report.full_report.get("conclusion") or ""
        _add_docx_paragraph(document, conclusion_text)

        # Sources references
        if report.sources_json:
            _add_docx_heading(document, "منابع و مراجع", 2)
            table = document.add_table(rows=1, cols=2)
            table.style = "Table Grid"
            _set_table_rtl(table)

            _set_cell_text_rtl(table.rows[0].cells[0], "منبع", bold=True, size_pt=11)
            _set_cell_text_rtl(table.rows[0].cells[1], "تاریخ نشر / نشانی", bold=True, size_pt=11)

            for source in report.sources_json:
                row = table.add_row().cells
                title_val = _plain(source.get("title") or source.get("source_name"))
                meta_val = " - ".join(
                    item
                    for item in (
                        _plain(source.get("published_at_jalali")),
                        _plain(source.get("url")),
                    )
                    if item
                )
                _set_cell_text_rtl(row[0], title_val, size_pt=10)
                _set_cell_text_rtl(row[1], meta_val, size_pt=10)

        document.save(path)
        return path
