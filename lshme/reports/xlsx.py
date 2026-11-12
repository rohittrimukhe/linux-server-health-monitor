"""XLSX workbook: Summary, Findings, one sheet per module, Metrics.

Uses openpyxl when installed; otherwise writes a valid minimal OOXML workbook
with the standard library (zipfile), so Excel output never depends on extras.
"""
import os
import re
import time
import zipfile
from xml.sax.saxutils import escape

from lshme.reports.doc import SEV_COLORS


def available():
    if os.environ.get("LSHME_NO_OPTIONAL"):
        return False
    try:
        import openpyxl  # noqa: F401
        return True
    except ImportError:
        return False


def sheet_name(name, used):
    base = re.sub(r"[\[\]\*\?/\\:]", "-", name)[:31] or "Sheet"
    n, out = 2, base
    while out.lower() in used:
        suffix = " %d" % n
        out = base[:31 - len(suffix)] + suffix
        n += 1
    used.add(out.lower())
    return out


def sheets_for(doc, run=None):
    """Return [(name, [rows...], header_row_indexes, severity_col_rows)] in a format-neutral way."""
    used = set()
    out = []
    summary = [[doc.title], [doc.subtitle], []]
    summary += [[k, v] for k, v in doc.meta]
    summary += [[], ["Overall status", doc.status], ["Health score", doc.score]]
    for s in ("CRITICAL", "WARNING", "UNKNOWN", "INFO"):
        summary.append([s.title() + " findings", doc.counts.get(s, 0)])
    if doc.banner:
        summary.append(["Note", doc.banner])
    summary.append([])
    hdr_idx = [len(summary)]
    summary.append(["Module" if doc.kind == "host" else "Server", "Score", "Status", "Critical", "Warning", "Summary"])
    summary += [list(r) for r in doc.module_rows]
    out.append((sheet_name("Summary", used), summary, hdr_idx))
    f_rows = [["Severity", "Module", "Code", "Component", "Message", "Recommendation", "Evidence"]]
    for f in doc.findings:
        f_rows.append([f["severity"], f["module"], f["code"], f["component"], f["message"], f.get("recommendation", ""), f.get("evidence", "")[:30000]])
    out.append((sheet_name("Findings", used), f_rows, [0]))
    if doc.recommendations:
        out.append((sheet_name("Actions", used), [["#", "Severity", "Component", "Recommended action"]] +
                    [[i, s, c, r] for i, (s, c, r) in enumerate(doc.recommendations, 1)], [0]))
    for s in doc.sections:
        if not s.tables:
            continue
        rows, hdrs = [[s.title], ["Score", s.score if s.score is not None else "n/a"], [s.summary]], []
        for n in s.notes:
            rows.append([n])
        for t in s.tables:
            rows.append([])
            rows.append([t.title])
            if t.note:
                rows.append([t.note])
            hdrs.append(len(rows))
            rows.append(list(t.headers))
            rows += [list(r) for r in t.rows]
        out.append((sheet_name(s.title, used), rows, hdrs))
    if run is not None:
        m = [["Metric", "Value"]] + [[k, v] for k, v in sorted(run.metrics().items())]
        out.append((sheet_name("Metrics", used), m, [0]))
        if run.trends.get("score_history"):
            out.append((sheet_name("Score history", used), [["Time", "Score"]] + [
                [time.strftime("%Y-%m-%d %H:%M", time.localtime(t)), s] for t, s in run.trends["score_history"] if s is not None], [0]))
    return out


def _cell_value(v):
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return v
    s = str(v)
    if re.match(r"^-?(0|[1-9]\d*)(\.\d+)?$", s) and len(s) < 15:
        try:
            return float(s) if "." in s else int(s)
        except ValueError:
            pass
    return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", s[:32000])


def render(doc, path, run=None):
    sheets = sheets_for(doc, run)
    if available():
        _render_openpyxl(sheets, path)
        return path, "openpyxl"
    _render_builtin(sheets, path)
    return path, "builtin"


def _render_openpyxl(sheets, path):
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter
    wb = Workbook()
    wb.remove(wb.active)
    head_fill = PatternFill("solid", fgColor="DCE6F1")
    sev_fill = dict((k, PatternFill("solid", fgColor=v.lstrip("#").upper())) for k, v in SEV_COLORS.items())
    for name, rows, hdrs in sheets:
        ws = wb.create_sheet(name)
        widths = {}
        for r in rows:
            ws.append([_cell_value(c) for c in r])
            for i, c in enumerate(r, 1):
                widths[i] = min(max(widths.get(i, 8), len(str(c)) + 2), 70)
        for i, w in widths.items():
            ws.column_dimensions[get_column_letter(i)].width = w
        ws["A1"].font = Font(bold=True, size=14 if name != "Findings" else 11)
        for h in hdrs:
            for cell in ws[h + 1]:
                cell.font = Font(bold=True)
                cell.fill = head_fill
        for row in ws.iter_rows():
            for cell in row:
                if isinstance(cell.value, str) and cell.value in sev_fill and cell.value != "OK":
                    cell.fill = sev_fill[cell.value]
                    cell.font = Font(bold=True, color="FFFFFF")
                if isinstance(cell.value, str) and len(cell.value) > 70:
                    cell.alignment = Alignment(wrap_text=True, vertical="top")
        if hdrs and hdrs[0] == 0:
            ws.freeze_panes = "A2"
            ws.auto_filter.ref = ws.dimensions
    wb.save(path)


def _col(n):
    s = ""
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def _render_builtin(sheets, path):
    """Minimal SpreadsheetML: inline strings, a bold header style and coloured severities."""
    sev_style = {"CRITICAL": 2, "WARNING": 3, "UNKNOWN": 4, "INFO": 5, "HEALTHY": 6}
    styles = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
              '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
              '<fonts count="3"><font><sz val="10"/><name val="Calibri"/></font><font><b/><sz val="10"/><name val="Calibri"/></font>'
              '<font><b/><sz val="10"/><color rgb="FFFFFFFF"/><name val="Calibri"/></font></fonts>'
              '<fills count="8"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill>'
              '<fill><patternFill patternType="solid"><fgColor rgb="FFDCE6F1"/></patternFill></fill>' +
              "".join('<fill><patternFill patternType="solid"><fgColor rgb="FF%s"/></patternFill></fill>' % SEV_COLORS[k].lstrip("#").upper()
                      for k in ("CRITICAL", "WARNING", "UNKNOWN", "INFO", "HEALTHY")) +
              '</fills><borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
              '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
              '<cellXfs count="7"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
              '<xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0" applyFont="1" applyFill="1"/>' +
              "".join('<xf numFmtId="0" fontId="2" fillId="%d" borderId="0" xfId="0" applyFont="1" applyFill="1"/>' % i for i in range(3, 8)) +
              '</cellXfs><cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>')
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        n = len(sheets)
        z.writestr("[Content_Types].xml",
                   '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                   '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                   '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                   '<Default Extension="xml" ContentType="application/xml"/>'
                   '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
                   '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>' +
                   "".join('<Override PartName="/xl/worksheets/sheet%d.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>' % i
                           for i in range(1, n + 1)) + '</Types>')
        z.writestr("_rels/.rels", '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                   '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>')
        z.writestr("xl/workbook.xml", '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                   '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>' +
                   "".join('<sheet name="%s" sheetId="%d" r:id="rId%d"/>' % (escape(s[0], {'"': "&quot;"}), i, i) for i, s in enumerate(sheets, 1)) +
                   '</sheets></workbook>')
        z.writestr("xl/_rels/workbook.xml.rels", '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                   '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">' +
                   "".join('<Relationship Id="rId%d" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet%d.xml"/>' % (i, i)
                           for i in range(1, n + 1)) +
                   '<Relationship Id="rId%d" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>' % (n + 1))
        z.writestr("xl/styles.xml", styles)
        for i, (name, rows, hdrs) in enumerate(sheets, 1):
            widths = {}
            xml_rows = []
            for ri, r in enumerate(rows, 1):
                cells = []
                for ci, v in enumerate(r, 1):
                    widths[ci] = min(max(widths.get(ci, 8), len(str(v)) + 2), 70)
                    ref = "%s%d" % (_col(ci), ri)
                    val = _cell_value(v)
                    style = 1 if (ri - 1) in hdrs else sev_style.get(val, 0) if isinstance(val, str) else 0
                    s_attr = ' s="%d"' % style if style else ""
                    if isinstance(val, (int, float)):
                        cells.append('<c r="%s"%s><v>%s</v></c>' % (ref, s_attr, val))
                    else:
                        txt = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", val)
                        cells.append('<c r="%s" t="inlineStr"%s><is><t xml:space="preserve">%s</t></is></c>' % (ref, s_attr, escape(txt)))
                xml_rows.append('<row r="%d">%s</row>' % (ri, "".join(cells)))
            cols = "".join('<col min="%d" max="%d" width="%d" customWidth="1"/>' % (c, c, w) for c, w in sorted(widths.items()))
            z.writestr("xl/worksheets/sheet%d.xml" % i, '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                       '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">%s<sheetData>%s</sheetData></worksheet>' % (
                           ("<cols>%s</cols>" % cols) if cols else "", "".join(xml_rows)))
