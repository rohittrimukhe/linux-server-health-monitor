# Linux Server Health Monitor Enterprise
# Copyright (c) 2026 LRS Services Pvt Ltd, Mumbai. All rights reserved.
"""Management presentation (PPTX).

With python-pptx: title, executive summary, module health table, key issues,
filesystem chart, trends, recommended actions and per-module slides.
Without it: a valid text-only deck written with the standard library.
"""
import os
import time
import zipfile
from xml.sax.saxutils import escape

from lshme.reports.doc import SEV_COLORS


def available():
    if os.environ.get("LSHME_NO_OPTIONAL"):
        return False
    try:
        import pptx  # noqa: F401
        return True
    except ImportError:
        return False


def slides_for(doc):
    """Format-neutral slide list: (title, bullets, table or None)."""
    c = doc.counts
    out = [("__title__", [doc.subtitle, time.strftime("%d %B %Y", time.localtime(doc.generated))] + ["%s: %s" % kv for kv in doc.meta[:4]], None)]
    bullets = ["Overall status: %s — health score %s/100" % (doc.status, doc.score),
               "Critical: %d   Warning: %d   Unknown: %d" % (c.get("CRITICAL", 0), c.get("WARNING", 0), c.get("UNKNOWN", 0))]
    if doc.banner:
        bullets.append(doc.banner)
    crit = [f for f in doc.findings if f["severity"] == "CRITICAL"]
    if crit:
        bullets.append("Top critical issues:")
        bullets += ["  • %s — %s" % (f["component"], f["message"][:110]) for f in crit[:5]]
    else:
        bullets.append("No critical issues detected.")
    out.append(("Executive summary", bullets, None))
    if any(doc.counts.get(s, 0) for s in ("CRITICAL", "WARNING", "UNKNOWN", "INFO")) or doc.charts.get("modules"):
        out.append(("__chart_overview__", [], None))
    hdr = doc.overview()[1][:5]
    rows = [r[:5] for r in doc.module_rows]
    for i in range(0, max(1, len(rows)), 14):
        out.append(("%s%s" % (doc.overview()[0], "" if i == 0 else " (cont.)"), [], (hdr, rows[i:i + 14])))
    key = [f for f in doc.findings if f["severity"] in ("CRITICAL", "WARNING")]
    for i in range(0, len(key), 8):
        out.append(("Key issues%s" % ("" if i == 0 else " (cont.)"), [], (["Severity", "Component", "Issue"],
                    [[f["severity"], f["component"][:40], f["message"][:120]] for f in key[i:i + 8]])))
    if doc.charts["filesystems"]:
        out.append(("__chart_fs__", [], None))
    for s in doc.sections:
        if s.name == "trends" and s.tables:
            t = s.tables[0]
            out.append(("Trends", [], (t.headers[:6], [r[:6] for r in t.rows[:12]])))
            if len(s.tables) > 1 and "forecast" in s.tables[1].title.lower():
                t = s.tables[1]
                out.append(("Capacity forecast (estimate)", [], (t.headers, t.rows[:12])))
    if doc.recommendations:
        recs = ["[%s] %s: %s" % (s, c_, r[:140]) for s, c_, r in doc.recommendations]
        for i in range(0, len(recs), 7):
            out.append(("Recommended actions%s" % ("" if i == 0 else " (cont.)"), recs[i:i + 7], None))
    for s in doc.sections:
        if s.name in ("trends",) or not s.findings or not s.tables:
            continue
        t = next((x for x in s.tables if x.kind == "summary"), s.tables[0])
        out.append(("%s — score %s" % (s.title, s.score if s.score is not None else "n/a"), [s.summary] if s.summary else [],
                    (t.headers[:7], [r[:7] for r in t.rows[:10]])))
    return out


def render(doc, path):
    slides = slides_for(doc)
    if available():
        _render_pptx(doc, slides, path)
        return path, "python-pptx"
    _render_builtin(doc, slides, path)
    return path, "builtin"


def _rgb(hexcolor):
    from pptx.dml.color import RGBColor
    return RGBColor.from_string(hexcolor.lstrip("#").upper())


def _render_pptx(doc, slides, path):
    from pptx import Presentation
    from pptx.chart.data import CategoryChartData
    from pptx.enum.chart import XL_CHART_TYPE
    from pptx.util import Emu, Inches, Pt
    prs = Presentation()
    prs.slide_width, prs.slide_height = Inches(13.333), Inches(7.5)
    blank = prs.slide_layouts[6]
    navy = "#0B3D6E"

    def header(slide, title):
        bar = slide.shapes.add_shape(1, 0, 0, prs.slide_width, Inches(0.9))
        bar.fill.solid()
        bar.fill.fore_color.rgb = _rgb(navy)
        bar.line.fill.background()
        tf = bar.text_frame
        tf.text = title
        p = tf.paragraphs[0]
        p.runs[0].font.size, p.runs[0].font.bold = Pt(26), True
        p.runs[0].font.color.rgb = _rgb("#FFFFFF")
        foot = slide.shapes.add_textbox(Inches(0.4), Inches(7.05), Inches(12.5), Inches(0.3)).text_frame
        foot.text = "%s · %s · %s" % (doc.subtitle, time.strftime("%Y-%m-%d", time.localtime(doc.generated)), doc.copyright)
        if doc.brand_logo:
            try:
                plate = slide.shapes.add_shape(1, Inches(11.0), Inches(0.12), Inches(2.15), Inches(0.66))
                plate.fill.solid()
                plate.fill.fore_color.rgb = _rgb("#FFFFFF")
                plate.line.fill.background()
                slide.shapes.add_picture(doc.brand_logo, Inches(11.05), Inches(0.15), height=Inches(0.6))
            except Exception:
                pass
        foot.paragraphs[0].runs[0].font.size = Pt(10)
        foot.paragraphs[0].runs[0].font.color.rgb = _rgb("#5B6876")

    for title, bullets, table in slides:
        s = prs.slides.add_slide(blank)
        if title == "__title__":
            bg = s.shapes.add_shape(1, 0, 0, prs.slide_width, prs.slide_height)
            bg.fill.solid()
            bg.fill.fore_color.rgb = _rgb(navy)
            bg.line.fill.background()
            tb = s.shapes.add_textbox(Inches(0.8), Inches(2.0), Inches(11.5), Inches(1.2)).text_frame
            tb.text = doc.title
            tb.paragraphs[0].runs[0].font.size, tb.paragraphs[0].runs[0].font.bold = Pt(40), True
            tb.paragraphs[0].runs[0].font.color.rgb = _rgb("#FFFFFF")
            sub = s.shapes.add_textbox(Inches(0.8), Inches(3.3), Inches(11.5), Inches(3)).text_frame
            for i, b in enumerate(bullets):
                p = sub.paragraphs[0] if i == 0 else sub.add_paragraph()
                p.text = b
                p.runs[0].font.size = Pt(20 if i == 0 else 14)
                p.runs[0].font.color.rgb = _rgb("#DCE6F1")
            if doc.brand_logo:
                try:
                    plate = s.shapes.add_shape(1, Inches(0.8), Inches(0.6), Inches(4.4), Inches(1.4))
                    plate.fill.solid()
                    plate.fill.fore_color.rgb = _rgb("#FFFFFF")
                    plate.line.fill.background()
                    s.shapes.add_picture(doc.brand_logo, Inches(0.95), Inches(0.7), height=Inches(1.2))
                except Exception:
                    pass
            cr = s.shapes.add_textbox(Inches(0.8), Inches(6.8), Inches(9), Inches(0.4)).text_frame
            cr.text = doc.copyright
            cr.paragraphs[0].runs[0].font.size = Pt(12)
            cr.paragraphs[0].runs[0].font.color.rgb = _rgb("#DCE6F1")
            badge = s.shapes.add_shape(1, Inches(10.3), Inches(5.6), Inches(2.4), Inches(1.1))
            badge.fill.solid()
            badge.fill.fore_color.rgb = _rgb(SEV_COLORS.get(doc.status, "#555555"))
            badge.line.fill.background()
            badge.text_frame.text = "%s  %s" % (doc.score, doc.status)
            badge.text_frame.paragraphs[0].runs[0].font.size = Pt(22)
            badge.text_frame.paragraphs[0].runs[0].font.bold = True
            continue
        if title == "__chart_overview__":
            header(s, "Health at a glance")
            sev = [(x.title(), doc.counts.get(x, 0), SEV_COLORS[x]) for x in ("CRITICAL", "WARNING", "UNKNOWN", "INFO") if doc.counts.get(x, 0)]
            if sev:
                cd = CategoryChartData()
                cd.categories = [l for l, _, _ in sev]
                cd.add_series("Findings", [v for _, v, _ in sev])
                pie = s.shapes.add_chart(XL_CHART_TYPE.DOUGHNUT, Inches(0.4), Inches(1.15), Inches(5.6), Inches(5.6), cd).chart
                pie.has_title, pie.has_legend = True, True
                pie.chart_title.text_frame.text = "Findings by severity"
                pie.plots[0].has_data_labels = True
                for i, (_, _, c) in enumerate(sev):
                    pt = pie.plots[0].series[0].points[i]
                    pt.format.fill.solid()
                    pt.format.fill.fore_color.rgb = _rgb(c)
            items = doc.charts.get("modules", [])[:14]
            if items:
                cd = CategoryChartData()
                cd.categories = [str(k)[:26] for k, _ in items]
                cd.add_series(doc.overview()[2], [float(v or 0) for _, v in items])
                bar = s.shapes.add_chart(XL_CHART_TYPE.BAR_CLUSTERED, Inches(6.2), Inches(1.15), Inches(6.8), Inches(5.6), cd).chart
                bar.has_legend = False
                bar.has_title = True
                bar.chart_title.text_frame.text = doc.overview()[2]
                bar.value_axis.maximum_scale, bar.value_axis.minimum_scale = 100, 0
                for i, (_, v) in enumerate(items):
                    pt = bar.plots[0].series[0].points[i]
                    pt.format.fill.solid()
                    v = float(v or 0)
                    pt.format.fill.fore_color.rgb = _rgb(SEV_COLORS["CRITICAL"] if v < 70 else SEV_COLORS["WARNING"] if v < 90 else SEV_COLORS["HEALTHY"])
            continue
        if title == "__chart_fs__":
            header(s, "Filesystem utilisation")
            cd = CategoryChartData()
            items = doc.charts["filesystems"][:15]
            cd.categories = [str(k)[:30] for k, _ in items]
            cd.add_series("Used %", [float(v or 0) for _, v in items])
            ch = s.shapes.add_chart(XL_CHART_TYPE.BAR_CLUSTERED, Inches(0.6), Inches(1.2), Inches(12), Inches(5.7), cd).chart
            ch.has_legend = False
            ch.value_axis.maximum_scale, ch.value_axis.minimum_scale = 100, 0
            plot = ch.plots[0]
            plot.has_data_labels = True
            plot.data_labels.number_format = '0"%"'
            plot.data_labels.number_format_is_linked = False
            for i, (_, v) in enumerate(items):
                pt = plot.series[0].points[i]
                pt.format.fill.solid()
                v = float(v or 0)
                pt.format.fill.fore_color.rgb = _rgb(SEV_COLORS["CRITICAL"] if v >= 90 else SEV_COLORS["WARNING"] if v >= 80 else SEV_COLORS["HEALTHY"])
            continue
        header(s, title)
        top = Inches(1.15)
        if bullets:
            h = Inches(0.45 * len(bullets) + 0.3) if table else Inches(5.6)
            tf = s.shapes.add_textbox(Inches(0.6), top, Inches(12.1), h).text_frame
            tf.word_wrap = True
            for i, b in enumerate(bullets):
                p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
                p.text = b
                p.runs[0].font.size = Pt(18 if not table else 13)
                if b.startswith("Overall status"):
                    p.runs[0].font.bold = True
                    p.runs[0].font.color.rgb = _rgb(SEV_COLORS.get(doc.status, "#333333"))
            top = top + h
        if table:
            headers, rows = table
            rows = rows or [["(none)"] + [""] * (len(headers) - 1)]
            nrows, ncols = len(rows) + 1, len(headers)
            height = min(Inches(5.7), Emu(int(Inches(0.38) * nrows)))
            gt = s.shapes.add_table(nrows, ncols, Inches(0.5), top, Inches(12.3), height).table
            for j, h in enumerate(headers):
                cell = gt.cell(0, j)
                cell.text = str(h)
                cell.text_frame.paragraphs[0].runs[0].font.size = Pt(12)
                cell.text_frame.paragraphs[0].runs[0].font.bold = True
            for i, r in enumerate(rows, 1):
                for j in range(ncols):
                    v = str(r[j]) if j < len(r) else ""
                    cell = gt.cell(i, j)
                    cell.text = v
                    run = cell.text_frame.paragraphs[0].runs[0] if cell.text_frame.paragraphs[0].runs else None
                    if run:
                        run.font.size = Pt(11)
                    if v in ("CRITICAL", "WARNING", "UNKNOWN") and run:
                        cell.fill.solid()
                        cell.fill.fore_color.rgb = _rgb(SEV_COLORS[v])
                        run.font.color.rgb = _rgb("#FFFFFF")
                        run.font.bold = True
    prs.save(path)


# ---------------------------------------------------------------------------
# dependency-free fallback
NS = ('xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
      'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
      'xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"')
THEME = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><a:theme xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" name="LSHME">'
         '<a:themeElements><a:clrScheme name="LSHME"><a:dk1><a:srgbClr val="000000"/></a:dk1><a:lt1><a:srgbClr val="FFFFFF"/></a:lt1>'
         '<a:dk2><a:srgbClr val="0B3D6E"/></a:dk2><a:lt2><a:srgbClr val="EEF2F7"/></a:lt2><a:accent1><a:srgbClr val="0B5CAD"/></a:accent1>'
         '<a:accent2><a:srgbClr val="C62828"/></a:accent2><a:accent3><a:srgbClr val="EF8F00"/></a:accent3><a:accent4><a:srgbClr val="2E7D32"/></a:accent4>'
         '<a:accent5><a:srgbClr val="6D6D6D"/></a:accent5><a:accent6><a:srgbClr val="1565C0"/></a:accent6><a:hlink><a:srgbClr val="0B5CAD"/></a:hlink>'
         '<a:folHlink><a:srgbClr val="6A1B9A"/></a:folHlink></a:clrScheme><a:fontScheme name="LSHME"><a:majorFont><a:latin typeface="Calibri"/>'
         '<a:ea typeface=""/><a:cs typeface=""/></a:majorFont><a:minorFont><a:latin typeface="Calibri"/><a:ea typeface=""/><a:cs typeface=""/></a:minorFont>'
         '</a:fontScheme><a:fmtScheme name="LSHME"><a:fillStyleLst><a:solidFill><a:schemeClr val="phClr"/></a:solidFill><a:solidFill><a:schemeClr val="phClr"/></a:solidFill>'
         '<a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:fillStyleLst><a:lnStyleLst><a:ln w="9525"><a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:ln>'
         '<a:ln w="25400"><a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:ln><a:ln w="38100"><a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:ln>'
         '</a:lnStyleLst><a:effectStyleLst><a:effectStyle><a:effectLst/></a:effectStyle><a:effectStyle><a:effectLst/></a:effectStyle><a:effectStyle><a:effectLst/></a:effectStyle>'
         '</a:effectStyleLst><a:bgFillStyleLst><a:solidFill><a:schemeClr val="phClr"/></a:solidFill><a:solidFill><a:schemeClr val="phClr"/></a:solidFill>'
         '<a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:bgFillStyleLst></a:fmtScheme></a:themeElements></a:theme>')
EMPTY_TREE = ('<p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr>'
              '<p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/><a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr>')


def _box(sid, name, x, y, w, h, paragraphs, fill=None):
    fill_xml = '<a:solidFill><a:srgbClr val="%s"/></a:solidFill>' % fill if fill else "<a:noFill/>"
    paras = []
    for text, size, bold, color in paragraphs:
        paras.append('<a:p><a:r><a:rPr lang="en-US" sz="%d" b="%d" dirty="0"><a:solidFill><a:srgbClr val="%s"/></a:solidFill></a:rPr>'
                     '<a:t>%s</a:t></a:r></a:p>' % (size * 100, 1 if bold else 0, color, escape(str(text))))
    return ('<p:sp><p:nvSpPr><p:cNvPr id="%d" name="%s"/><p:cNvSpPr txBox="1"/><p:nvPr/></p:nvSpPr><p:spPr><a:xfrm><a:off x="%d" y="%d"/>'
            '<a:ext cx="%d" cy="%d"/></a:xfrm><a:prstGeom prst="rect"><a:avLst/></a:prstGeom>%s</p:spPr><p:txBody><a:bodyPr wrap="square" lIns="91440" tIns="45720" rIns="91440" bIns="45720"><a:normAutofit/></a:bodyPr>'
            '<a:lstStyle/>%s</p:txBody></p:sp>') % (sid, name, x, y, w, h, fill_xml, "".join(paras) or "<a:p/>")


def _render_builtin(doc, slides, path):
    W, H = 12192000, 6858000
    xml_slides = []
    for title, bullets, table in slides:
        shapes = []
        if title == "__title__":
            shapes.append(_box(2, "bg", 0, 0, W, H, [], "0B3D6E"))
            shapes.append(_box(3, "title", 700000, 1800000, W - 1400000, 1000000, [(doc.title, 40, True, "FFFFFF")]))
            shapes.append(_box(4, "sub", 700000, 2900000, W - 1400000, 3000000, [(b, 18 if i == 0 else 14, False, "DCE6F1") for i, b in enumerate(bullets)] + [(doc.copyright, 12, False, "DCE6F1")]))
            shapes.append(_box(5, "score", W - 3200000, H - 1500000, 2500000, 900000, [("%s  %s" % (doc.score, doc.status), 22, True, "FFFFFF")],
                               SEV_COLORS.get(doc.status, "#555555").lstrip("#")))
        else:
            if title == "__chart_overview__":
                title = "Health at a glance"
                bullets = ["%s findings: %d" % (x.title(), doc.counts.get(x, 0)) for x in ("CRITICAL", "WARNING", "UNKNOWN", "INFO")] + \
                          ["%s: %s" % (k, v) for k, v in doc.charts.get("modules", [])[:10]]
            if title == "__chart_fs__":
                title = "Filesystem utilisation"
                bullets = ["%s: %.0f%%" % (k, float(v or 0)) for k, v in doc.charts["filesystems"][:14]]
            shapes.append(_box(2, "header", 0, 0, W, 820000, [(title, 26, True, "FFFFFF")], "0B3D6E"))
            lines = [(b, 16, b.startswith("Overall status"), "1D2733") for b in bullets]
            if table:
                headers, rows = table
                lines.append((" | ".join(str(h) for h in headers), 12, True, "0B3D6E"))
                for r in rows:
                    sev = str(r[0]) if r else ""
                    lines.append((" | ".join(str(c) for c in r), 11, False, SEV_COLORS.get(sev, "#1D2733").lstrip("#") if sev in ("CRITICAL", "WARNING") else "1D2733"))
            shapes.append(_box(3, "body", 500000, 1000000, W - 1000000, H - 1500000, lines))
            shapes.append(_box(4, "footer", 400000, H - 420000, W - 800000, 300000, [("%s · %s" % (doc.subtitle, doc.copyright), 10, False, "5B6876")]))
        xml_slides.append('<?xml version="1.0" encoding="UTF-8" standalone="yes"?><p:sld %s><p:cSld><p:spTree>%s%s</p:spTree></p:cSld>'
                          '<p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr></p:sld>' % (NS, EMPTY_TREE, "".join(shapes)))
    n = len(xml_slides)
    rel = lambda rid, typ, target: '<Relationship Id="%s" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/%s" Target="%s"/>' % (rid, typ, target)
    rels = lambda items: '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">%s</Relationships>' % "".join(items)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                   '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/>'
                   '<Override PartName="/ppt/presentation.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"/>'
                   '<Override PartName="/ppt/slideMasters/slideMaster1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slideMaster+xml"/>'
                   '<Override PartName="/ppt/slideLayouts/slideLayout1.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slideLayout+xml"/>'
                   '<Override PartName="/ppt/theme/theme1.xml" ContentType="application/vnd.openxmlformats-officedocument.theme+xml"/>' +
                   "".join('<Override PartName="/ppt/slides/slide%d.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.slide+xml"/>' % i for i in range(1, n + 1)) +
                   '</Types>')
        z.writestr("_rels/.rels", rels([rel("rId1", "officeDocument", "ppt/presentation.xml")]))
        z.writestr("ppt/presentation.xml", '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><p:presentation %s>'
                   '<p:sldMasterIdLst><p:sldMasterId id="2147483648" r:id="rId1"/></p:sldMasterIdLst><p:sldIdLst>%s</p:sldIdLst>'
                   '<p:sldSz cx="%d" cy="%d"/><p:notesSz cx="6858000" cy="9144000"/></p:presentation>' % (
                       NS, "".join('<p:sldId id="%d" r:id="rId%d"/>' % (256 + i, i + 1) for i in range(1, n + 1)), W, H))
        z.writestr("ppt/_rels/presentation.xml.rels", rels([rel("rId1", "slideMaster", "slideMasters/slideMaster1.xml")] +
                   [rel("rId%d" % (i + 1), "slide", "slides/slide%d.xml" % i) for i in range(1, n + 1)] +
                   [rel("rId%d" % (n + 2), "theme", "theme/theme1.xml")]))
        z.writestr("ppt/slideMasters/slideMaster1.xml", '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><p:sldMaster %s><p:cSld><p:spTree>%s</p:spTree></p:cSld>'
                   '<p:clrMap bg1="lt1" tx1="dk1" bg2="lt2" tx2="dk2" accent1="accent1" accent2="accent2" accent3="accent3" accent4="accent4" accent5="accent5" accent6="accent6" hlink="hlink" folHlink="folHlink"/>'
                   '<p:sldLayoutIdLst><p:sldLayoutId id="2147483649" r:id="rId1"/></p:sldLayoutIdLst></p:sldMaster>' % (NS, EMPTY_TREE))
        z.writestr("ppt/slideMasters/_rels/slideMaster1.xml.rels", rels([rel("rId1", "slideLayout", "../slideLayouts/slideLayout1.xml"),
                                                                         rel("rId2", "theme", "../theme/theme1.xml")]))
        z.writestr("ppt/slideLayouts/slideLayout1.xml", '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><p:sldLayout %s type="blank" preserve="1">'
                   '<p:cSld name="Blank"><p:spTree>%s</p:spTree></p:cSld><p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr></p:sldLayout>' % (NS, EMPTY_TREE))
        z.writestr("ppt/slideLayouts/_rels/slideLayout1.xml.rels", rels([rel("rId1", "slideMaster", "../slideMasters/slideMaster1.xml")]))
        z.writestr("ppt/theme/theme1.xml", THEME)
        for i, x in enumerate(xml_slides, 1):
            z.writestr("ppt/slides/slide%d.xml" % i, x)
            z.writestr("ppt/slides/_rels/slide%d.xml.rels" % i, rels([rel("rId1", "slideLayout", "../slideLayouts/slideLayout1.xml")]))
