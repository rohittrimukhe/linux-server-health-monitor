# Linux Server Health Monitor Enterprise
# Copyright (c) 2026 LRS Services Pvt Ltd, Mumbai. All rights reserved.
"""PDF report with ReportLab (charts, coloured tables); falls back to minipdf."""
import os
import time
from xml.sax.saxutils import escape

from lshme.reports import minipdf
from lshme.reports.doc import SEV_COLORS


def available():
    if os.environ.get("LSHME_NO_OPTIONAL"):
        return False
    try:
        import reportlab  # noqa: F401
        return True
    except ImportError:
        return False


def render(doc, path, logo=""):
    if not available():
        minipdf.render(doc, path)
        return path, "builtin"
    from reportlab.graphics.charts.barcharts import HorizontalBarChart
    from reportlab.graphics.charts.lineplots import LinePlot
    from reportlab.graphics.shapes import Drawing, String
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_LEFT
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import (Image, KeepTogether, PageBreak, Paragraph, Preformatted, SimpleDocTemplate, Spacer,
                                    Table, TableStyle)

    ss = getSampleStyleSheet()
    st = {
        "title": ParagraphStyle("t", parent=ss["Title"], fontSize=22, textColor=colors.HexColor("#0b3d6e"), alignment=TA_LEFT),
        "sub": ParagraphStyle("s", parent=ss["Normal"], fontSize=11, textColor=colors.HexColor("#5b6876")),
        "h1": ParagraphStyle("h1", parent=ss["Heading1"], fontSize=15, textColor=colors.HexColor("#0b3d6e"), spaceBefore=10),
        "h2": ParagraphStyle("h2", parent=ss["Heading2"], fontSize=12, textColor=colors.HexColor("#0b3d6e"), spaceBefore=8),
        "h3": ParagraphStyle("h3", parent=ss["Heading3"], fontSize=9.5, spaceBefore=6, spaceAfter=2),
        "n": ParagraphStyle("n", parent=ss["Normal"], fontSize=8.5, leading=10.5),
        "cell": ParagraphStyle("c", parent=ss["Normal"], fontSize=7, leading=8.5),
        "cellb": ParagraphStyle("cb", parent=ss["Normal"], fontSize=7, leading=8.5, fontName="Helvetica-Bold"),
        "muted": ParagraphStyle("m", parent=ss["Normal"], fontSize=7.5, textColor=colors.HexColor("#5b6876")),
        "code": ParagraphStyle("code", parent=ss["Code"], fontSize=6.5, leading=7.5),
    }
    P = lambda s, style="cell": Paragraph(escape(str(s)).replace("\n", "<br/>"), st[style])
    page = A4
    W = page[0] - 30 * mm

    def footer(canvas, d):
        canvas.saveState()
        canvas.setFont("Helvetica", 7)
        canvas.setFillColor(colors.HexColor("#5b6876"))
        canvas.drawString(15 * mm, 9 * mm, "%s — %s" % (doc.subtitle, time.strftime("%Y-%m-%d %H:%M", time.localtime(doc.generated))))
        canvas.drawRightString(page[0] - 15 * mm, 9 * mm, "Page %d" % d.page)
        canvas.drawCentredString(page[0] / 2.0, 5 * mm, doc.copyright)
        if doc.brand_logo:
            try:
                canvas.drawImage(doc.brand_logo, page[0] - 15 * mm - 30 * mm, page[1] - 9 * mm, width=30 * mm, height=7 * mm,
                                 preserveAspectRatio=True, mask="auto", anchor="ne")
            except Exception:
                pass
        canvas.setStrokeColor(colors.HexColor("#0b5cad"))
        canvas.setLineWidth(2)
        canvas.line(15 * mm, page[1] - 10 * mm, page[0] - 15 * mm, page[1] - 10 * mm)
        canvas.restoreState()

    def table(headers, rows, col_weights=None, sev_col=None, zebra=True, max_rows=800):
        data = [[P(h, "cellb") for h in headers]]
        for r in rows[:max_rows]:
            data.append([P(c) for c in (list(r) + [""] * (len(headers) - len(r)))[:len(headers)]])
        if col_weights is None:
            lens = [max([len(str(h))] + [min(len(str(r[i])) if i < len(r) else 0, 60) for r in rows[:200]]) + 2 for i, h in enumerate(headers)]
            col_weights = lens
        tot = float(sum(col_weights)) or 1
        widths = [max(12 * mm, W * w / tot) for w in col_weights]
        scale = W / sum(widths)
        widths = [w * scale for w in widths]
        t = Table(data, colWidths=widths, repeatRows=1)
        style = [("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e8eef6")), ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#c9d1db")),
                 ("VALIGN", (0, 0), (-1, -1), "TOP"), ("TOPPADDING", (0, 0), (-1, -1), 2), ("BOTTOMPADDING", (0, 0), (-1, -1), 2)]
        if zebra:
            for i in range(2, len(data), 2):
                style.append(("BACKGROUND", (0, i), (-1, i), colors.HexColor("#f7f9fb")))
        for i, r in enumerate(rows[:max_rows], 1):
            for j, c in enumerate(r):
                if str(c) in ("CRITICAL", "WARNING", "UNKNOWN", "READ-ONLY", "STALE", "MISSING", "NOT LISTENING", "EXPIRED", "FAILED", "BREACH", "UNREACHABLE"):
                    col = SEV_COLORS.get(str(c), SEV_COLORS["CRITICAL"])
                    style.append(("BACKGROUND", (j, i), (j, i), colors.HexColor(col)))
                    style.append(("TEXTCOLOR", (j, i), (j, i), colors.white))
        t.setStyle(TableStyle(style))
        return t

    def bar_chart(items, title, score_mode=False):
        # ReportLab draws the first category at the bottom: sort so the worst ends up on top
        items = sorted(items, key=lambda kv: -float(kv[1] or 0) if score_mode else float(kv[1] or 0))[-20:]
        h = 30 + 14 * len(items)
        dr = Drawing(W, h)
        bc = HorizontalBarChart()
        bc.x, bc.y, bc.width, bc.height = 120, 10, W - 150, h - 30
        bc.data = [[float(v or 0) for _, v in items]]
        bc.categoryAxis.categoryNames = [str(k)[:24] for k, _ in items]
        bc.categoryAxis.labels.fontSize = 6.5
        bc.valueAxis.valueMin, bc.valueAxis.valueMax, bc.valueAxis.valueStep = 0, 100, 20
        bc.valueAxis.labels.fontSize = 6.5
        bc.barWidth = 8
        for i, (_, v) in enumerate(items):
            v = float(v or 0)
            if score_mode:
                c = SEV_COLORS["CRITICAL"] if v < 70 else SEV_COLORS["WARNING"] if v < 90 else SEV_COLORS["HEALTHY"]
            else:
                c = SEV_COLORS["CRITICAL"] if v >= 90 else SEV_COLORS["WARNING"] if v >= 80 else SEV_COLORS["HEALTHY"]
            bc.bars[(0, i)].fillColor = colors.HexColor(c)
        bc.bars.strokeColor = None
        bc.barLabelFormat = "%.0f"
        bc.barLabels.nudge = 9
        bc.barLabels.fontSize = 6.5
        bc.barLabels.fontName = "Helvetica-Bold"
        dr.add(bc)
        dr.add(String(0, h - 10, title, fontSize=9, fontName="Helvetica-Bold", fillColor=colors.HexColor("#0b3d6e")))
        return dr

    story = []
    logos = [p for p in (doc.brand_logo, doc.customer_logo or logo) if p and os.path.isfile(p)]
    cells = []
    for p in logos:
        try:
            cells.append(Image(p, width=60 * mm, height=18 * mm, kind="proportional"))
        except Exception:
            pass
    if cells:
        lt = Table([cells], colWidths=[W / len(cells)] * len(cells))
        lt.setStyle(TableStyle([("ALIGN", (0, 0), (0, 0), "LEFT"), ("ALIGN", (-1, 0), (-1, 0), "RIGHT"), ("VALIGN", (0, 0), (-1, -1), "MIDDLE")]))
        story += [lt, Spacer(1, 6)]
    story += [Paragraph(escape(doc.title), st["title"]), Paragraph(escape(doc.subtitle), st["sub"]), Spacer(1, 8)]
    meta_rows = [[P(k, "cellb"), P(v)] for k, v in doc.meta]
    mt = Table(meta_rows, colWidths=[35 * mm, W - 35 * mm])
    mt.setStyle(TableStyle([("LINEBELOW", (0, 0), (-1, -1), 0.2, colors.HexColor("#e1e6ec")), ("VALIGN", (0, 0), (-1, -1), "TOP")]))
    story.append(mt)
    if doc.banner:
        story += [Spacer(1, 6), Paragraph("<b>%s</b>" % escape(doc.banner), ParagraphStyle("b", parent=st["n"], textColor=colors.HexColor("#8a4b00"), backColor=colors.HexColor("#fff4e5"), borderPadding=4))]
    c = doc.counts
    col = SEV_COLORS.get(doc.status, "#555")
    summary = Table([[Paragraph('<font size="30" color="%s"><b>%s</b></font>' % (col, doc.score), st["n"]),
                      Paragraph('<font size="14" color="%s"><b>%s</b></font><br/><br/>Critical <b>%d</b> &nbsp; Warning <b>%d</b> &nbsp; '
                                'Unknown <b>%d</b> &nbsp; Info <b>%d</b><br/><font size="7" color="#5b6876">%s</font>' % (
                                    col, doc.status, c.get("CRITICAL", 0), c.get("WARNING", 0), c.get("UNKNOWN", 0), c.get("INFO", 0),
                                    escape(doc.health.get("method", ""))), st["n"])]], colWidths=[35 * mm, W - 35 * mm])
    summary.setStyle(TableStyle([("BOX", (0, 0), (-1, -1), 0.8, colors.HexColor(col)), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                                 ("TOPPADDING", (0, 0), (-1, -1), 8), ("BOTTOMPADDING", (0, 0), (-1, -1), 8)]))
    story += [Spacer(1, 10), summary, Spacer(1, 10)]
    sev = [(s.title(), doc.counts.get(s, 0), SEV_COLORS[s]) for s in ("CRITICAL", "WARNING", "UNKNOWN", "INFO") if doc.counts.get(s, 0)]
    if sev:
        from reportlab.graphics.charts.piecharts import Pie
        dr = Drawing(W, 130)
        pie = Pie()
        pie.x, pie.y, pie.width, pie.height = 10, 10, 105, 105
        pie.data = [v for _, v, _ in sev]
        total = float(sum(v for _, v, _ in sev))
        pie.labels = ["%.0f%%" % (100 * v / total) if v / total >= 0.05 else "" for _, v, _ in sev]
        pie.simpleLabels = 1
        pie.slices.labelRadius = 0.72
        pie.slices.fontColor = colors.white
        pie.slices.fontName = "Helvetica-Bold"
        pie.slices.fontSize = 8
        pie.innerRadiusFraction = 0.55
        pie.slices.strokeColor = colors.white
        pie.slices.strokeWidth = 1.5
        for i, (_, _, c) in enumerate(sev):
            pie.slices[i].fillColor = colors.HexColor(c)
        dr.add(pie)
        dr.add(String(140, 112, "Findings by severity", fontSize=9, fontName="Helvetica-Bold", fillColor=colors.HexColor("#0b3d6e")))
        for i, (label, v, c) in enumerate(sev):
            y = 92 - i * 16
            from reportlab.graphics.shapes import Rect
            dr.add(Rect(140, y - 2, 9, 9, fillColor=colors.HexColor(c), strokeColor=None))
            dr.add(String(154, y, "%s  %d  (%.0f%%)" % (label, v, 100 * v / total), fontSize=8.5))
        story.append(dr)
    if doc.charts["modules"]:
        story.append(bar_chart(doc.charts["modules"], doc.overview()[2], True))
    if doc.charts["filesystems"]:
        story.append(bar_chart(doc.charts["filesystems"], "Filesystem usage %"))
    sh = [(t, s) for t, s in doc.charts["score_history"] if s is not None]
    if len(sh) >= 2:
        dr = Drawing(W, 110)
        lp = LinePlot()
        lp.x, lp.y, lp.width, lp.height = 30, 15, W - 50, 75
        t0 = sh[0][0]
        lp.data = [[((t - t0) / 86400.0, s) for t, s in sh]]
        lp.yValueAxis.valueMin, lp.yValueAxis.valueMax = 0, 100
        lp.lines[0].strokeColor = colors.HexColor("#0b5cad")
        lp.lines[0].strokeWidth = 1.5
        lp.xValueAxis.labels.fontSize = lp.yValueAxis.labels.fontSize = 6.5
        dr.add(lp)
        dr.add(String(0, 98, "Health score history (days since %s)" % time.strftime("%Y-%m-%d", time.localtime(t0)),
                      fontSize=9, fontName="Helvetica-Bold", fillColor=colors.HexColor("#0b3d6e")))
        story.append(dr)
    story.append(Paragraph(doc.overview()[0], st["h1"]))
    story.append(table(doc.overview()[1],
                       doc.module_rows, [22, 7, 10, 7, 7, 47]))
    key = [f for f in doc.findings if f["severity"] in ("CRITICAL", "WARNING", "UNKNOWN")]
    story.append(Paragraph("Issues requiring attention (%d)" % len(key), st["h1"]))
    if key:
        story.append(table(["Severity", "Component", "Finding", "Recommended action"],
                           [[f["severity"], f["component"], f["message"], f.get("recommendation", "")] for f in key], [10, 20, 40, 30]))
    else:
        story.append(Paragraph("No critical, warning or unknown findings.", st["n"]))
    if doc.recommendations:
        story.append(Paragraph("Recommended actions", st["h1"]))
        for i, (s, comp, r) in enumerate(doc.recommendations, 1):
            story.append(Paragraph('%d. <font color="%s"><b>[%s]</b></font> <b>%s</b>: %s' % (i, SEV_COLORS.get(s, "#555"), s, escape(comp), escape(r)), st["n"]))
    for s in doc.sections:
        story.append(PageBreak() if doc.report_type != "executive" and s is doc.sections[0] else Spacer(1, 6))
        story.append(Paragraph("%s <font size='9' color='#5b6876'>score %s</font>" % (escape(s.title), s.score if s.score is not None else "n/a"), st["h1"]))
        if s.summary:
            story.append(Paragraph(escape(s.summary), st["muted"]))
        for n in s.notes:
            story.append(Paragraph(escape(n), st["muted"]))
        info = [f for f in s.findings if f["severity"] == "INFO"]
        if info and doc.report_type != "executive":
            story.append(Paragraph("Informational", st["h3"]))
            story.append(table(["Component", "Finding"], [[f["component"], f["message"]] for f in info], [30, 70]))
        for t in s.tables:
            block = [Paragraph(escape(t.title), st["h3"])]
            if t.note:
                block.append(Paragraph(escape(t.note), st["muted"]))
            block.append(table(t.headers, t.rows))
            story.append(KeepTogether(block) if len(t.rows) < 15 else block[0])
            if len(t.rows) >= 15:
                story.extend(block[1:])
    for title, text in doc.appendix:
        story += [PageBreak(), Paragraph("Appendix — %s" % escape(title), st["h2"]), Preformatted(text[:60000], st["code"])]
    story += [Spacer(1, 12), Paragraph(escape(doc.copyright), st["muted"])]
    sd = SimpleDocTemplate(path, pagesize=page, leftMargin=15 * mm, rightMargin=15 * mm, topMargin=15 * mm, bottomMargin=17 * mm,
                           title=doc.title, author=doc.copyright, subject=doc.subtitle)
    sd.build(story, onFirstPage=footer, onLaterPages=footer)
    return path, "reportlab"
