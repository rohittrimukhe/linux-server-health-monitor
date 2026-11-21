# Linux Server Health Monitor Enterprise
# Copyright (c) 2026 LRS Services Pvt Ltd, Mumbai. All rights reserved.
"""The SAN storage section of the reports, for every array added (any make).

Daily: is each array healthy, controllers, failed drives, pools and how full they are, I/O now.
Weekly and monthly: the same plus pool growth, I/O and response time over the period (average and peak) and how often the array answered.
Comprehensive: everything: every drive (wear, spares), every volume, fans / power / batteries, recent critical events, charts."""
from lshme.core.util import fmt_bytes
from lshme.reports.periodic import (Bar, Cell, charts, fmt_delta, growth_charts, growth_item, kpis, level_for, note, stats, table)

BAD_DRIVE = ("failed", "removed", "fault", "broken")
COLORS = ["#0b5ed7", "#d9730d", "#1e8e3e", "#7b3fe4"]


def san_data(run):
    m = run.module("san") if run else None
    if not m or m.status == "SKIPPED" or not (m.data or {}).get("arrays"):
        return None
    return m.data["arrays"]


def _status(d):
    if d.get("error"):
        return Cell("Unreachable", "chip-cr")
    st = d.get("status", "unknown")
    return Cell("Optimal", "chip-ok") if st == "optimal" else Cell(st.title(), "chip-cr" if st in ("failed", "offline") else "chip-wn")


def _ok(s):
    return s in ("optimal", "ok", "online", "present", "spare", "")


def summary_point(run):
    arrays = san_data(run)
    if not arrays:
        return None
    bad = [d["name"] for d in arrays if d.get("error") or d.get("status") != "optimal"]
    failed = sum(1 for d in arrays for x in d.get("drives", []) if x["status"] in BAD_DRIVE)
    pools = [(d["name"], p) for d in arrays for p in d.get("pools", []) if p.get("used_percent") is not None]
    full = max(pools, key=lambda x: x[1]["used_percent"]) if pools else None
    if bad:
        text = "SAN storage needs attention: %s" % ", ".join(bad)
        lv = "cr"
    else:
        text = ("The SAN storage is healthy" if len(arrays) == 1 else "All %d SAN storage systems are healthy" % len(arrays))
        lv = "ok"
    if failed:
        text += "; %d failed drive%s" % (failed, "" if failed == 1 else "s")
        lv = "cr"
    if full:
        text += "; fullest pool %s at %.0f %%" % (full[1]["name"], full[1]["used_percent"])
        if full[1]["used_percent"] >= 80 and lv == "ok":
            lv = "wn"
    return lv, text + "."


def _io(data, name):
    """(avg, peak) of IOPS (read+write), MB/s and response time over the period, from the history of health checks."""
    out = {}
    for key in ("read_iops", "write_iops", "read_mbps", "write_mbps", "latency_ms"):
        out[key] = stats([p for p in data.points("san.%s:%s" % (key, name)) if p[0] >= data.since])
    return out


def _reach(data, name):
    pts = [p for p in data.points("san.reachable:" + name) if p[0] >= data.since]
    return 100.0 * sum(1 for _, v in pts if v >= 0.5) / len(pts) if pts else None


def blocks(data, ptype, variant):
    arrays = san_data(data.run)
    if not arrays:
        return []
    full = variant == "comprehensive"
    win = "d1" if ptype == "daily" else "d30" if ptype == "monthly" else "d7"
    wl = {"d1": "24 h", "d7": "7 d", "d30": "30 d"}[win]
    th = lambda key, d: data.cfg.getfloat("thresholds", "san", key, d) if data.cfg else d
    pw, pc = th("pool_warning", 80), th("pool_critical", 90)
    ok_arrays = [d for d in arrays if not d.get("error")]
    failed = [(d["name"], x) for d in ok_arrays for x in d["drives"] if x["status"] in BAD_DRIVE]
    pools = [(d["name"], p) for d in ok_arrays for p in d["pools"]]
    fullest = max([x for x in pools if x[1]["used_percent"] is not None], key=lambda x: x[1]["used_percent"], default=None)
    healthy = sum(1 for d in ok_arrays if d["status"] == "optimal")
    items = [("%d of %d" % (healthy, len(arrays)), "Storage systems optimal", "ok" if healthy == len(arrays) else "cr"),
             (str(len(failed)), "Failed drives", "cr" if failed else "ok")]
    if fullest:
        items.append(("%.0f %%" % fullest[1]["used_percent"], "Fullest pool (%s)" % fullest[1]["name"], level_for(fullest[1]["used_percent"], pw, pc)))
    lat = [d["perf"]["latency_ms"] for d in ok_arrays if (d.get("perf") or {}).get("latency_ms")]
    if lat:
        items.append(("%.1f ms" % max(lat), "Response time now", "wn" if max(lat) >= th("latency_warning_ms", 20) else "ok"))
    out = [kpis(items)]

    # the arrays
    rows = []
    for d in arrays:
        if d.get("error"):
            rows.append([Cell(d["name"], "b"), d.get("type", ""), "-", "-", _status(d), Cell(d["error"][:80], "wrap"), "-", "-"])
            continue
        ctl = ", ".join("%s %s" % (c["name"].replace("Controller ", ""), "OK" if c["status"] == "optimal" else c["status"]) for c in d["controllers"]) or "-"
        nd = len(d["drives"])
        nf = sum(1 for x in d["drives"] if x["status"] in BAD_DRIVE)
        row = [Cell(d["name"] + ((" (%s)" % d["system_name"]) if d.get("system_name") else ""), "b"), d.get("model") or d.get("type", ""), d.get("firmware") or "-", d.get("serial") or "-", _status(d), ctl,
               Cell("%d (%d failed)" % (nd, nf), "cr" if nf else "") if nd else "-", "%d / %d" % (len(d["pools"]), len(d["volumes"]))]
        rows.append(row)
    out.append(table("Storage systems", ["Storage", "Model", "Firmware", "Serial", "Status", "Controllers", "Drives", "Pools / volumes"], rows,
                     align=["", "", "", "", "", "", "r", "r"]))
    if any(d.get("unreachable") for d in ok_arrays):
        out.append(note("One controller's management port did not answer: " + "; ".join("%s: %s" % (d["name"], ", ".join(d["unreachable"])) for d in ok_arrays if d.get("unreachable"))))

    # pools (volume groups / disk pools / aggregates / CPGs)
    if pools:
        prow = []
        for n, p in sorted(pools, key=lambda x: -(x[1]["used_percent"] or 0)):
            it = growth_item(data, "san.pool_used_bytes:%s|%s" % (n, p["name"]))
            prow.append([Cell(p["name"], "b"), n, p.get("raid") or "-", Bar(p["used_percent"], pw, pc) if p["used_percent"] is not None else Cell("-", "fl"),
                         fmt_bytes(p["used"]), fmt_bytes(p["free"]), fmt_bytes(p["capacity"]), fmt_delta(it[win] if it else None, p["capacity"]),
                         Cell("OK", "chip-ok") if _ok(p["status"]) else Cell(p["status"].title(), "chip-cr")])
        out.append(table("Pools and volume groups", ["Pool", "Storage", "RAID", "Used", "Used space", "Free", "Capacity", "Growth (%s)" % wl, "Status"], prow,
                         align=["", "", "", "", "r", "r", "r", "r", ""], legend="Thin volumes stop writing when their pool is full; the bar turns amber at %.0f %% and red at %.0f %%." % (pw, pc)))

    # problems found now (any make): failed or failing drives, hardware, controllers, volumes
    probs = []
    for d in ok_arrays:
        probs += [[Cell("CRIT", "chip-cr"), d["name"], "Drive " + x["slot"], x["status"]] for x in d["drives"] if x["status"] in BAD_DRIVE]
        probs += [[Cell("WARN", "chip-wn"), d["name"], "Drive " + x["slot"], "predicts failure" if x.get("predictive_failure") else "SSD %.0f %% worn" % x["wear_percent"]]
                  for x in d["drives"] if x.get("predictive_failure") or (x.get("wear_percent") is not None and x["wear_percent"] >= th("ssd_wear_warning", 90))]
        probs += [[Cell("CRIT", "chip-cr"), d["name"], c["name"], c["status"]] for c in d["controllers"] if c["status"] not in ("optimal",)]
        probs += [[Cell("CRIT" if h_["kind"] in ("Power supply", "Battery") else "WARN", "chip-cr" if h_["kind"] in ("Power supply", "Battery") else "chip-wn"), d["name"], h_["name"], h_["status"]]
                  for h_ in d["hardware"] if not _ok(h_["status"])]
        probs += [[Cell("WARN", "chip-wn"), d["name"], "Volume " + v["name"], v["status"]] for v in d["volumes"] if not _ok(v["status"])]
        probs += [[Cell("CRIT", "chip-cr"), d["name"], "Failure", f] for f in d.get("failures", [])[:10]]
    if probs:
        out.append(table("Storage problems now", ["Level", "Storage", "Part", "State"], probs[:40]))
    else:
        out.append(note("No failed or failing drives, controllers, power supplies, fans, batteries or volumes on any storage system."))

    # I/O: now (daily) or over the period (weekly, monthly)
    io_rows = []
    for d in ok_arrays:
        p = d.get("perf") or {}
        if not p:
            continue
        if ptype == "daily":
            io_rows.append([Cell(d["name"], "b"), "%.0f" % ((p.get("read_iops") or 0) + (p.get("write_iops") or 0)), "%.1f" % ((p.get("read_mbps") or 0) + (p.get("write_mbps") or 0)),
                            ("%.1f ms" % p["latency_ms"]) if p.get("latency_ms") else "-"])
        else:
            s = _io(data, d["name"])
            f = lambda st, fmt: (fmt % st["avg"], fmt % st["max"]) if st else ("-", "-")
            ri, wi, rm, wm, la = f(s["read_iops"], "%.0f"), f(s["write_iops"], "%.0f"), f(s["read_mbps"], "%.1f"), f(s["write_mbps"], "%.1f"), f(s["latency_ms"], "%.1f")
            r = _reach(data, d["name"])
            io_rows.append([Cell(d["name"], "b"), "%s / %s" % ri, "%s / %s" % wi, "%s / %s" % rm, "%s / %s" % wm, "%s / %s" % la,
                            Cell("%.0f %%" % r, "ok" if r >= 99.5 else "wn") if r is not None else "-"])
    if io_rows:
        if ptype == "daily":
            out.append(table("I/O now", ["Storage", "IOPS", "MB/s", "Response time"], io_rows, align=["", "r", "r", "r"]))
        else:
            out.append(table("I/O over the period (average / peak)", ["Storage", "Read IOPS", "Write IOPS", "Read MB/s", "Write MB/s", "Response ms", "Answered"], io_rows,
                             align=["", "r", "r", "r", "r", "r", "r"], legend="Measured at each health check. 'Answered' is the share of checks in which the storage replied."))

    if ptype != "daily":
        gi = [("%s / %s" % (n, p["name"]), growth_item(data, "san.pool_used_bytes:%s|%s" % (n, p["name"]))) for n, p in pools]
        gi = [(n, it) for n, it in gi if it and it.get(win) is not None and abs(it[win]) >= 512 * 1024 * 1024][:4]
        gc = growth_charts(gi, "Used space in pool", data.since)
        for d in ok_arrays[:2]:
            series = []
            for key, label in (("latency_ms", "Response ms"),):
                pts = [[int(t), round(v, 2)] for t, v in data.points("san.%s:%s" % (key, d["name"])) if t >= data.since]
                if len(pts) >= 2:
                    series.append({"name": label, "pts": pts})
            if series:
                gc.append({"title": "Response time of %s" % d["name"], "unit": " ms", "color": COLORS[0], "warn": th("latency_warning_ms", 20), "crit": None, "max": None, "min": 0,
                           "series": series})
        c = charts(gc)
        if c:
            out.append(c)

    if not full:
        return out

    # the full inventory
    for d in ok_arrays:
        if d["drives"]:
            out.append(table("Drives of %s" % d["name"], ["Location", "Type", "Capacity", "State", "Hot spare", "SSD wear"],
                             [[x["slot"], x.get("media") or "-", fmt_bytes(x["capacity"]) if x.get("capacity") else "-",
                               Cell("OK", "chip-ok") if _ok(x["status"]) else Cell(x["status"].title(), "chip-cr" if x["status"] in BAD_DRIVE else "chip-wn"),
                               "yes" if x.get("hot_spare") else "", ("%.0f %%" % x["wear_percent"]) if x.get("wear_percent") is not None else "-"] for x in d["drives"]],
                             align=["", "", "r", "", "", "r"]))
        if d["volumes"]:
            out.append(table("Volumes of %s" % d["name"], ["Volume", "Pool", "Size", "State", "Mapped to hosts"],
                             [[Cell(v["name"], "b"), v.get("pool") or "-", fmt_bytes(v["capacity"]), Cell("OK", "chip-ok") if _ok(v["status"]) else Cell(v["status"].title(), "chip-wn"),
                               ("yes (%d)" % v["hosts"] if v.get("hosts") else "yes") if v.get("mapped") else ("no" if v.get("mapped") is False else "-")]
                              for v in sorted(d["volumes"], key=lambda v: -(v["capacity"] or 0))], align=["", "", "r", "", ""]))
        if d["hardware"] or d["controllers"]:
            out.append(table("Controllers and hardware of %s" % d["name"], ["Part", "Kind", "State", "Detail"],
                             [[Cell(c["name"], "b"), "Controller", Cell("OK", "chip-ok") if c["status"] == "optimal" else Cell(c["status"].title(), "chip-cr"), c.get("detail", "")] for c in d["controllers"]] +
                             [[h_["name"], h_["kind"], Cell("OK", "chip-ok") if _ok(h_["status"]) else Cell(h_["status"].title(), "chip-cr"), ""] for h_ in d["hardware"]]))
        if d.get("events"):
            out.append(table("Recent critical events on %s" % d["name"], ["Time", "Level", "Event"], [[e.get("time", ""), e.get("severity", ""), Cell(e.get("text", ""), "wrap")] for e in d["events"][:20]]))
    return out
