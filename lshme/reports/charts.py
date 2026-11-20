# Linux Server Health Monitor Enterprise
# Copyright (c) 2026 LRS Services Pvt Ltd, Mumbai. All rights reserved.
"""Dependency-free SVG charts for HTML pages: donut/pie, horizontal bars, columns.

Design rules (shared with the JavaScript charts): one palette, value and
percentage labels on the chart, threshold lines, worst items first, axes with
ticks, and native <title> tooltips. Pure inline SVG, so charts print, work
offline and survive email clients.
"""
import html
import math

from lshme.reports.doc import SEV_COLORS

E = lambda s: html.escape(str(s if s is not None else ""))
FONT = 'font-family="Segoe UI,Helvetica,Arial,sans-serif"'
TRACK = "rgba(127,140,155,.16)"
GRID = "rgba(127,140,155,.28)"
OK, WARN, CRIT = SEV_COLORS["HEALTHY"], SEV_COLORS["WARNING"], SEV_COLORS["CRITICAL"]


def _fmt(v):
    v = float(v)
    return ("%d" % v) if v == int(v) else ("%.1f" % v)


def donut(items, title="", size=210, hole=0.62, center_label=None):
    """items: [(label, value, color)]. Slices >= 5% carry a percentage label."""
    items = [(l, float(v), c) for l, v, c in items if v and float(v) > 0]
    total = sum(v for _, v, _ in items)
    r = size / 2.0 - 6
    ri = r * hole
    cx = cy = size / 2.0
    out = ['<svg viewBox="0 0 %d %d" width="%d" height="%d" role="img" aria-label="%s" %s>' % (size, size, size, size, E(title), FONT)]
    if not total:
        out.append('<circle cx="%.1f" cy="%.1f" r="%.1f" fill="none" stroke="%s" stroke-width="%.1f"/>' % (cx, cy, (r + ri) / 2, TRACK, r - ri))
    else:
        angle = -math.pi / 2
        gap = 0.012 if len(items) > 1 else 0
        for label, v, color in items:
            sweep = 2 * math.pi * v / total
            a0, a1 = angle + gap, angle + sweep - gap
            if len(items) == 1:
                out.append('<circle cx="%.1f" cy="%.1f" r="%.1f" fill="none" stroke="%s" stroke-width="%.1f"><title>%s: %s (100%%)</title></circle>' % (
                    cx, cy, (r + ri) / 2, color, r - ri, E(label), _fmt(v)))
            else:
                large = 1 if (a1 - a0) > math.pi else 0
                p = [(cx + r * math.cos(a0), cy + r * math.sin(a0)), (cx + r * math.cos(a1), cy + r * math.sin(a1)),
                     (cx + ri * math.cos(a1), cy + ri * math.sin(a1)), (cx + ri * math.cos(a0), cy + ri * math.sin(a0))]
                out.append('<path d="M%.2f,%.2f A%.2f,%.2f 0 %d,1 %.2f,%.2f L%.2f,%.2f A%.2f,%.2f 0 %d,0 %.2f,%.2f Z" fill="%s">'
                           '<title>%s: %s (%.0f%%)</title></path>' % (p[0][0], p[0][1], r, r, large, p[1][0], p[1][1], p[2][0], p[2][1],
                                                                       ri, ri, large, p[3][0], p[3][1], color, E(label), _fmt(v), 100 * v / total))
            share = v / total
            if share >= 0.05:
                mid = angle + sweep / 2
                lr = (r + ri) / 2
                out.append('<text x="%.1f" y="%.1f" text-anchor="middle" dominant-baseline="central" font-size="%d" font-weight="700" fill="#fff">%.0f%%</text>' % (
                    cx + lr * math.cos(mid), cy + lr * math.sin(mid), max(10, size // 19), 100 * share))
            angle += sweep
    lbl = center_label if center_label is not None else _fmt(total)
    out.append('<text x="%.1f" y="%.1f" text-anchor="middle" font-size="%d" font-weight="800" fill="currentColor">%s</text>' % (cx, cy + size * 0.03, size // 6, E(lbl)))
    if title:
        out.append('<text x="%.1f" y="%.1f" text-anchor="middle" font-size="%d" fill="currentColor" opacity=".65" letter-spacing=".5">%s</text>' % (
            cx, cy + size * 0.13, max(10, size // 17), E(title.upper())))
    out.append("</svg>")
    return "".join(out)


def legend(items, total=None):
    """Legend table: colour, label, value and share of total."""
    items = [(l, v, c) for l, v, c in items if v]
    total = total if total is not None else sum(float(v) for _, v, _ in items)
    rows = []
    for label, v, color in items:
        rows.append('<tr><td style="padding:3px 8px 3px 0"><span style="display:inline-block;width:12px;height:12px;border-radius:3px;background:%s;vertical-align:-1px"></span></td>'
                    '<td style="padding:3px 14px 3px 0">%s</td><td style="padding:3px 10px 3px 0;text-align:right;font-weight:700">%s</td>'
                    '<td style="padding:3px 0;text-align:right;opacity:.65">%.0f%%</td></tr>' % (color, E(label), _fmt(v), 100.0 * float(v) / total if total else 0))
    return '<table style="border-collapse:collapse;font-size:13px">%s</table>' % "".join(rows)


def pie_block(items, title, size=190):
    """Donut plus legend side by side (wraps on narrow screens)."""
    return '<div style="display:flex;gap:18px;align-items:center;flex-wrap:wrap">%s%s</div>' % (donut(items, title, size), legend(items))


def severity_items(counts):
    return [(s.title(), counts.get(s, 0), SEV_COLORS[s]) for s in ("CRITICAL", "WARNING", "UNKNOWN", "INFO")]


def status_color(v, score_mode=True, warn=80, crit=90):
    if score_mode:  # low scores are bad
        return CRIT if v < 70 else WARN if v < 90 else OK
    return CRIT if v >= crit else WARN if v >= warn else OK


def _key(score_mode, warn, crit):
    labels = (("Healthy (90+)", OK), ("Warning (70-89)", WARN), ("Critical (<70)", CRIT)) if score_mode else \
        (("Normal (<%g%%)" % warn, OK), ("Warning (%g%%+)" % warn, WARN), ("Critical (%g%%+)" % crit, CRIT))
    return '<div style="display:flex;gap:14px;flex-wrap:wrap;font-size:12px;opacity:.85;margin-top:4px">%s</div>' % "".join(
        '<span><span style="display:inline-block;width:10px;height:10px;border-radius:2px;background:%s;margin-right:5px"></span>%s</span>' % (c, E(l))
        for l, c in labels)


def hbars(items, maxv=100.0, unit="", score_mode=True, warn=80, crit=90, sort=True, limit=25):
    """Horizontal bar chart with track, value labels, threshold lines, x axis and colour key."""
    items = [(str(l), float(v or 0)) for l, v in items]
    if not items:
        return ""
    if sort:
        items.sort(key=lambda x: x[1] if score_mode else -x[1])  # worst first
    items = items[:limit]
    W, row, LBL, VAL, TOP = 640, 30, 210, 56, 6
    plot = W - LBL - VAL
    H = TOP + row * len(items) + 26
    X = lambda v: LBL + plot * min(max(v, 0), maxv) / maxv
    out = ['<svg viewBox="0 0 %d %d" width="100%%" style="max-width:760px" role="img" %s>' % (W, H, FONT)]
    for i, (label, v) in enumerate(items):
        y = TOP + i * row
        if i % 2 == 0:
            out.append('<rect x="0" y="%d" width="%d" height="%d" fill="%s" opacity=".35" rx="4"/>' % (y, W, row, TRACK))
        out.append('<text x="6" y="%d" font-size="13" fill="currentColor">%s</text>' % (y + 19, E(label[:30])))
        out.append('<rect x="%d" y="%d" width="%d" height="14" rx="7" fill="%s"/>' % (LBL, y + 8, plot, TRACK))
        out.append('<rect x="%d" y="%d" width="%.1f" height="14" rx="7" fill="%s"><title>%s: %s%s</title></rect>' % (
            LBL, y + 8, max(X(v) - LBL, 3), status_color(v, score_mode, warn, crit), E(label), _fmt(v), unit))
        out.append('<text x="%d" y="%d" text-anchor="end" font-size="13" font-weight="700" fill="currentColor">%s%s</text>' % (W - 4, y + 19, _fmt(v), unit))
    base = TOP + row * len(items)
    for t in (warn, crit) if not score_mode else (70, 90):
        out.append('<line x1="%.1f" y1="%d" x2="%.1f" y2="%d" stroke="%s" stroke-dasharray="4 3" stroke-width="1.2"/>' % (
            X(t), TOP, X(t), base, WARN if t in (warn, 70) else CRIT))
    for f in (0, 0.25, 0.5, 0.75, 1):
        x = LBL + plot * f
        out.append('<line x1="%.1f" y1="%d" x2="%.1f" y2="%d" stroke="%s"/>' % (x, base, x, base + 4, GRID))
        out.append('<text x="%.1f" y="%d" text-anchor="middle" font-size="11" fill="currentColor" opacity=".6">%s%s</text>' % (x, base + 16, _fmt(maxv * f), unit))
    out.append("</svg>")
    return "".join(out) + _key(score_mode, warn, crit)


def columns(items, maxv=100.0, unit="", height=250, score_mode=True, warn=80, crit=90, y_title=""):
    """Vertical column chart with gridlines, threshold lines, value labels and rotated category labels."""
    items = [(str(l), float(v or 0)) for l, v in items]
    if not items:
        return ""
    n = len(items)
    W = max(420, 58 * n + 70)
    L, T, B = 54, 18, 74
    plot_h = height - T - B
    slot = (W - L - 12) / float(n)
    bw = min(46, slot * 0.6)
    Y = lambda v: T + plot_h * (1 - min(max(v, 0), maxv) / maxv)
    out = ['<svg viewBox="0 0 %d %d" width="100%%" style="max-width:%dpx" role="img" %s>' % (W, height, max(520, W), FONT)]
    for f in (0, 0.25, 0.5, 0.75, 1):
        y = Y(maxv * f)
        out.append('<line x1="%d" y1="%.1f" x2="%d" y2="%.1f" stroke="%s"/>' % (L, y, W - 8, y, GRID))
        out.append('<text x="%d" y="%.1f" text-anchor="end" font-size="11" fill="currentColor" opacity=".6">%s%s</text>' % (L - 6, y + 4, _fmt(maxv * f), unit))
    if y_title:
        out.append('<text transform="translate(13 %.1f) rotate(-90)" text-anchor="middle" font-size="11" fill="currentColor" opacity=".7">%s</text>' % (T + plot_h / 2, E(y_title)))
    for t, color in ((70, WARN), (90, CRIT)) if score_mode else ((warn, WARN), (crit, CRIT)):
        out.append('<line x1="%d" y1="%.1f" x2="%d" y2="%.1f" stroke="%s" stroke-dasharray="5 4" stroke-width="1.3"/>' % (L, Y(t), W - 8, Y(t), color))
    for i, (label, v) in enumerate(items):
        x = L + slot * i + (slot - bw) / 2
        y = Y(v)
        h = T + plot_h - y
        color = status_color(v, score_mode, warn, crit)
        out.append('<path d="M%.1f,%.1f v%.1f a5,5 0 0 1 5,-5 h%.1f a5,5 0 0 1 5,5 v%.1f z" fill="%s"><title>%s: %s%s</title></path>' % (
            x, T + plot_h, -max(h - 5, 0), bw - 10, max(h - 5, 0), color, E(label), _fmt(v), unit))
        out.append('<text x="%.1f" y="%.1f" text-anchor="middle" font-size="12" font-weight="700" fill="currentColor">%s%s</text>' % (x + bw / 2, y - 6, _fmt(v), unit))
        lx, ly = x + bw / 2, T + plot_h + 14
        out.append('<text x="%.1f" y="%.1f" text-anchor="end" font-size="11.5" fill="currentColor" transform="rotate(-32 %.1f %.1f)">%s</text>' % (
            lx, ly, lx, ly, E(label[:20])))
    out.append('<line x1="%d" y1="%.1f" x2="%d" y2="%.1f" stroke="currentColor" opacity=".35"/>' % (L, T + plot_h, W - 8, T + plot_h))
    out.append("</svg>")
    return "".join(out) + _key(score_mode, warn, crit)


def line(points, maxv=100.0, unit="", height=210, color="#0b5cad", bands=((70, CRIT), (90, WARN)), empty="Not enough data yet"):
    """Time-series line with shaded area, threshold bands, axis ticks, point markers and the latest value."""
    import time as _t
    pts = [(float(t), float(v)) for t, v in points if v is not None]
    W, L, R, T, B = 640, 46, 54, 14, 30
    out = ['<svg viewBox="0 0 %d %d" width="100%%" style="max-width:760px" role="img" %s>' % (W, height, FONT)]
    if len(pts) < 2:
        out.append('<text x="%d" y="%d" text-anchor="middle" font-size="13" fill="currentColor" opacity=".6">%s</text></svg>' % (W // 2, height // 2, E(empty)))
        return "".join(out)
    t0, t1 = pts[0][0], pts[-1][0]
    span = (t1 - t0) or 1
    X = lambda t: L + (W - L - R) * (t - t0) / span
    Y = lambda v: T + (height - T - B) * (1 - min(max(v, 0), maxv) / maxv)
    lo = 0
    for limit, c in bands:  # shaded zones below each threshold (e.g. critical < 70, warning < 90)
        out.append('<rect x="%d" y="%.1f" width="%d" height="%.1f" fill="%s" opacity=".07"/>' % (L, Y(limit), W - L - R, Y(lo) - Y(limit), c))
        lo = limit
    for f in (0, 0.25, 0.5, 0.75, 1):
        y = Y(maxv * f)
        out.append('<line x1="%d" y1="%.1f" x2="%d" y2="%.1f" stroke="%s"/>' % (L, y, W - R, y, GRID))
        out.append('<text x="%d" y="%.1f" text-anchor="end" font-size="11" fill="currentColor" opacity=".6">%s%s</text>' % (L - 6, y + 4, _fmt(maxv * f), unit))
    path = " ".join("%s%.1f,%.1f" % ("M" if i == 0 else "L", X(t), Y(v)) for i, (t, v) in enumerate(pts))
    out.append('<path d="%s L%.1f,%.1f L%.1f,%.1f Z" fill="%s" opacity=".14"/>' % (path, X(t1), Y(0), X(t0), Y(0), color))
    out.append('<path d="%s" fill="none" stroke="%s" stroke-width="2.5" stroke-linejoin="round" stroke-linecap="round"/>' % (path, color))
    step = max(1, len(pts) // 40)
    for t, v in pts[::step]:
        out.append('<circle cx="%.1f" cy="%.1f" r="3" fill="var(--card,#fff)" stroke="%s" stroke-width="2"><title>%s: %s%s</title></circle>' % (
            X(t), Y(v), color, _t.strftime("%Y-%m-%d %H:%M", _t.localtime(t)), _fmt(v), unit))
    lt, lv = pts[-1]
    out.append('<circle cx="%.1f" cy="%.1f" r="5" fill="%s"/>' % (X(lt), Y(lv), color))
    out.append('<text x="%.1f" y="%.1f" font-size="13" font-weight="800" fill="%s">%s%s</text>' % (X(lt) + 8, Y(lv) + 4, color, _fmt(lv), unit))
    for i in range(5):
        t = t0 + span * i / 4
        fmt = "%H:%M" if span < 2 * 86400 else "%d %b"
        out.append('<text x="%.1f" y="%d" text-anchor="%s" font-size="11" fill="currentColor" opacity=".6">%s</text>' % (
            X(t), height - 9, "start" if i == 0 else "end" if i == 4 else "middle", _t.strftime(fmt, _t.localtime(t))))
    out.append("</svg>")
    return "".join(out)


def card(title, body, extra=""):
    return '<div class="card"><h3>%s</h3>%s%s</div>' % (E(title), body, extra)
