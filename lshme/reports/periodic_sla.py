# Linux Server Health Monitor Enterprise
# Copyright (c) 2026 LRS Services Pvt Ltd, Mumbai. All rights reserved.
"""'SLA achieved' section of the daily, weekly and monthly reports.

Availability for the report period comes from the live watcher's incident log: time with a confirmed CRITICAL problem is
downtime, time with only warnings is degraded. It is compared with the SLA target ([main] [sla] target_percent)."""
import json
import os
import time

from lshme.core.util import fmt_duration
from lshme.ops import sla
from lshme.reports.periodic import DAY, Cell, kpis, note, table

ALLOWED = {"daily": "day", "weekly": "week", "monthly": "month"}


def coverage(state, start, end):
    """Share of the period the watcher was sampling (from its per-day tick counter), or None when unknown."""
    cov = (state or {}).get("coverage") or {}
    if not cov:
        return None
    interval = float((state or {}).get("interval", 60) or 60)
    day0 = time.strftime("%Y-%m-%d", time.localtime(start))
    ticks = sum(n for day, n in cov.items() if day >= day0)
    midnight = time.mktime(time.strptime(day0, "%Y-%m-%d"))
    return min(100.0, 100.0 * ticks * interval / max(1.0, end - midnight))


def measure(cfg, paths, now, days):
    start = now - days * DAY
    state = {}
    try:
        with open(os.path.join(paths.data_dir, "watch_state.json"), encoding="utf-8") as f:
            state = json.load(f)
    except (IOError, OSError, ValueError):
        pass
    events = sla.load_events(os.path.join(paths.data_dir, "watch_events.jsonl"))
    res = sla.compute(events, start, now, now)
    res["coverage"] = coverage(state, start, now)
    res["target"] = cfg.getfloat("main", "sla", "target_percent", 99.9)
    return res


def blocks(cfg, paths, now, days, ptype):
    """(kpi item for 'Overall status', [blocks for the 'SLA achieved' section])."""
    r = measure(cfg, paths, now, days)
    target, avail = r["target"], r["availability"]
    ok = avail >= target
    budget = r["period"] * (1 - target / 100.0)
    used = 100.0 * r["downtime"] / budget if budget > 0 else (0.0 if not r["downtime"] else 100.0)
    tone = "ok" if ok else "cr"
    verdict = "Achieved" if ok else "Missed"
    kpi = ("%.2f %%" % avail, "SLA achieved (target %g %%)" % target, tone)
    rows = [
        [Cell("SLA target", "b"), "%g %% availability" % target],
        [Cell("Availability achieved", "b"), Cell("%.3f %%" % avail, tone)],
        [Cell("Result", "b"), Cell(verdict, "chip-ok" if ok else "chip-cr")],
        [Cell("Downtime (critical)", "b"), fmt_duration(r["downtime"]) if r["downtime"] else "none"],
        [Cell("Allowed downtime this %s" % ALLOWED.get(ptype, "period"), "b"), fmt_duration(budget)],
        [Cell("Downtime allowance used", "b"), Cell("%.0f %%" % used, "ok" if used < 50 else "wn" if used < 100 else "cr")],
        [Cell("Degraded (warnings only)", "b"), fmt_duration(r["degraded"]) if r["degraded"] else "none"],
        [Cell("Incidents (critical / all)", "b"), "%d / %d" % (r["critical_incidents"], len(r["incidents"]))],
        [Cell("Mean time to recover", "b"), fmt_duration(r["mttr"]) if r["mttr"] is not None else "n/a"],
        [Cell("Monitoring coverage", "b"), ("%.1f %%" % r["coverage"]) if r["coverage"] is not None else "unknown"],
    ]
    out = [kpis([kpi, (fmt_duration(r["downtime"]) if r["downtime"] else "0 min", "Downtime", tone),
                 ("%.0f %%" % used, "Allowance used", "ok" if used < 50 else "wn" if used < 100 else "cr")]),
           table("SLA for the period", ["Measure", "Value"], rows, align=["", ""],
                 legend="Availability = time without a confirmed critical problem / the period. The target is set in main.conf [sla] target_percent.")]
    comps = [c for c in r["components"] if c["downtime"] or c["degraded"]][:15]
    if comps:
        out.append(table("Availability by component", ["Component", "Availability", "Result", "Downtime", "Degraded", "Incidents"],
                         [[Cell("%s: %s" % (c["group"], c["component"]), "b"),
                           Cell("%.3f %%" % c["availability"], "ok" if c["availability"] >= target else "cr"),
                           Cell("Achieved" if c["availability"] >= target else "Missed", "chip-ok" if c["availability"] >= target else "chip-cr"),
                           fmt_duration(c["downtime"]) if c["downtime"] else "-", fmt_duration(c["degraded"]) if c["degraded"] else "-",
                           "%d (%d critical)" % (c["incidents"], c["critical"])] for c in comps],
                         align=["", "r", "", "r", "r", "r"]))
    if r["coverage"] is not None and r["coverage"] < 90:
        out.append(note("The live watcher covered only %.1f %% of this period, so availability reflects the monitored time. "
                        "Keep it running: vitals watch install." % r["coverage"]))
    elif r["coverage"] is None:
        out.append(note("The live watcher has not recorded this period, so downtime could not be measured. Start it with: vitals watch install."))
    return kpi, out
