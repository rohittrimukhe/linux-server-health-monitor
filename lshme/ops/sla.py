# Linux Server Health Monitor Enterprise
# Copyright (c) 2026 LRS Services Pvt Ltd, Mumbai. All rights reserved.
"""Availability / SLA reporting from the real-time incident log.

Downtime is time with at least one confirmed CRITICAL problem; degraded time
is time with only WARNING problems. Figures are calculated per server and per
component (service, filesystem, port, process, module) for a period
(normally a calendar month), with incidents, MTTR and monitoring coverage.
"""
import calendar
import json
import os
import socket
import time

from lshme import APP_NAME, VERSION
from lshme.core.model import Table
from lshme.core.util import fmt_duration
from lshme.reports.doc import ReportDoc, Section


def month_range(month=None, now=None):
    """(start, end, label) for 'YYYY-MM'; default is the previous full month."""
    now = now or time.time()
    if month:
        y, m = [int(x) for x in month.split("-")]
    else:
        t = time.localtime(now)
        y, m = (t.tm_year, t.tm_mon - 1) if t.tm_mon > 1 else (t.tm_year - 1, 12)
    start = time.mktime((y, m, 1, 0, 0, 0, 0, 0, -1))
    end = time.mktime((y + (m == 12), m % 12 + 1, 1, 0, 0, 0, 0, 0, -1))
    return start, end, "%s %d" % (calendar.month_name[m], y)


def current_month(now=None):
    t = time.localtime(now or time.time())
    return "%04d-%02d" % (t.tm_year, t.tm_mon)


def load_events(path):
    out = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                try:
                    out.append(json.loads(line))
                except ValueError:
                    continue
    except (IOError, OSError):
        pass
    return sorted(out, key=lambda e: e.get("ts", 0))


def incidents_from(events, now):
    """Rebuild incidents: {key, component, message, start, crit_start, end, severity, ongoing}."""
    open_, done = {}, []
    for e in events:
        k = e.get("key")
        if e["event"] == "open":
            open_[k] = {"key": k, "component": e.get("component", ""), "message": e.get("message", ""), "start": e["ts"],
                        "crit_start": e["ts"] if e.get("severity") == "CRITICAL" else None, "severity": e.get("severity", "WARNING")}
        elif e["event"] == "escalate" and k in open_:
            inc = open_[k]
            if e.get("severity") == "CRITICAL" and inc["crit_start"] is None:
                inc["crit_start"] = e["ts"]
            inc["severity"] = "CRITICAL" if e.get("severity") == "CRITICAL" else inc["severity"]
            inc["message"] = e.get("message", inc["message"])
        elif e["event"] == "close" and k in open_:
            inc = open_.pop(k)
            inc.update(end=e["ts"], ongoing=False)
            done.append(inc)
    for inc in open_.values():
        inc.update(end=now, ongoing=True)
        done.append(inc)
    return sorted(done, key=lambda i: i["start"])


def _union(intervals):
    total, cur_s, cur_e = 0.0, None, None
    for s, e in sorted(intervals):
        if cur_e is None or s > cur_e:
            if cur_e is not None:
                total += cur_e - cur_s
            cur_s, cur_e = s, e
        else:
            cur_e = max(cur_e, e)
    if cur_e is not None:
        total += cur_e - cur_s
    return total


def _clip(s, e, start, end):
    s, e = max(s, start), min(e, end)
    return (s, e) if e > s else None


def component_group(key, component):
    kind = key.split(":", 1)[0]
    label = {"svc": "Service", "port": "Port", "proc": "Process", "disk": "Filesystem", "inode": "Filesystem",
             "ro": "Filesystem", "netfs": "Filesystem", "cpu": "System", "load": "System", "memory": "System",
             "swap": "System"}.get(kind)
    if kind == "mod":
        name = key.split(":")[1]
        try:
            from lshme import modules
            return modules.load(name).TITLE, component
        except Exception:
            return name, component
    return label or "Other", component


def compute(events, start, end, now=None, coverage=None, interval=60):
    now = min(now or time.time(), end)
    incidents = []
    for inc in incidents_from(events, now):
        span = _clip(inc["start"], inc["end"], start, end)
        if span is None:
            continue
        crit = _clip(inc["crit_start"], inc["end"], start, end) if inc["crit_start"] is not None else None
        incidents.append(dict(inc, span=span, crit=crit))
    period = max(1.0, now - start)
    down = _union([i["crit"] for i in incidents if i["crit"]])
    any_problem = _union([i["span"] for i in incidents])
    comps = {}
    for i in incidents:
        g = component_group(i["key"], i["component"])
        c = comps.setdefault(g, {"crit": [], "all": [], "count": 0, "critical": 0})
        c["all"].append(i["span"])
        c["count"] += 1
        if i["crit"]:
            c["crit"].append(i["crit"])
            c["critical"] += 1
    rows = []
    for (group, comp), c in comps.items():
        d = _union(c["crit"])
        rows.append({"group": group, "component": comp, "downtime": d, "degraded": _union(c["all"]) - d,
                     "availability": 100.0 * (1 - d / period), "incidents": c["count"], "critical": c["critical"]})
    rows.sort(key=lambda r: (-r["downtime"], -r["incidents"]))
    resolved = [i for i in incidents if not i["ongoing"]]
    durations = [i["span"][1] - i["span"][0] for i in resolved]
    cov = None
    if coverage:
        ticks = sum(n for day, n in coverage.items() if start <= time.mktime(time.strptime(day, "%Y-%m-%d")) < end)
        cov = min(100.0, 100.0 * ticks * interval / period)
    return {"start": start, "end": end, "until": now, "period": period, "downtime": down, "degraded": any_problem - down,
            "availability": 100.0 * (1 - down / period), "incidents": incidents, "components": rows,
            "critical_incidents": sum(1 for i in incidents if i["crit"]), "mttr": sum(durations) / len(durations) if durations else None,
            "longest": max(durations) if durations else None, "coverage": cov}


def daily_scores(history_db, host, start, end):
    try:
        from lshme.engine.history import History
        h = History(history_db)
        rows = h.con.execute("SELECT date(ts, 'unixepoch', 'localtime'), AVG(score), MIN(score), COUNT(*) FROM runs "
                             "WHERE host=? AND ts>=? AND ts<? GROUP BY 1 ORDER BY 1", (host, start, end)).fetchall()
        h.close()
        return rows
    except Exception:
        return []


def build_doc(result, label, cfg, host=None, scores=None):
    host = host or socket.gethostname()
    target = cfg.getfloat("main", "sla", "target_percent", 99.9)
    d = ReportDoc()
    d.kind = "sla"
    d.host = host
    d.report_type = "sla"
    d.title = "Availability & SLA Report"
    d.subtitle = "%s — %s" % (host, label)
    d.overview_title = "Availability by component"
    d.overview_headers = ["Component", "Availability %", "Status", "Critical incidents", "All incidents", "Downtime / degraded"]
    d.chart_title = "Availability % by component"
    d.score_label, d.score_suffix = "AVAILABILITY", "%"
    g = cfg.section("main", "general")
    period_txt = "%s to %s" % (time.strftime("%Y-%m-%d", time.localtime(result["start"])),
                               time.strftime("%Y-%m-%d %H:%M", time.localtime(result["until"])))
    d.meta = [(k, v) for k, v in (("Company", g.get("company")), ("Customer", g.get("customer")), ("Server", host),
                                   ("Environment", g.get("environment")), ("Period", period_txt), ("SLA target", "%g%%" % target),
                                   ("Monitoring coverage", "%.1f%%" % result["coverage"] if result["coverage"] is not None else "unknown"),
                                   ("Generated by", "%s v%s" % (APP_NAME, VERSION))) if v]
    avail = result["availability"]
    status = "CRITICAL" if avail < target else ("WARNING" if result["incidents"] else "HEALTHY")
    d.health = {"score": round(avail, 3), "status": status,
                "method": "Availability = time without a confirmed CRITICAL problem / period. Degraded = WARNING-only time."}
    findings = []
    if avail < target:
        findings.append({"severity": "CRITICAL", "code": "SLA_BREACH", "component": host, "module": "sla", "evidence": "",
                         "message": "Server availability %.3f%% is below the %g%% target (%s downtime)" % (avail, target, fmt_duration(result["downtime"])),
                         "recommendation": "Review the incidents below and address the components with the most downtime."})
    for r in result["components"]:
        if r["availability"] < target:
            findings.append({"severity": "WARNING", "code": "SLA_COMPONENT_BREACH", "component": "%s %s" % (r["group"], r["component"]),
                             "module": "sla", "evidence": "", "recommendation": "",
                             "message": "%s %s availability %.3f%% (%s down)" % (r["group"], r["component"], r["availability"], fmt_duration(r["downtime"]))})
    if result["coverage"] is not None and result["coverage"] < 90:
        findings.append({"severity": "WARNING", "code": "SLA_LOW_COVERAGE", "component": "watch", "module": "sla", "evidence": "",
                         "message": "Real-time monitoring covered only %.1f%% of the period; availability reflects monitored time" % result["coverage"],
                         "recommendation": "Keep 'healthcheck watch' running (healthcheck watch install)."})
    d.findings = findings
    d.counts = {"CRITICAL": sum(1 for f in findings if f["severity"] == "CRITICAL"),
                "WARNING": sum(1 for f in findings if f["severity"] == "WARNING"), "UNKNOWN": 0, "INFO": 0}
    d.module_rows = [["Server (overall)", "%.3f" % avail, "BREACH" if avail < target else "OK", result["critical_incidents"],
                      len(result["incidents"]), "%s / %s" % (fmt_duration(result["downtime"]), fmt_duration(result["degraded"]))]]
    for r in result["components"]:
        d.module_rows.append(["%s: %s" % (r["group"], r["component"]), "%.3f" % r["availability"],
                              "BREACH" if r["availability"] < target else "OK", r["critical"], r["incidents"],
                              "%s / %s" % (fmt_duration(r["downtime"]), fmt_duration(r["degraded"]))])
    d.charts["modules"] = []  # availability bars all round to ~100%; the component table carries the detail
    summary = Table("Summary", ["Measure", "Value"], [
        ["Availability", "%.3f%% (target %g%%)" % (avail, target)], ["Downtime", fmt_duration(result["downtime"])],
        ["Degraded (warnings only)", fmt_duration(result["degraded"])], ["Incidents (critical / all)", "%d / %d" % (result["critical_incidents"], len(result["incidents"]))],
        ["Mean time to recover", fmt_duration(result["mttr"]) if result["mttr"] is not None else "n/a"],
        ["Longest incident", fmt_duration(result["longest"]) if result["longest"] is not None else "n/a"],
        ["Monitoring coverage", "%.1f%%" % result["coverage"] if result["coverage"] is not None else "unknown"]], kind="summary")
    inc_rows = [[time.strftime("%Y-%m-%d %H:%M", time.localtime(i["span"][0])),
                 "ongoing" if i["ongoing"] else time.strftime("%Y-%m-%d %H:%M", time.localtime(i["span"][1])),
                 fmt_duration(i["span"][1] - i["span"][0]), i["severity"], i["component"], i["message"]] for i in result["incidents"]]
    sections = [Section("sla", "Availability summary", status, None, "", [], [summary])]
    sections.append(Section("incidents", "Incidents", status, None, "%d incident(s) in the period" % len(inc_rows), [],
                            [Table("Incident log", ["Start", "End", "Duration", "Severity", "Component", "Problem"], inc_rows or [["", "", "", "", "", "no incidents"]], kind="summary")]))
    if scores:
        sections.append(Section("scores", "Daily health score", status, None, "", [],
                                [Table("Daily health score (scheduled/manual runs)", ["Day", "Average", "Minimum", "Runs"],
                                       [[r[0], round(r[1] or 0), r[2], r[3]] for r in scores], kind="summary")]))
    d.sections = sections
    d.recommendations = [(f["severity"], f["component"], f["recommendation"]) for f in findings if f["recommendation"]]
    return d


def generate(app, month=None, formats=("html", "pdf", "xlsx"), now=None):
    from lshme import reports
    start, end, label = month_range(month, now)
    state = {}
    try:
        with open(os.path.join(app.paths.data_dir, "watch_state.json"), encoding="utf-8") as f:
            state = json.load(f)
    except (IOError, OSError, ValueError):
        pass
    events = load_events(os.path.join(app.paths.data_dir, "watch_events.jsonl"))
    result = compute(events, start, end, now, state.get("coverage"), state.get("interval", 60))
    host = socket.gethostname()
    doc = build_doc(result, label, app.cfg, host, daily_scores(app.paths.history_db, host, start, end))
    outdir = reports.output_dir(app.paths.report_dir, host, "sla", time.strftime("%Y%m", time.localtime(start)))
    base = "%s_sla_%s" % (reports.safe(host), time.strftime("%Y-%m", time.localtime(start)))
    files, notes = reports.write_all(doc, [f for f in formats if f != "json"], outdir, base, logo=app.cfg.get("main", "general", "logo", ""))
    if "json" in formats:
        path = os.path.join(outdir, base + ".json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump({k: v for k, v in result.items()}, f, indent=1, default=str)
        files.append(path)
    return result, doc, outdir, files, notes
