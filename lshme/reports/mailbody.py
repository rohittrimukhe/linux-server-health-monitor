# Linux Server Health Monitor Enterprise
# Copyright (c) 2026 LRS Services Pvt Ltd, Mumbai. All rights reserved.
"""The email body for the daily, weekly and monthly reports: a short graphical summary a manager can read in a minute.

Built for mail clients, Outlook desktop included: tables and inline styles only (no CSS classes, flexbox, SVG or scripts).
Bars are table cells. The one picture is a small donut of the checks (passed / warnings / critical), drawn here as a PNG of a
few KB and sent inline (cid:), together with the LRS and customer logos when they are small enough.
build() returns (html, [(cid, png bytes), ...])."""
import math
import os
import re
from html import escape as E

from lshme.reports.periodic import Bar, Cell

GREEN, AMBER, RED, GREY = (30, 142, 62), (245, 158, 11), (197, 34, 31), (226, 232, 240)
HEX = {"ok": "#15803d", "wn": "#b45309", "cr": "#b91c1c", "na": "#64748b"}
SOFT = {"ok": "#dcfce7", "wn": "#fef3c7", "cr": "#fee2e2", "na": "#f1f5f9"}
EDGE = {"ok": "#15803d", "wn": "#d97706", "cr": "#b91c1c", "na": "#94a3b8"}
BARC = {"ok": "#16a34a", "wn": "#d97706", "cr": "#dc2626"}
STATUS = {"HEALTHY": "#15803d", "WARNING": "#b45309", "CRITICAL": "#b91c1c"}
FONT = "font-family:Inter,Segoe UI,Roboto,Helvetica,Arial,sans-serif"      # no quotes: the styles sit inside single-quoted attributes
MAX_LOGO = 150 * 1024


def donut_png(parts, size=240):
    """A ring chart: parts = [(value, (r, g, b)), ...]. Anti-aliased by 3x3 sampling; transparent outside the ring."""
    from lshme.ops.branding import encode_png
    total = float(sum(v for v, _ in parts)) or 1.0
    stops, acc = [], 0.0
    for v, col in parts:
        if v > 0:
            acc += v / total
            stops.append((acc, col))
    if not stops:
        stops = [(1.0, GREY)]
    r_out, r_in, c = size / 2.0 - 1, size / 2.0 * 0.62, size / 2.0
    px = bytearray(size * size * 4)
    sub = (1 / 6.0, 0.5, 5 / 6.0)
    for y in range(size):
        for x in range(size):
            dx0, dy0 = x - c, y - c
            d0 = math.hypot(dx0 + 0.5, dy0 + 0.5)
            if d0 < r_in - 1.5 or d0 > r_out + 1.5:
                continue
            rs = gs = bs = hit = 0
            for sy in sub:
                for sx in sub:
                    dx, dy = dx0 + sx, dy0 + sy
                    d = math.hypot(dx, dy)
                    if r_in <= d <= r_out:
                        f = (math.atan2(dx, -dy) / (2 * math.pi)) % 1.0           # clockwise from 12 o'clock
                        col = next((cc for s, cc in stops if f <= s), stops[-1][1])
                        rs, gs, bs, hit = rs + col[0], gs + col[1], bs + col[2], hit + 1
            if hit:
                o = (y * size + x) * 4
                px[o], px[o + 1], px[o + 2], px[o + 3] = rs // hit, gs // hit, bs // hit, 255 * hit // 9
    return encode_png(size, size, px)


def _find(doc, kind, titles=None):
    for title, blocks in doc.sections:
        if titles and not str(title).lower().startswith(titles):
            continue
        for b in blocks:
            if b.get("type") == kind and not b.get("excel_only"):
                return title, b
    return None, None


def _pct(s):
    m = re.match(r"^\s*([0-9]+(?:\.[0-9]+)?)\s*%", str(s))
    return float(m.group(1)) if m else None


def _level(c):
    st = c.style if isinstance(c, Cell) else ""
    return "cr" if st.endswith("cr") else "wn" if st.endswith("wn") else "ok"


def _bar(label, pct, lv, note=""):
    w = int(max(0, min(100, round(pct))))
    fill = "<td width='%d%%' bgcolor='%s' style='background:%s;height:10px;font-size:1px;line-height:1px;border-radius:5px'>&nbsp;</td>" % (w, BARC[lv], BARC[lv]) if w else ""
    rest = "<td bgcolor='#e8edf4' style='background:#e8edf4;height:10px;font-size:1px;line-height:1px'>&nbsp;</td>" if w < 100 else ""
    return ("<tr><td class='bl' style='padding:7px 0;width:150px;font-size:13px;color:#334155;%s'>%s</td>"
            "<td style='padding:7px 10px'><table role='presentation' width='100%%' cellpadding='0' cellspacing='0' style='border-collapse:collapse;background:#e8edf4;border-radius:5px'><tr>%s%s</tr></table></td>"
            "<td class='bv' style='padding:7px 0;width:118px;text-align:right;font-size:13px;font-weight:700;color:%s;%s'>%s</td></tr>") % (
        FONT, E(label), fill, rest, HEX[lv], FONT, E(("%.0f %%" % pct) + (" " + note if note else "")))


def _section(title, inner):
    return ("<tr><td class='pad' style='padding:18px 28px 4px;%s'><div style='font-size:15px;font-weight:700;color:#0f172a;margin:0 0 8px'>%s</div>%s</td></tr>"
            % (FONT, E(title), inner))


def summary(doc):
    """The figures the email shows, taken from the report itself (so the email and the attachment always agree)."""
    s = {"kpis": [], "points": [], "checks": None, "bars": [], "actions": [], "closed": 0, "sla": None, "compare": None}
    _, c = _find(doc, "table", ("this period against", "this month against", "compared with the previous"))
    if c and c.get("rows"):
        want = ("health score (average)", "cpu used (average)", "memory used (average)", "filesystem space added", "oracle data added", "days with a critical finding")
        rows = sorted((r for r in c["rows"] if str(r[0]).lower() in want), key=lambda r: want.index(str(r[0]).lower()))
        if rows:
            s["compare"] = (c["headers"], rows[:5])
    _, k = _find(doc, "kpis")
    if k:
        s["kpis"] = [(str(v), str(l), lv) for v, l, lv in k["items"]][:4]
    _, sk = _find(doc, "kpis", ("sla", "are we meeting"))
    if sk:
        s["sla"] = [(str(v), str(l), lv) for v, l, lv in sk["items"]]
    _, p = _find(doc, "points")
    if p:
        s["points"] = list(p["items"])[:6]
    _, t = _find(doc, "table", ("daily checklist", "checklist"))
    if t:
        lv = [_level(r[0]) for r in t["rows"] if r]
        s["checks"] = (lv.count("ok"), lv.count("wn"), lv.count("cr"))
        for r in t["rows"]:
            v = _pct(r[2]) if len(r) > 2 else None
            if v is not None and len(s["bars"]) < 4 and "filesystem" not in str(r[1]).lower():   # disks get their own bars below
                s["bars"].append((str(r[1]), v, _level(r[0]), ""))
    for title, blocks in doc.sections:                       # the fullest filesystems (usage bars in the report)
        for b in blocks:
            if b.get("type") == "table" and not b.get("excel_only") and b.get("rows") and len(b["rows"][0]) > 1 and isinstance(b["rows"][0][1], Bar):
                for r in sorted(b["rows"], key=lambda r: -r[1].pct)[:3]:
                    s["bars"].append(("Disk %s" % r[0], r[1].pct, r[1].level(), ""))
                break
        else:
            continue
        break
    _, a = _find(doc, "actions")
    if a:
        s["actions"] = list(a["items"])[:5]
    for title, blocks in doc.sections:
        if str(title).lower().startswith(("closed issues", "resolved during")):
            for b in blocks:
                if b.get("type") == "table":
                    s["closed"] += len(b.get("rows") or [])
                elif b.get("type") == "note":
                    m = re.match(r"^\s*(\d+)\s", b.get("text", ""))
                    s["closed"] += int(m.group(1)) if m else 0
    return s


def _logo(path, cid, images, height):
    try:
        if path and os.path.isfile(path) and os.path.getsize(path) <= MAX_LOGO and path.lower().endswith(".png"):
            with open(path, "rb") as f:
                images.append((cid, f.read()))
            return "<img src='cid:%s' alt='' height='%d' style='display:block;height:%dpx;width:auto;max-width:220px;border:0'>" % (cid, height, height)
    except OSError:
        pass
    return ""


PHONE_CSS = ("<style>@media only screen and (max-width:540px){"
             ".wrap{width:100%!important;max-width:100%!important}.outer{padding:8px 4px!important}"
             ".logo img{max-width:140px!important;height:auto!important}"
             ".tile{display:inline-block!important;width:50%!important;box-sizing:border-box}"
             ".side{display:block!important;width:100%!important;text-align:left!important;padding-top:14px!important}"
             ".pad{padding-left:16px!important;padding-right:16px!important}"
             ".bl{width:92px!important;font-size:12px!important}.bv{width:56px!important}"
             ".big{font-size:19px!important}}</style>")


def dashboard_url(cfg, host=""):
    """Where the email's button points: [smtp] dashboard_url, or this server's web interface when it listens on the network."""
    if not cfg:
        return ""
    url = (cfg.get("smtp", "smtp", "dashboard_url", "") or "").strip()
    if url:
        return url if url.startswith(("https://", "http://")) else ""
    try:
        from lshme.core.config import as_bool
        if not as_bool(cfg.get("web", "web", "enabled", "no"), False):
            return ""
        bind = cfg.get("web", "web", "bind", "127.0.0.1") or "127.0.0.1"
        if bind.startswith("127.") or bind in ("localhost", "::1") or not host:
            return ""
        scheme = "https" if as_bool(cfg.get("web", "web", "tls", "yes"), True) else "http"
        return "%s://%s:%s/" % (scheme, host if bind in ("0.0.0.0", "::") else bind, cfg.get("web", "web", "port", "8443"))
    except Exception:
        return ""


def _compare(headers, rows):
    colour = {"up": "#c5221f", "dn": "#1e8e3e"}
    th = "padding:7px 8px;font-size:11px;color:#64748b;text-transform:uppercase;letter-spacing:.5px;text-align:%s;border-bottom:1px solid #e2e8f0;" + FONT + ";"
    head = "<tr><th style='%s'>&nbsp;</th><th style='%s'>%s</th><th style='%s'>%s</th><th style='%s'>%s</th></tr>" % (
        th % "left", th % "right", E(headers[1]), th % "right", E(headers[2]), th % "left", E(headers[3] if len(headers) > 3 else "Change"))
    body = []
    for r in rows:
        ch = r[3] if len(r) > 3 else ""
        st = ch.style if isinstance(ch, Cell) else "fl"
        txt = str(ch).replace("up ", "▲ ").replace("down ", "▼ ")
        td = "padding:8px;font-size:13px;border-bottom:1px solid #f1f5f9;" + FONT + ";"
        body.append("<tr><td style='%scolor:#334155'>%s</td><td style='%stext-align:right;font-weight:700;color:#0f172a'>%s</td>"
                    "<td style='%stext-align:right;color:#64748b'>%s</td><td style='%scolor:%s;font-weight:%s'>%s</td></tr>" % (
                        td, E(str(r[0])), td, E(str(r[1])), td, E(str(r[2])), td, colour.get(st, "#64748b"), "700" if st in colour else "400", E(txt)))
    return ("<table role='presentation' width='100%%' cellpadding='0' cellspacing='0' style='border-collapse:collapse'>%s%s</table>"
            "<div style='font-size:11.5px;color:#94a3b8;margin-top:6px;%s'>Green: better &middot; red: worse &middot; grey: about the same</div>" % (head, "".join(body), FONT))


def _button(url):
    return ("<tr><td class='pad' align='left' style='padding:20px 28px 0'><table role='presentation' cellpadding='0' cellspacing='0'><tr>"
            "<td bgcolor='#2563eb' style='background:#2563eb;border-radius:10px'><a href='%s' style='display:inline-block;padding:11px 22px;color:#ffffff;"
            "font-size:14px;font-weight:700;text-decoration:none;%s'>Open the full report &rarr;</a></td></tr></table></td></tr>") % (E(url), FONT)


def build(doc, report_files=(), link=""):
    s = summary(doc)
    images = []
    status = doc.status
    scol = STATUS.get(status, "#64748b")
    att = doc.attention if getattr(doc, "attention", None) is not None else doc.counts.get("CRITICAL", 0) + doc.counts.get("WARNING", 0)
    att_txt = ("1 item needs attention" if att == 1 else "%d items need attention" % att) if att else "All clear"
    lrs = _logo(getattr(doc, "brand_logo", ""), "lrs-logo", images, 38)
    cust = _logo(getattr(doc, "customer_logo", ""), "customer-logo", images, 34)
    base = getattr(doc, "title_plain", "") or doc.title
    head = ("<tr><td class='pad' style='padding:16px 28px;background:#ffffff;border-bottom:1px solid #e2e8f0'><table role='presentation' width='100%%' cellpadding='0' cellspacing='0'><tr>"
            "<td class='logo' align='left'>%s</td><td class='logo' align='right'>%s</td></tr></table></td></tr>") % (lrs or "<b style='%s;color:#0b3d91;font-size:16px'>LRS Vitals</b>" % FONT,
                                                                                         cust or "<span style='%s;color:#334155;font-size:14px;font-weight:700'>%s</span>" % (FONT, E(getattr(doc, "customer", "") or "")))
    band = ("<tr><td class='pad' bgcolor='#123a6b' style='background:#123a6b;background-image:linear-gradient(135deg,#123a6b,#0e2d57);padding:22px 28px;%s'>"
            "<table role='presentation' width='100%%' cellpadding='0' cellspacing='0'><tr><td style='vertical-align:middle'>"
            "<div class='big' style='color:#ffffff;font-size:22px;font-weight:700;line-height:1.2'>%s</div>"
            "<div style='color:#c7d7f5;font-size:13px;margin-top:6px'>%s</div></td>"
            "<td class='side' align='right' style='vertical-align:middle;width:170px'><table role='presentation' cellpadding='0' cellspacing='0'><tr>"
            "<td bgcolor='%s' style='background:%s;border-radius:8px;padding:10px 16px;text-align:center;%s'>"
            "<div style='color:#fff;font-size:17px;font-weight:800;letter-spacing:.5px'>%s</div><div style='color:#fff;font-size:11.5px;margin-top:2px'>%s</div>"
            "</td></tr></table></td></tr></table></td></tr>") % (FONT, E(base), E(doc.subtitle), scol, scol, FONT, E(status), E(att_txt))
    rows = [head, band]
    if s["kpis"]:
        cells = "".join(
            "<td class='tile' width='%d%%' style='padding:6px;vertical-align:top'><table role='presentation' width='100%%' cellpadding='0' cellspacing='0'><tr>"
            "<td bgcolor='%s' style='background:%s;border-left:4px solid %s;border-radius:8px;padding:12px 14px;%s'>"
            "<div style='font-size:22px;font-weight:800;color:#0f172a;line-height:1.15'>%s</div>"
            "<div style='font-size:11px;color:#64748b;text-transform:uppercase;letter-spacing:.6px;margin-top:4px'>%s</div></td></tr></table></td>" % (
                100 // len(s["kpis"]), SOFT[lv], SOFT[lv], EDGE[lv], FONT, E(v), E(l)) for v, l, lv in s["kpis"])
        rows.append("<tr><td style='padding:14px 22px 0'><table role='presentation' width='100%%' cellpadding='0' cellspacing='0'><tr>%s</tr></table></td></tr>" % cells)
    if s["checks"]:
        okn, wn, cr = s["checks"]
        images.append(("checks-donut", donut_png([(okn, GREEN), (wn, AMBER), (cr, RED)])))
        total = okn + wn + cr
        legend = "".join(
            "<tr><td style='padding:5px 0'><span style='display:inline-block;width:11px;height:11px;border-radius:3px;background:%s'>&nbsp;</span></td>"
            "<td style='padding:5px 8px;font-size:14px;color:#334155;%s'>%s</td><td style='padding:5px 0;font-size:14px;font-weight:800;color:#0f172a;text-align:right;%s'>%d</td></tr>" % (
                c, FONT, E(t), FONT, n) for c, t, n in (("#1e8e3e", "Passed", okn), ("#f59e0b", "Warning", wn), ("#c5221f", "Critical", cr)))
        inner = ("<table role='presentation' cellpadding='0' cellspacing='0'><tr>"
                 "<td style='width:132px;vertical-align:middle'><img src='cid:checks-donut' width='120' height='120' alt='%d of %d checks passed' style='display:block;border:0'></td>"
                 "<td style='vertical-align:middle;padding-left:18px'><div style='font-size:26px;font-weight:800;color:#0f172a;%s'>%d of %d</div>"
                 "<div style='font-size:12px;color:#64748b;text-transform:uppercase;letter-spacing:.6px;margin-bottom:6px;%s'>checks passed</div>"
                 "<table role='presentation' cellpadding='0' cellspacing='0'>%s</table></td></tr></table>") % (okn, total, FONT, okn, total, FONT, legend)
        rows.append(_section("Checks this period", inner))
    if s["bars"]:
        rows.append(_section("Usage at its highest", "<table role='presentation' width='100%%' cellpadding='0' cellspacing='0'>%s</table>"
                             "<div style='font-size:11.5px;color:#94a3b8;margin-top:4px;%s'>Green: within limits &middot; amber: warning &middot; red: critical</div>" % (
                                 "".join(_bar(*b) for b in s["bars"]), FONT)))
    if s["sla"]:
        v, l, lv = s["sla"][0]
        extra = " &middot; ".join("%s %s" % (E(x), E(y.lower())) for x, y, _ in s["sla"][1:3])
        rows.append(_section("Service availability (SLA)",
                             "<div style='%s'><span style='font-size:26px;font-weight:800;color:%s'>%s</span> <span style='font-size:13px;color:#64748b'>%s</span>"
                             "<div style='font-size:13px;color:#334155;margin-top:2px'>%s</div></div>" % (FONT, HEX[lv], E(v), E(l), extra)))
    if s["compare"]:
        rows.append(_section("Compared with the previous period", _compare(*s["compare"])))
    if s["points"]:
        icon = {"ok": ("&#10004;", "#1e8e3e"), "wn": ("!", "#e8830c"), "cr": ("&#10006;", "#c5221f")}
        pts = "".join(
            "<tr><td style='vertical-align:top;padding:6px 10px 6px 0;width:22px'><span style='display:inline-block;width:20px;height:20px;line-height:20px;border-radius:10px;"
            "background:%s;color:#fff;text-align:center;font-size:12px;font-weight:800;%s'>%s</span></td>"
            "<td style='padding:6px 0;font-size:14px;line-height:1.5;color:#1e293b;%s'>%s</td></tr>" % (icon[lv][1], FONT, icon[lv][0], FONT, E(t)) for lv, t in s["points"])
        rows.append(_section("Summary", "<table role='presentation' width='100%%' cellpadding='0' cellspacing='0'>%s</table>" % pts))
    if s["actions"]:
        acts = "".join(
            "<tr><td style='padding:5px 0'><table role='presentation' width='100%%' cellpadding='0' cellspacing='0'><tr>"
            "<td bgcolor='%s' style='background:%s;border-left:4px solid %s;border-radius:6px;padding:10px 14px;%s'>"
            "<div style='font-size:14px;font-weight:700;color:#0f172a'>%d. %s</div><div style='font-size:12.5px;color:#64748b;margin-top:3px'>%s</div></td></tr></table></td></tr>" % (
                SOFT[lv], SOFT[lv], EDGE[lv], FONT, i, E(m), E(w)) for i, (lv, m, w) in enumerate(s["actions"], 1))
        rows.append(_section("What needs attention", "<table role='presentation' width='100%%' cellpadding='0' cellspacing='0'>%s</table>" % acts))
    elif s["checks"]:
        rows.append(_section("What needs attention", "<div style='font-size:14px;color:#1e8e3e;%s'>Nothing. Every check is within its limits.</div>" % FONT))
    if s["closed"]:
        rows.append(_section("Resolved", "<div style='font-size:14px;color:#334155;%s'>%d issue%s found in this period %s now closed and no longer count%s in the score.</div>" % (
            FONT, s["closed"], "" if s["closed"] == 1 else "s", "is" if s["closed"] == 1 else "are", "s" if s["closed"] == 1 else "")))
    if link:
        rows.append(_button(link))
    files = ", ".join(os.path.basename(p) for p in report_files)
    rows.append("<tr><td style='padding:18px 28px 22px;%s'><div style='border-top:1px solid #e2e8f0;padding-top:12px;font-size:12.5px;color:#64748b'>%s</div>"
                "<div style='font-size:11.5px;color:#94a3b8;margin-top:6px'>%s &middot; %s</div></td></tr>" % (
                    FONT, ("The full report is attached: %s" % E(files)) if files else "Open the web dashboard for the full report.",
                    E(dict(doc.meta).get("Generated by", "LRS Vitals")), E(doc.copyright)))
    html = ("<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
            "<meta name='color-scheme' content='light'><title>%s</title>%s</head>"
            "<body style='margin:0;padding:0;background:#eef2f7;%s'>"
            "<div style='display:none;max-height:0;overflow:hidden'>%s - %s</div>"
            "<table role='presentation' width='100%%' cellpadding='0' cellspacing='0' bgcolor='#eef2f7' style='background:#eef2f7'><tr><td class='outer' align='center' style='padding:20px 10px'>"
            "<table class='wrap' role='presentation' width='660' cellpadding='0' cellspacing='0' bgcolor='#ffffff' style='width:100%%;max-width:660px;background:#ffffff;border:1px solid #e2e8f0;border-radius:12px;overflow:hidden'>"
            "%s</table></td></tr></table></body></html>") % (E(doc.title), PHONE_CSS, FONT, E(status), E(att_txt), "".join(rows))
    return html, images
