# Linux Server Health Monitor Enterprise
# Copyright (c) 2026 LRS Services Pvt Ltd, Mumbai. All rights reserved.
"""The executive summary: the daily, weekly or monthly report for management and the CEO.

Plain words, a handful of large figures and three questions answered: is everything running, are we meeting the service target, and what
needs a decision. No tables of cores, folders or ports. The detail stays in the technical reports; this one is built from the same data,
so the two can never disagree."""
from lshme.reports import periodic as P
from lshme.reports.periodic import Cell, actions, kpis, note, table

STATUS_WORDS = {"HEALTHY": ("All good", "ok"), "WARNING": ("Needs attention", "wn"), "CRITICAL": ("Action required", "cr")}
PERIOD = {"daily": "last 24 hours", "weekly": "last 7 days", "monthly": "last 30 days"}


def section(d, title):
    return next((blocks for t, blocks in d.sections if t == title), [])


def runway(data):
    """(days until the first filesystem is full, mount point) over the filesystems that have a trustworthy forecast, else None."""
    best = None
    try:
        for f in P.filesystems(data):
            it = P.growth_item(data, "fs.used_bytes:" + f["mountpoint"], f["size"])
            if it and it.get("forecast_ok") and it.get("full_days") is not None and it["full_days"] < 3650:
                if best is None or it["full_days"] < best[0]:
                    best = (it["full_days"], f["mountpoint"])
    except Exception:
        return None
    return best


def compose(d, data, run, ptype):
    """Turn the finished technical document into the executive one (same document object, new title, fewer and plainer sections)."""
    words, tone = STATUS_WORDS.get(d.status, ("Unknown", "wn"))
    attention = next((b for b in section(d, "What needs attention") if b["type"] == "actions"), None)
    acts = attention["items"] if attention else []
    sla = section(d, "SLA achieved")
    sla_kpi = next((b for b in sla if b["type"] == "kpis"), None)
    avail = sla_kpi["items"][0] if sla_kpi else ("n/a", "SLA", "wn")
    crit = sum(1 for a in acts if a[0] == "cr")
    items = [(words, "Overall status", tone),
             avail if avail[1].startswith("SLA") else (avail[0], "Availability vs target", avail[2]),
             ("%d" % len(acts) if acts else "None", "Issues needing action", "cr" if crit else "wn" if acts else "ok")]
    rw = runway(data)
    if rw:
        days = int(max(1, round(rw[0])))
        items.append(("%d days" % days if days < 365 else "%.1f years" % (days / 365.0), "Until a disk is full (%s)" % rw[1], "cr" if days < 30 else "wn" if days < 90 else "ok"))
    else:
        items.append(("No risk", "Disk space outlook", "ok"))
    try:
        sec = int(data.cur.get("security.pending_security_updates", 0) or 0)
        items.append(("%d" % sec, "Security patches waiting", "cr" if sec >= 20 else "wn" if sec else "ok"))
    except (TypeError, ValueError):
        pass
    story = next((b for b in section(d, "In short") if b["type"] == "points"), None)
    sections = [("At a glance", [kpis(items)])]
    if story:
        sections.append(("In plain words", [story]))
    keep_sla = [b for b in sla if b["type"] in ("kpis", "note")]
    if keep_sla:
        sections.append(("Are we meeting the service target?", keep_sla + [note(
            "The service target is %g %% availability. Downtime is time when a service or the server was unavailable; "
            "full disks and high load are not counted as downtime." % data.cfg.getfloat("main", "sla", "target_percent", 99.9))]))
    if acts:
        sections.append(("What needs a decision", [actions(acts[:5])]))
    else:
        sections.append(("What needs a decision", [note("Nothing needs a decision. Every check is within its limits.")]))
    closed = section(d, "Closed issues")
    n_closed = sum(len(b["rows"]) for b in closed if b["type"] == "table" and b["title"].startswith("Problems"))
    if n_closed:
        sections.append(("Resolved during the period", [note("%d issue%s found during this period %s now closed. Closed issues do not count in the score." % (
            n_closed, "" if n_closed == 1 else "s", "is" if n_closed == 1 else "are"))]))
    exceptions = section(d, "Accepted exceptions")
    if exceptions:
        n = sum(len(b["rows"]) for b in exceptions if b["type"] == "table")
        sections.append(("Known issues that were accepted", [note("%d known issue%s %s been formally accepted and %s kept out of the score. They are listed with the "
                                                                   "reason and the approver in the detailed report." % (n, "" if n == 1 else "s", "has" if n == 1 else "have", "is" if n == 1 else "are"))]))
    cap = [b for b in section(d, "Capacity forecast") if b["type"] == "table"]
    if cap:
        sections.append(("Disk space outlook", cap[:1]))
    compare = [b for b in (section(d, "This period against the last one") + section(d, "This month against last month")) if b["type"] == "table"]
    if compare:
        sections.append(("Compared with the previous period", compare[:1]))
    sections.append(("About this report", [note("This is the management summary for the %s. The technical detail (CPU, memory, disks, network, databases, patches) is in the "
                                                "daily, weekly and monthly technical reports, which come from the same data." % PERIOD.get(ptype, "period"))]))
    d.sections = sections
    d.banner = d.banner
    return d
