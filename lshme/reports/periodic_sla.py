# Linux Server Health Monitor Enterprise
# Copyright (c) 2026 LRS Services Pvt Ltd, Mumbai. All rights reserved.
"""'SLA achieved' section of the daily, weekly and monthly reports.

Availability for the report period comes from the live watcher's incident log and is compared with the SLA target
([main] [sla] target_percent).

What counts as downtime: time with a confirmed CRITICAL problem of a kind that makes the server or a service unavailable
(a critical service, port or process that is down, a read-only or unresponsive filesystem; see [sla] count_as_downtime).
Capacity problems such as a full disk, high CPU, memory or load are not downtime: they are shown as degraded time.

Availability is measured over the time the watcher was actually monitoring. The SLA is only rated once the watcher has covered at
least half of the period (and at least one hour); before that the figures are shown but marked 'not rated yet'."""
import json
import os
import time

from lshme.core.util import fmt_duration
from lshme.ops import sla
from lshme.reports.periodic import DAY, Cell, kpis, note, table

ALLOWED = {"daily": "day", "weekly": "week", "monthly": "month"}
MIN_RATED_SHARE = 0.5
MIN_RATED_SECONDS = 3600


def monitored_seconds(state, start, end):
    """Seconds of the period the watcher was sampling (its per-day tick counter), or None when it has not recorded anything."""
    cov = (state or {}).get("coverage") or {}
    if not cov:
        return None
    interval = float((state or {}).get("interval", 60) or 60)
    day0 = time.strftime("%Y-%m-%d", time.localtime(start))
    ticks = sum(n for day, n in cov.items() if day >= day0)
    return min(max(0.0, end - start), ticks * interval)


def measure(cfg, paths, now, days):
    start = now - days * DAY
    state = {}
    try:
        with open(os.path.join(paths.data_dir, "watch_state.json"), encoding="utf-8") as f:
            state = json.load(f)
    except (IOError, OSError, ValueError):
        pass
    events = sla.load_events(os.path.join(paths.data_dir, "watch_events.jsonl"))
    res = sla.compute(events, start, now, now, down_kinds=sla.down_kinds(cfg))
    mon = monitored_seconds(state, start, now)
    res["monitored"] = mon
    res["coverage"] = (100.0 * mon / res["period"]) if mon is not None and res["period"] else None
    res["rated"] = mon is not None and mon >= max(MIN_RATED_SECONDS, MIN_RATED_SHARE * res["period"])
    basis = mon if (mon and mon < res["period"]) else res["period"]
    res["basis"] = basis
    res["availability_monitored"] = max(0.0, 100.0 * (1 - res["downtime"] / basis)) if (basis and mon is not None) else None
    res["target"] = cfg.getfloat("main", "sla", "target_percent", 99.9)
    return res


def pct(v):
    return "%.3f %%" % v


def blocks(cfg, paths, now, days, ptype):
    """(kpi item for 'Overall status', [blocks for the 'SLA achieved' section])."""
    r = measure(cfg, paths, now, days)
    target, avail, rated = r["target"], r["availability_monitored"], r["rated"]
    period_name = ALLOWED.get(ptype, "period")
    ok = rated and avail >= target
    tone = "fl" if not rated else ("ok" if ok else "cr")
    verdict = "Not rated yet" if not rated else ("Achieved" if ok else "Missed")
    budget = r["basis"] * (1 - target / 100.0)
    used = (100.0 * r["downtime"] / budget) if budget > 0 else (0.0 if not r["downtime"] else 999.0)
    used_text = ">999 %" if used > 999 else "%.0f %%" % used
    used_tone = "ok" if used < 50 else "wn" if used < 100 else "cr"
    mon = r["monitored"]
    mon_text = "%s of %s" % (fmt_duration(mon), fmt_duration(r["period"])) if mon is not None else "none recorded"
    if rated:
        kpi = ("%.2f %%" % avail, "SLA achieved (target %g %%)" % target, tone)
    elif mon is None:
        kpi = ("not rated yet", "SLA (no monitoring data yet)", "wn")
    else:
        kpi = ("not rated yet", "SLA (monitored %s)" % mon_text, "wn")
    rows = [
        [Cell("SLA target", "b"), "%g %% availability" % target],
        [Cell("Availability achieved", "b"), Cell(pct(avail) if avail is not None else "unknown", tone if avail is not None else "fl")],
        [Cell("Result", "b"), Cell(verdict, "chip-ok" if ok else "chip-cr" if rated else "chip-wn")],
        [Cell("Monitored", "b"), mon_text + (" (%.1f %%)" % r["coverage"] if r["coverage"] is not None else "")],
        [Cell("Downtime (service or server unavailable)", "b"), fmt_duration(r["downtime"]) if r["downtime"] else "none"],
        [Cell("Downtime allowed this %s" % period_name, "b"), fmt_duration(budget)],
        [Cell("Downtime allowance used", "b"), Cell(used_text, used_tone)],
        [Cell("Degraded (warnings, full disks, high load)", "b"), fmt_duration(r["degraded"]) if r["degraded"] else "none"],
        [Cell("Incidents (causing downtime / all)", "b"), "%d / %d" % (r["critical_incidents"], len(r["incidents"]))],
        [Cell("Mean time to recover", "b"), fmt_duration(r["mttr"]) if r["mttr"] is not None else "n/a"],
    ]
    out = [kpis([kpi, (fmt_duration(r["downtime"]) if r["downtime"] else "0 min", "Downtime", "cr" if r["downtime"] else "ok"),
                 (used_text, "Allowance used", used_tone)]),
           table("SLA for the period", ["Measure", "Value"], rows, align=["", ""],
                 legend="Availability = time without a confirmed critical service or server problem, over the time LRS Vitals monitored. A full disk, "
                        "high CPU, memory or load counts as degraded time, not downtime. The target is [sla] target_percent in main.conf; "
                        "what counts as downtime is [sla] count_as_downtime.")]
    comps = [c for c in r["components"] if c["downtime"] or c["degraded"]][:15]
    if comps:
        base = r["basis"]
        out.append(table("Availability by component", ["Component", "Availability", "Result", "Downtime", "Degraded", "Incidents"],
                         [[Cell("%s: %s" % (c["group"], c["component"]), "b"),
                           Cell(pct(100.0 * (1 - c["downtime"] / base)), "ok" if (100.0 * (1 - c["downtime"] / base)) >= target else "cr"),
                           Cell("Achieved" if (100.0 * (1 - c["downtime"] / base)) >= target else "Missed",
                                "chip-ok" if (100.0 * (1 - c["downtime"] / base)) >= target else "chip-cr") if rated else Cell("Not rated", "chip-wn"),
                           fmt_duration(c["downtime"]) if c["downtime"] else "-", fmt_duration(c["degraded"]) if c["degraded"] else "-",
                           "%d (%d causing downtime)" % (c["incidents"], c["critical"])] for c in comps],
                         align=["", "r", "", "r", "r", "r"]))
    if mon is None:
        out.append(note("The live watcher has not recorded this period, so downtime could not be measured. Start it with: vitals watch install."))
    elif not rated:
        need = max(MIN_RATED_SECONDS, MIN_RATED_SHARE * r["period"])
        out.append(note("The SLA is not rated yet: the live watcher has monitored %s of the %s %s, and a rating needs at least %s. "
                        "The figures above cover the monitored time only." % (fmt_duration(mon), fmt_duration(r["period"]), period_name, fmt_duration(need))))
    elif r["coverage"] is not None and r["coverage"] < 90:
        out.append(note("The live watcher covered %.1f %% of this %s; availability is calculated over the monitored time. Keep the watcher "
                        "running: vitals watch install." % (r["coverage"], period_name)))
    return kpi, out
