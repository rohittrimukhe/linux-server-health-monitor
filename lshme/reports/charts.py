# Linux Server Health Monitor Enterprise
# Copyright (c) 2026 LRS Services Pvt Ltd, Mumbai. All rights reserved.
"""Dependency-free SVG charts (donut/pie, vertical bars, line) for HTML pages.

Charts are plain inline SVG: they print, work offline and in email-safe HTML,
and need no JavaScript or external libraries.
"""
import html
import math

from lshme.reports.doc import SEV_COLORS

E = lambda s: html.escape(str(s if s is not None else ""))
FONT = 'font-family="Helvetica,Arial,sans-serif"'


def donut(items, title="", size=200, hole=0.58, center_label=None):
    """items: [(label, value, color)]. Zero values are skipped; a full ring is drawn for a single slice."""
    items = [(l, float(v), c) for l, v, c in items if v and float(v) > 0]
    total = sum(v for _, v, _ in items)
    r = size / 2.0 - 4
    cx = cy = size / 2.0
    parts = ['<svg viewBox="0 0 %d %d" width="%d" height="%d" role="img" aria-label="%s" %s>' % (size, size, size, size, E(title), FONT)]
    if not total:
        parts.append('<circle cx="%.1f" cy="%.1f" r="%.1f" fill="#e6eaef"/>' % (cx, cy, r))
    elif len(items) == 1:
        parts.append('<circle cx="%.1f" cy="%.1f" r="%.1f" fill="%s"><title>%s: %g</title></circle>' % (cx, cy, r, items[0][2], E(items[0][0]), items[0][1]))
    else:
        angle = -math.pi / 2
        for label, v, color in items:
            sweep = 2 * math.pi * v / total
            x1, y1 = cx + r * math.cos(angle), cy + r * math.sin(angle)
            angle += sweep
            x2, y2 = cx + r * math.cos(angle), cy + r * math.sin(angle)
            parts.append('<path d="M%.2f,%.2f L%.2f,%.2f A%.2f,%.2f 0 %d,1 %.2f,%.2f Z" fill="%s" stroke="#fff" stroke-width="2">'
                         '<title>%s: %g (%.0f%%)</title></path>' % (cx, cy, x1, y1, r, r, 1 if sweep > math.pi else 0, x2, y2, color, E(label), v, 100 * v / total))
    if hole:
        parts.append('<circle cx="%.1f" cy="%.1f" r="%.1f" fill="var(--card,#fff)"/>' % (cx, cy, r * hole))
        lbl = center_label if center_label is not None else ("%g" % total)
        parts.append('<text x="%.1f" y="%.1f" text-anchor="middle" font-size="%d" font-weight="700" fill="currentColor">%s</text>' % (cx, cy + 4, size // 7, E(lbl)))
        if title:
            parts.append('<text x="%.1f" y="%.1f" text-anchor="middle" font-size="%d" fill="currentColor" opacity=".7">%s</text>' % (cx, cy + size // 9 + 4, size // 16, E(title)))
    parts.append("</svg>")
    return "".join(parts)


def legend(items):
    rows = []
    for label, v, color in items:
        if v:
            rows.append('<span style="display:inline-flex;align-items:center;gap:6px;margin:2px 10px 2px 0;font-size:12.5px">'
                        '<span style="width:11px;height:11px;border-radius:2px;background:%s;display:inline-block"></span>%s <b>%s</b></span>' % (color, E(label), E(v)))
    return "<div>%s</div>" % "".join(rows)


def severity_items(counts):
    return [(s.title(), counts.get(s, 0), SEV_COLORS[s]) for s in ("CRITICAL", "WARNING", "UNKNOWN", "INFO")]


def status_color(v, score_mode=True, warn=80, crit=90):
    if score_mode:
        return SEV_COLORS["CRITICAL"] if v < 70 else SEV_COLORS["WARNING"] if v < 90 else SEV_COLORS["HEALTHY"]
    return SEV_COLORS["CRITICAL"] if v >= crit else SEV_COLORS["WARNING"] if v >= warn else SEV_COLORS["HEALTHY"]


def columns(items, maxv=100.0, unit="", height=220, score_mode=True, warn=80, crit=90):
    """Vertical bar chart: items [(label, value)] with value labels and rotated category labels."""
    if not items:
        return ""
    n = len(items)
    w = max(360, 46 * n + 40)
    plot_h = height - 70
    bw = (w - 40) / float(n) * 0.62
    out = ['<svg viewBox="0 0 %d %d" width="100%%" style="max-width:%dpx" role="img" %s>' % (w, height, max(480, w), FONT)]
    for frac in (0.25, 0.5, 0.75, 1.0):
        y = 10 + plot_h * (1 - frac)
        out.append('<line x1="30" y1="%.1f" x2="%d" y2="%.1f" stroke="currentColor" opacity=".12"/>' % (y, w - 6, y))
        out.append('<text x="26" y="%.1f" text-anchor="end" font-size="10" fill="currentColor" opacity=".6">%g</text>' % (y + 3, maxv * frac))
    for i, (label, v) in enumerate(items):
        v = max(0.0, float(v or 0))
        x = 34 + (w - 40) / float(n) * i + ((w - 40) / float(n) - bw) / 2
        h = plot_h * min(v, maxv) / maxv
        y = 10 + plot_h - h
        out.append('<rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" rx="3" fill="%s"><title>%s: %g%s</title></rect>' % (
            x, y, bw, max(h, 1), status_color(v, score_mode, warn, crit), E(label), v, unit))
        out.append('<text x="%.1f" y="%.1f" text-anchor="middle" font-size="10.5" font-weight="700" fill="currentColor">%g</text>' % (x + bw / 2, y - 4, round(v, 1)))
        lx, ly = x + bw / 2, 10 + plot_h + 12
        out.append('<text x="%.1f" y="%.1f" text-anchor="end" font-size="10" fill="currentColor" transform="rotate(-35 %.1f %.1f)">%s</text>' % (
            lx, ly, lx, ly, E(str(label)[:18])))
    out.append("</svg>")
    return "".join(out)


def card(title, body, extra=""):
    return '<div class="card"><h3>%s</h3>%s%s</div>' % (E(title), body, extra)
