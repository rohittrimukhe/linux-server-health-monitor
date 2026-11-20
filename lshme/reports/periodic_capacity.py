# Linux Server Health Monitor Enterprise
# Copyright (c) 2026 LRS Services Pvt Ltd, Mumbai. All rights reserved.
"""Capacity forecast section: when each filesystem fills up, by what date to act, and whether growth is speeding up.

Shown in the weekly and monthly reports by default (web.conf-free: [reports] capacity_forecast_in = weekly, monthly)."""
from lshme.core.util import fmt_bytes
from lshme.reports import periodic as P
from lshme.reports.periodic import Bar, Cell, DAY, MB, chip, stamp, table

GB = 1024.0 ** 3


def slope(pts):
    """Least-squares growth in bytes per day, or None."""
    if len(pts) < 3 or pts[-1][0] - pts[0][0] < DAY * 0.9:
        return None
    n = float(len(pts))
    sx, sy = sum(p[0] for p in pts), sum(p[1] for p in pts)
    sxx, sxy = sum(p[0] * p[0] for p in pts), sum(p[0] * p[1] for p in pts)
    den = n * sxx - sx * sx
    return (n * sxy - sx * sy) / den * DAY if den else None


def rate_cell(r):
    if r is None:
        return Cell("-", "fl")
    if abs(r) < MB:
        return Cell("0", "fl")
    return Cell(("+" if r > 0 else "-") + fmt_bytes(abs(r)) + "/day", "up" if r > 0 else "dn")


def analyse(data, f):
    mp, size, used = f["mountpoint"], f["size"], f["used"]
    pts = data.points("fs.used_bytes:" + mp, 31)
    r30 = slope(pts)
    r7 = slope([p for p in pts if p[0] >= data.now - 7 * DAY])
    older = [p for p in pts if p[0] < data.now - 7 * DAY]
    r_prev = slope(older)
    rate = r7 if (r7 is not None and r7 > 0) else r30
    days = (size - used) / rate if rate and rate > 0 else None
    speeding = bool(r7 and r_prev and r_prev > 0 and r7 > 1.5 * r_prev and r7 > 50 * MB)
    return {"mp": mp, "pts": pts, "r30": r30, "r7": r7, "prev": r_prev, "days": days, "speeding": speeding, "size": size, "used": used,
            "free": f.get("free", size - used), "pct": f.get("used_percent") or 0}


def blocks(data, fs):
    cfg = data.cfg
    lead = cfg.getint("reports", "reports", "forecast_lead_days", 14)
    rows, items = [], []
    for f in fs:
        a = analyse(data, f)
        items.append(a)
        w, c = cfg.threshold("disk", "warning", 80), cfg.threshold("disk", "critical", 90)
        if a["days"] is None:
            trend = Cell("not growing" if a["r30"] is not None else "needs more history", "fl")
            lv = P.level_for(a["pct"], w, c)
            rows.append([Cell(a["mp"], "b"), Bar(a["pct"], w, c), fmt_bytes(a["free"]), rate_cell(a["r30"]), rate_cell(a["r7"]), trend,
                         Cell("-", "fl"), Cell("-", "fl"), Cell("-", "fl"), chip(lv)])
            continue
        fill = data.now + a["days"] * DAY
        lv = "cr" if a["days"] <= 7 else "wn" if (a["days"] <= 30 or a["speeding"]) else P.level_for(a["pct"], w, c)
        trend = Cell("speeding up", "cr") if a["speeding"] else Cell("steady" if not a["r7"] or not a["prev"] or a["r7"] <= 1.1 * a["prev"] else "rising", "fl")
        if a["prev"] and a["r7"] is not None and a["r7"] < 0.6 * a["prev"]:
            trend = Cell("slowing down", "dn")
        full_txt = "> 1 year" if a["days"] > 365 else "%d days" % max(1, round(a["days"]))
        rows.append([Cell(a["mp"], "b"), Bar(a["pct"], w, c), fmt_bytes(a["free"]), rate_cell(a["r30"]), rate_cell(a["r7"]), trend,
                     Cell(stamp(fill, "%d %b %Y"), "ok" if a["days"] > 365 else "wn" if a["days"] <= 30 else "ok"),
                     Cell(stamp(fill - lead * DAY, "%d %b %Y"), "cr" if fill - lead * DAY <= data.now else "fl") if a["days"] <= 365 else Cell("-", "fl"),
                     Cell(full_txt, "cr" if a["days"] <= 7 else "wn" if a["days"] <= 30 else "ok"), chip(lv)])
    out = [table("When each filesystem fills up", ["Filesystem", "Used", "Free", "Growth (30 d)", "Growth (7 d)", "Trend", "Full on", "Act by", "Days left", "Status"], rows,
                 align=["", "", "r", "r", "r", "", "", "", "r", ""],
                 legend="Estimate from a straight-line fit of the recorded usage (the last 7 days when they show growth, otherwise 30 days). "
                        "'Act by' is the full date minus %d days, the time most teams need to extend or clean up. 'Speeding up' means the last 7 days grew over 1.5 times faster than before." % lead)]
    risky = sorted([a for a in items if a["days"] is not None and a["days"] <= 365], key=lambda a: a["days"])[:3]
    charts = []
    for a in risky:
        horizon = min(a["days"], 90) * DAY
        last_t, last_v = a["pts"][-1]
        rate = (a["size"] - a["used"]) / a["days"]
        proj = [[int(last_t), round(last_v / GB, 2)], [int(last_t + horizon), round(min(a["size"], last_v + rate * horizon / DAY) / GB, 2)]]
        charts.append({"title": "Projected fill of %s" % a["mp"], "unit": " GB", "color": "#1d4ed8", "warn": None, "crit": round(a["size"] / GB, 2), "max": None, "min": 0,
                       "multi": True, "colors": ["#1d4ed8", "#ef8f00"], "info": "full in about %d days" % max(1, round(a["days"])),
                       "series": [{"name": "Used", "pts": [[int(t), round(v / GB, 2)] for t, v in a["pts"]]}, {"name": "Forecast", "pts": proj}]})
    if charts:
        out.append({"type": "wide_charts", "items": charts})
    elif rows:
        out.append(P.note("No filesystem is forecast to fill within a year at its current growth."))
    return out
