"""Exports d'une conversation : TXT, JSON et PDF (transcription complète ou résumé)."""
import io
import json
import os
import re
from datetime import datetime
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (HRFlowable, ListFlowable, ListItem, Paragraph, Preformatted,
                                SimpleDocTemplate, Spacer, Table, TableStyle)

from llm_service import DOMAINS

ROLE_LABELS = {"user": "Vous", "assistant": "Assistant"}


# ----------------------------------------------------------------------------
# Utilitaires
# ----------------------------------------------------------------------------
def safe_filename(title: str, ext: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9_-]+", "_", title.strip())[:50].strip("_") or "conversation"
    return f"{slug}_{datetime.now():%Y%m%d_%H%M}.{ext}"


def domain_label(key: str) -> str:
    return DOMAINS.get(key, DOMAINS["general"])["label"]


def _fmt_date(iso: str) -> str:
    try:
        return datetime.fromisoformat(iso).strftime("%d/%m/%Y %H:%M")
    except (TypeError, ValueError):
        return iso or ""


def _fmt_ms(ms) -> str:
    return "—" if ms is None else f"{ms / 1000:.2f} s"


# ----------------------------------------------------------------------------
# TXT / JSON
# ----------------------------------------------------------------------------
def to_txt(conv: dict, messages: list[dict]) -> str:
    lines = [
        conv["title"],
        "=" * len(conv["title"]),
        f"Domaine : {domain_label(conv['domain'])}",
        f"Créée le : {_fmt_date(conv['created_at'])}",
        f"Messages : {len(messages)}",
        "",
    ]
    for m in messages:
        lines.append(f"[{_fmt_date(m['created_at'])}] {ROLE_LABELS[m['role']]}")
        lines.append(m["content"])
        if m["role"] == "assistant":
            meta = [f"temps {_fmt_ms(m['response_time_ms'])}", f"tokens {m['total_tokens'] or '—'}"]
            if m["rating"]:
                meta.append(f"note {m['rating']}/5")
            lines.append("(" + " · ".join(meta) + ")")
            for s in m["sources"]:
                lines.append(f"  [{s['label']}] {s['document_name']} (lignes {s['start_line']}-{s['end_line']})")
        lines.append("")
    return "\n".join(lines)


def to_json(conv: dict, messages: list[dict]) -> str:
    payload = {
        "conversation": {k: conv[k] for k in ("id", "title", "domain", "created_at", "updated_at")},
        "messages": [
            {k: m[k] for k in (
                "id", "role", "content", "created_at", "model", "response_time_ms", "first_token_ms",
                "prompt_tokens", "completion_tokens", "total_tokens", "rating", "sources")}
            for m in messages
        ],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


# ----------------------------------------------------------------------------
# PDF
# ----------------------------------------------------------------------------
_FONTS: tuple[str, str, bool] | None = None


def _fonts() -> tuple[str, str, bool]:
    """(police normale, police grasse, unicode ?). Utilise DejaVu/Arial si disponibles."""
    global _FONTS
    if _FONTS:
        return _FONTS
    candidates = [
        ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
        ("C:/Windows/Fonts/arial.ttf", "C:/Windows/Fonts/arialbd.ttf"),
        ("/Library/Fonts/Arial.ttf", "/Library/Fonts/Arial Bold.ttf"),
        ("/System/Library/Fonts/Supplemental/Arial.ttf", "/System/Library/Fonts/Supplemental/Arial Bold.ttf"),
    ]
    for regular, bold in candidates:
        if os.path.exists(regular):
            pdfmetrics.registerFont(TTFont("Body", regular))
            pdfmetrics.registerFont(TTFont("Body-Bold", bold if os.path.exists(bold) else regular))
            pdfmetrics.registerFontFamily("Body", normal="Body", bold="Body-Bold", italic="Body", boldItalic="Body-Bold")
            _FONTS = ("Body", "Body-Bold", True)
            return _FONTS
    _FONTS = ("Helvetica", "Helvetica-Bold", False)
    return _FONTS


def _clean(text: str) -> str:
    """Rend le texte compatible avec la police (Helvetica ne gère que le latin-1/cp1252)."""
    text = text.replace("\r", "")
    if not _fonts()[2]:
        text = text.encode("cp1252", errors="replace").decode("cp1252")
    return text


def _styles() -> dict:
    regular, bold, _ = _fonts()
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle("t", parent=base["Title"], fontName=bold, fontSize=20, leading=24,
                                alignment=TA_LEFT, textColor=colors.HexColor("#1e1b4b"), spaceAfter=4),
        "meta": ParagraphStyle("m", fontName=regular, fontSize=9, leading=12, textColor=colors.HexColor("#64748b")),
        "h2": ParagraphStyle("h2", fontName=bold, fontSize=13, leading=16, spaceBefore=12, spaceAfter=4,
                             textColor=colors.HexColor("#4338ca")),
        "body": ParagraphStyle("b", fontName=regular, fontSize=10, leading=14, spaceAfter=4),
        "role_user": ParagraphStyle("ru", fontName=bold, fontSize=9, leading=12, spaceBefore=10,
                                    textColor=colors.HexColor("#0f172a")),
        "role_ai": ParagraphStyle("ra", fontName=bold, fontSize=9, leading=12, spaceBefore=10,
                                  textColor=colors.HexColor("#4338ca")),
        "small": ParagraphStyle("s", fontName=regular, fontSize=8, leading=10, textColor=colors.HexColor("#64748b")),
        "code": ParagraphStyle("c", fontName="Courier", fontSize=8, leading=10, backColor=colors.HexColor("#f1f5f9"),
                               borderPadding=4, spaceAfter=6),
    }


def _inline(text: str) -> str:
    """Markdown en ligne -> balises reportlab (après échappement)."""
    text = escape(_clean(text))
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"`([^`]+)`", r'<font face="Courier">\1</font>', text)
    return text


def _markdown_flowables(text: str, st: dict) -> list:
    flow, bullets, in_code, code = [], [], False, []

    def flush_bullets():
        nonlocal bullets
        if bullets:
            flow.append(ListFlowable([ListItem(Paragraph(b, st["body"]), leftIndent=12) for b in bullets],
                                     bulletType="bullet", start="•", leftIndent=12))
            bullets = []

    for raw in text.splitlines():
        line = raw.rstrip()
        if line.strip().startswith("```"):
            if in_code:
                flow.append(Preformatted(_clean("\n".join(code)), st["code"]))
                code = []
            in_code = not in_code
            continue
        if in_code:
            code.append(line)
            continue
        heading = re.match(r"^#{1,6}\s+(.*)", line)
        item = re.match(r"^\s*(?:[-*+]|\d+[.)])\s+(.*)", line)
        if heading:
            flush_bullets()
            flow.append(Paragraph(_inline(heading.group(1)), st["h2"]))
        elif item:
            bullets.append(_inline(item.group(1)))
        elif not line.strip():
            flush_bullets()
        else:
            flush_bullets()
            flow.append(Paragraph(_inline(line), st["body"]))
    flush_bullets()
    if code:
        flow.append(Preformatted(_clean("\n".join(code)), st["code"]))
    return flow


def _build(story: list, title: str) -> bytes:
    buffer = io.BytesIO()

    def footer(canvas, doc):
        canvas.saveState()
        canvas.setFont(_fonts()[0], 8)
        canvas.setFillColor(colors.HexColor("#94a3b8"))
        canvas.drawString(2 * cm, 1.2 * cm, "FlaskGPT")
        canvas.drawRightString(A4[0] - 2 * cm, 1.2 * cm, f"Page {doc.page}")
        canvas.restoreState()

    doc = SimpleDocTemplate(buffer, pagesize=A4, leftMargin=2 * cm, rightMargin=2 * cm,
                            topMargin=2 * cm, bottomMargin=2 * cm, title=title, author="FlaskGPT")
    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return buffer.getvalue()


def _header(conv: dict, st: dict, subtitle: str) -> list:
    return [
        Paragraph(escape(_clean(conv["title"])), st["title"]),
        Paragraph(escape(_clean(
            f"{subtitle} · Domaine : {domain_label(conv['domain'])} · Créée le {_fmt_date(conv['created_at'])}")),
            st["meta"]),
        Spacer(1, 6),
        HRFlowable(width="100%", thickness=0.8, color=colors.HexColor("#c7d2fe")),
    ]


def to_pdf(conv: dict, messages: list[dict]) -> bytes:
    """Transcription complète de la conversation."""
    st = _styles()
    story = _header(conv, st, "Transcription complète")
    for m in messages:
        is_user = m["role"] == "user"
        label = f"{ROLE_LABELS[m['role']]} — {_fmt_date(m['created_at'])}"
        story.append(Paragraph(escape(_clean(label)), st["role_user" if is_user else "role_ai"]))
        story.extend(_markdown_flowables(m["content"], st))
        if not is_user:
            meta = [f"Temps : {_fmt_ms(m['response_time_ms'])}", f"Tokens : {m['total_tokens'] or '—'}"]
            if m["rating"]:
                meta.append(f"Note : {m['rating']}/5")
            story.append(Paragraph(escape(_clean(" · ".join(meta))), st["small"]))
            if m["sources"]:
                refs = "; ".join(f"[{s['label']}] {s['document_name']} (l. {s['start_line']}-{s['end_line']})"
                                 for s in m["sources"])
                story.append(Paragraph(escape(_clean("Sources : " + refs)), st["small"]))
    return _build(story, conv["title"])


def summary_pdf(conv: dict, messages: list[dict], summary_text: str, stats: dict) -> bytes:
    """Résumé généré par le LLM + indicateurs de la conversation + sources citées."""
    st = _styles()
    story = _header(conv, st, "Résumé de la conversation")

    avg_rating = f"{stats['avg_rating']:.1f}/5" if stats["avg_rating"] else "—"
    table = Table(
        [["Messages", "Temps moyen", "Tokens", "Note moyenne"],
         [str(stats["n_messages"]), _fmt_ms(stats["avg_response_ms"]), str(stats["total_tokens"]), avg_rating]],
        colWidths=[4.2 * cm] * 4,
    )
    table.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, 0), _fonts()[1]), ("FONTNAME", (0, 1), (-1, 1), _fonts()[0]),
        ("FONTSIZE", (0, 0), (-1, 0), 8), ("FONTSIZE", (0, 1), (-1, 1), 12),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#64748b")),
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#eef2ff")),
        ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#c7d2fe")),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"), ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    story += [Spacer(1, 10), table, Spacer(1, 6)]
    story.extend(_markdown_flowables(summary_text, st))

    used = {}
    for m in messages:
        for s in m["sources"]:
            used.setdefault(s["document_name"], set()).add(f"l. {s['start_line']}-{s['end_line']}")
    if used:
        story.append(Paragraph("Sources consultées", st["h2"]))
        for name, ranges in used.items():
            story.append(Paragraph(escape(_clean(f"• {name} ({', '.join(sorted(ranges))})")), st["body"]))
    return _build(story, f"Résumé — {conv['title']}")
