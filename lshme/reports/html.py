# Linux Server Health Monitor Enterprise
# Copyright (c) 2026 LRS Services Pvt Ltd, Mumbai. All rights reserved.
"""Self-contained HTML report (inline CSS + SVG charts, no external assets)."""
import base64
import html
import os
import time

from lshme.reports import charts as ch
from lshme.reports.doc import SEV_COLORS

E = lambda s: html.escape(str(s if s is not None else ""))

CSS = """
:root{--ink:#1d2733;--muted:#5b6876;--line:#d9dee5;--bg:#f4f6f9;--card:#fff;--accent:#0b5cad}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.45 -apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif}
.wrap{max-width:1200px;margin:0 auto;padding:24px 16px 48px}
header{background:linear-gradient(120deg,#0b3d6e,#0b5cad);color:#fff;padding:28px 16px}
header .inner{max-width:1200px;margin:0 auto;display:flex;gap:24px;align-items:center;flex-wrap:wrap}
header h1{margin:0;font-size:26px}header p{margin:4px 0 0;opacity:.85}
header img{max-height:60px;background:#fff;border-radius:8px;padding:6px 10px}
header .logos{display:flex;gap:12px;align-items:center;margin-left:auto}
.copyright{margin-top:6px;font-weight:600;color:#0b3d6e}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:16px;margin:16px 0}
.grid.wide{grid-template-columns:repeat(auto-fit,minmax(min(100%,520px),1fr))}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:16px;overflow:auto}
.card h2,.card h3{margin:0 0 10px;font-size:16px}
.kv{width:100%;border-collapse:collapse}.kv td{padding:3px 6px;border-bottom:1px solid #eef1f4;vertical-align:top}.kv td:first-child{color:var(--muted);width:40%}
.chips{display:flex;gap:8px;flex-wrap:wrap}.chip{border-radius:999px;padding:4px 12px;color:#fff;font-weight:600;font-size:13px}
.banner{background:#fff4e5;border:1px solid #ffb74d;color:#8a4b00;padding:10px 14px;border-radius:8px;margin-top:16px;font-weight:600}
table.t{border-collapse:collapse;width:100%;font-size:12.5px}table.t th{background:#eef2f7;text-align:left;position:sticky;top:0}
table.t th,table.t td{border:1px solid var(--line);padding:5px 7px;vertical-align:top}table.t tr:nth-child(even) td{background:#fafbfc}
.sev{display:inline-block;min-width:72px;text-align:center;border-radius:4px;color:#fff;font-weight:700;font-size:11px;padding:2px 6px}
details{background:var(--card);border:1px solid var(--line);border-radius:10px;margin:12px 0;padding:0 16px}
details>summary{cursor:pointer;padding:14px 0;font-weight:600;font-size:15px;list-style:none;display:flex;gap:10px;align-items:center;flex-wrap:wrap}
details>summary::-webkit-details-marker{display:none}details[open]>summary{border-bottom:1px solid var(--line);margin-bottom:10px}
.muted{color:var(--muted)}.small{font-size:12px}pre{white-space:pre-wrap;word-break:break-word;background:#f6f8fa;border:1px solid var(--line);border-radius:6px;padding:8px;font-size:11.5px;max-height:320px;overflow:auto}
.tblwrap{overflow:auto;max-height:520px;margin:6px 0 16px}h4{margin:14px 0 4px;font-size:13.5px}
footer{color:var(--muted);text-align:center;font-size:12px;margin-top:32px}
@media print{header{background:#0b3d6e!important;-webkit-print-color-adjust:exact;print-color-adjust:exact}details{break-inside:avoid}.tblwrap{max-height:none}}
"""


def sev_badge(sev):
    return '<span class="sev" style="background:%s">%s</span>' % (SEV_COLORS.get(sev, "#555"), E(sev))


def gauge(score, status):
    color = SEV_COLORS.get(status, "#555")
    r, c = 52, 2 * 3.14159 * 52
    off = c * (1 - max(0, min(100, score or 0)) / 100.0)
    return ('<svg width="140" height="140" viewBox="0 0 140 140" role="img" aria-label="score %s">'
            '<circle cx="70" cy="70" r="%d" fill="none" stroke="#e6eaef" stroke-width="14"/>'
            '<circle cx="70" cy="70" r="%d" fill="none" stroke="%s" stroke-width="14" stroke-linecap="round" '
            'stroke-dasharray="%.1f" stroke-dashoffset="%.1f" transform="rotate(-90 70 70)"/>'
            '<text x="70" y="68" text-anchor="middle" font-size="%d" font-weight="700" fill="#1d2733">%s</text>'
            '<text x="70" y="92" text-anchor="middle" font-size="12" fill="%s" font-weight="700">%s</text></svg>') % (
        score, r, r, color, c, off, 32 if len(str(score)) <= 3 else 22, score, color, E(status))


def bar_color(v, warn=80, crit=90, score_mode=False):
    if score_mode:  # low scores are bad
        return SEV_COLORS["CRITICAL"] if v < 70 else SEV_COLORS["WARNING"] if v < 90 else SEV_COLORS["HEALTHY"]
    return SEV_COLORS["CRITICAL"] if v >= crit else SEV_COLORS["WARNING"] if v >= warn else SEV_COLORS["HEALTHY"]


def hbars(items, maxv=100.0, unit="%", warn=80, crit=90, score_mode=False):
    if not items:
        return '<p class="muted">No data</p>'
    row = 30
    h = row * len(items) + 8
    out = ['<svg width="100%%" viewBox="0 0 600 %d" role="img" font-family="Helvetica,Arial,sans-serif" style="max-width:640px">' % h]
    for i, (label, v) in enumerate(items):
        v = float(v or 0)
        y = 4 + i * row
        color = bar_color(v, warn, crit, score_mode)
        w = 290 * min(v, maxv) / maxv
        out.append('<text x="0" y="%d" font-size="16" fill="#1d2733">%s</text>' % (y + 17, E(str(label)[:22])))
        out.append('<rect x="225" y="%d" width="290" height="20" fill="#eef1f4" rx="4"/>' % (y + 2))
        out.append('<rect x="225" y="%d" width="%.1f" height="20" fill="%s" rx="4"/>' % (y + 2, w, color))
        out.append('<text x="600" y="%d" font-size="16" text-anchor="end" font-weight="700" fill="#1d2733">%.0f%s</text>' % (y + 17, v, unit))
    out.append("</svg>")
    return "".join(out)


def spark(points):
    pts = [(t, s) for t, s in points if s is not None]
    if len(pts) < 2:
        return '<p class="muted small">Score history appears after a few runs.</p>'
    t0, t1 = pts[0][0], pts[-1][0]
    span = (t1 - t0) or 1
    coords = " ".join("%.1f,%.1f" % (10 + 580 * (t - t0) / span, 110 - s) for t, s in pts)
    return ('<svg width="100%%" viewBox="0 0 600 130" role="img">'
            '<line x1="10" y1="40" x2="590" y2="40" stroke="#ffcc80" stroke-dasharray="4"/>'
            '<line x1="10" y1="10" x2="590" y2="10" stroke="#e6eaef"/><line x1="10" y1="110" x2="590" y2="110" stroke="#e6eaef"/>'
            '<polyline points="%s" fill="none" stroke="#0b5cad" stroke-width="2.5"/>'
            '<text x="10" y="125" font-size="10" fill="#5b6876">%s</text><text x="590" y="125" font-size="10" text-anchor="end" fill="#5b6876">%s</text></svg>') % (
        coords, time.strftime("%Y-%m-%d", time.localtime(t0)), time.strftime("%Y-%m-%d", time.localtime(t1)))


def table_html(t):
    head = "".join("<th>%s</th>" % E(h) for h in t.headers)
    body = []
    for r in t.rows:
        cells = []
        for c in r:
            s = str(c)
            if s in SEV_COLORS and s != "OK":
                cells.append("<td>%s</td>" % sev_badge(s))
            elif s in ("BREACH", "READ-ONLY", "STALE", "MISSING", "NOT LISTENING", "EXPIRED", "FAILED", "UNREACHABLE"):
                cells.append('<td><span class="sev" style="background:%s">%s</span></td>' % (SEV_COLORS["CRITICAL"], E(s)))
            else:
                cells.append("<td>%s</td>" % E(s))
        body.append("<tr>%s</tr>" % "".join(cells))
    note = '<p class="muted small">%s</p>' % E(t.note) if t.note else ""
    return '<h4>%s</h4>%s<div class="tblwrap"><table class="t"><thead><tr>%s</tr></thead><tbody>%s</tbody></table></div>' % (
        E(t.title), note, head, "".join(body))


def findings_html(findings, show_evidence=True):
    if not findings:
        return '<p class="muted">No findings.</p>'
    rows = []
    for f in findings:
        ev = ""
        if show_evidence and f.get("evidence"):
            ev = "<details style='border:0;padding:0;margin:4px 0'><summary class='small muted' style='padding:2px 0;font-weight:400'>evidence</summary><pre>%s</pre></details>" % E(f["evidence"])
        rows.append("<tr><td>%s</td><td>%s</td><td><b>%s</b><br><span class='small muted'>%s</span>%s</td><td class='small'>%s</td></tr>" % (
            sev_badge(f["severity"]), E(f["component"]), E(f["message"]), E(f["code"]), ev, E(f.get("recommendation", ""))))
    return '<div class="tblwrap"><table class="t"><thead><tr><th>Severity</th><th>Component</th><th>Finding</th><th>Recommended action</th></tr></thead><tbody>%s</tbody></table></div>' % "".join(rows)


def logo_tag(path):
    if not path or not os.path.isfile(path):
        return ""
    ext = path.rsplit(".", 1)[-1].lower()
    mime = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "svg": "image/svg+xml", "gif": "image/gif"}.get(ext)
    if not mime or os.path.getsize(path) > 2 * 1024 * 1024:
        return ""
    with open(path, "rb") as f:
        return '<img alt="logo" src="data:%s;base64,%s">' % (mime, base64.b64encode(f.read()).decode("ascii"))


def render(doc, path, logo=""):
    c = doc.counts
    chips = "".join('<span class="chip" style="background:%s">%s %d</span>' % (SEV_COLORS[s], s.title(), c.get(s, 0))
                    for s in ("CRITICAL", "WARNING", "UNKNOWN", "INFO"))
    meta = "".join("<tr><td>%s</td><td>%s</td></tr>" % (E(k), E(v)) for k, v in doc.meta)
    ov_title, mod_hdr, chart_title = doc.overview()
    from lshme.core.model import Table
    parts = ['<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">',
             '<title>%s — %s</title><style>%s</style></head><body>' % (E(doc.title), E(doc.host or doc.subtitle), CSS),
             '<header><div class="inner"><div><h1>%s</h1><p>%s</p></div><div class="logos">%s%s</div></div></header><div class="wrap">' % (
                 E(doc.title), E(doc.subtitle), logo_tag(doc.customer_logo or logo), logo_tag(doc.brand_logo))]
    if doc.banner:
        parts.append('<div class="banner">%s</div>' % E(doc.banner))
    parts.append('<div class="grid"><div class="card" style="display:flex;gap:16px;align-items:center">%s<div><h2>Overall health</h2>'
                 '<div class="chips">%s</div><p class="small muted">%s</p></div></div>'
                 '<div class="card"><h3>Report details</h3><table class="kv">%s</table></div></div>' % (
                     gauge(doc.score, doc.status), chips, E(doc.health.get("method", "")), meta))
    pies, wide = [], []
    sev_items = ch.severity_items(doc.counts)
    if any(v for _, v, _ in sev_items):
        pies.append(ch.card("Findings by severity", ch.pie_block(sev_items, "findings")))
    if doc.module_rows and doc.kind in ("host", "fleet"):
        st = {}
        for r in doc.module_rows:
            st[r[2]] = st.get(r[2], 0) + 1
        palette = {"OK": SEV_COLORS["HEALTHY"], "HEALTHY": SEV_COLORS["HEALTHY"], "WARNING": SEV_COLORS["WARNING"],
                   "CRITICAL": SEV_COLORS["CRITICAL"], "UNREACHABLE": "#6a1b9a", "SKIPPED": "#a8b0b9"}
        mod_items = [(k.title(), st[k], palette[k]) for k in palette if st.get(k)]
        pies.append(ch.card("%s status" % ("Module" if doc.kind == "host" else "Server"),
                            ch.pie_block(mod_items, "modules" if doc.kind == "host" else "servers")))
    if doc.kind == "host":
        wide.append(ch.card("Health score history", ch.line(doc.charts["score_history"], empty="Score history appears after a few runs.")))
    if doc.charts["modules"]:
        wide.insert(0, ch.card(chart_title + " (worst first)", ch.hbars(doc.charts["modules"], 100, "", score_mode=True)))
    if doc.charts["filesystems"]:
        wide.append(ch.card("Filesystem usage", ch.columns(doc.charts["filesystems"], 100, "%", score_mode=False, y_title="Used %")))
    if pies:
        parts.append('<div class="grid">%s</div>' % "".join(pies))
    if wide:
        parts.append('<div class="grid wide">%s</div>' % "".join(wide))
    parts.append('<div class="card"><h2>%s</h2>%s</div>' % (ov_title,
                                                            table_html(Table("", mod_hdr, doc.module_rows))))
    key = [f for f in doc.findings if f["severity"] in ("CRITICAL", "WARNING", "UNKNOWN")]
    parts.append('<div class="card" style="margin-top:16px"><h2>Issues requiring attention (%d)</h2>%s</div>' % (
        len(key), findings_html(key, doc.report_type != "executive")))
    if doc.recommendations:
        parts.append('<div class="card" style="margin-top:16px"><h2>Recommended actions</h2><ol>%s</ol></div>' % "".join(
            "<li>%s <b>%s</b>: %s</li>" % (sev_badge(s), E(c_), E(r)) for s, c_, r in doc.recommendations))
    for s in doc.sections:
        status = "SKIPPED" if s.score is None and s.status == "SKIPPED" else (
            "CRITICAL" if any(f["severity"] == "CRITICAL" for f in s.findings) else
            "WARNING" if any(f["severity"] == "WARNING" for f in s.findings) else "OK")
        badge = sev_badge(status) if status != "SKIPPED" else '<span class="sev" style="background:#9e9e9e">SKIPPED</span>'
        body = ['<p class="muted">%s</p>' % E(s.summary)] if s.summary else []
        for n in s.notes:
            body.append('<p class="small muted">%s</p>' % E(n))
        info = [f for f in s.findings if f["severity"] == "INFO"]
        if info:
            body.append("<h4>Informational</h4>" + findings_html(info, False))
        body += [table_html(t) for t in s.tables]
        parts.append('<details %s><summary>%s %s <span class="muted small">score %s</span></summary>%s</details>' % (
            "open" if status in ("CRITICAL", "WARNING") else "", badge, E(s.title),
            s.score if s.score is not None else "n/a", "".join(body)))
    for title, text in doc.appendix:
        parts.append('<details><summary>Appendix — %s</summary><pre>%s</pre></details>' % (E(title), E(text)))
    parts.append('<footer>Generated %s · read-only health check · %s<div class="copyright">%s</div></footer></div></body></html>' % (
        time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(doc.generated)), E(dict(doc.meta).get("Generated by", "")), E(doc.copyright)))
    with open(path, "w", encoding="utf-8") as f:
        f.write("".join(parts))
    return path


def email_body(doc, report_files=()):
    """Compact HTML summary for the email body."""
    c = doc.counts
    key = [f for f in doc.findings if f["severity"] in ("CRITICAL", "WARNING")][:25]
    rows = "".join("<tr><td style='padding:4px 8px;border:1px solid #ddd'>%s</td><td style='padding:4px 8px;border:1px solid #ddd'>%s</td>"
                   "<td style='padding:4px 8px;border:1px solid #ddd'>%s</td></tr>" % (sev_badge(f["severity"]), E(f["component"]), E(f["message"]))
                   for f in key)
    files = "".join("<li>%s</li>" % E(os.path.basename(p)) for p in report_files)
    return ("<html><body style='font-family:Arial,sans-serif;color:#1d2733'>"
            "<h2 style='margin:0'>%s</h2><p style='margin:4px 0 12px;color:#5b6876'>%s</p>%s"
            "<p style='font-size:18px'><b style='color:%s'>%s</b> — score <b>%s/100</b> · critical %d · warning %d · unknown %d</p>"
            "%s<p>Attached reports:</p><ul>%s</ul><p style='color:#888;font-size:12px'>Generated by %s<br>%s</p></body></html>") % (
        E(doc.title), E(doc.subtitle), ("<p style='background:#fff4e5;padding:8px'>%s</p>" % E(doc.banner)) if doc.banner else "",
        SEV_COLORS.get(doc.status, "#555"), E(doc.status), doc.score, c.get("CRITICAL", 0), c.get("WARNING", 0), c.get("UNKNOWN", 0),
        ("<table style='border-collapse:collapse;font-size:13px'>%s</table>" % rows) if rows else "<p>No critical or warning findings.</p>",
        files or "<li>none</li>", E(dict(doc.meta).get("Generated by", "LSHME")), E(doc.copyright))
