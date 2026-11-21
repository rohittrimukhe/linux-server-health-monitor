# Linux Server Health Monitor Enterprise
# Copyright (c) 2026 LRS Services Pvt Ltd, Mumbai. All rights reserved.
"""The report templates: what each report is for, who reads it, what it contains, and a live preview built from the latest health check.

Every report is made from the same data; a template decides what is shown and in which words. `vitals templates list` and the
dashboard (Reports > Report templates) show them; the preview uses the newest stored health-check result of this server."""
import json
import os
import shutil
import tempfile

from lshme.reports import periodic

TEMPLATES = [
    {"id": "executive-daily", "name": "Executive summary - daily", "ptype": "daily", "variant": "executive", "audience": "CEO and management",
     "pages": "1-2", "formats": "PDF, HTML, Excel",
     "about": "The answer to 'is everything running?' in plain words: status, service target, what needs a decision, disk space outlook. No technical tables.",
     "sections": ["At a glance", "In plain words", "Are we meeting the service target?", "What needs a decision", "Disk space outlook"],
     "sent_to": "to_summary of the daily schedule"},
    {"id": "executive-weekly", "name": "Executive summary - weekly", "ptype": "weekly", "variant": "executive", "audience": "CEO and management",
     "pages": "2", "formats": "PDF, HTML, Excel",
     "about": "The week for management: availability against the target, what changed since last week, risks and decisions.",
     "sections": ["At a glance", "In plain words", "Are we meeting the service target?", "What needs a decision", "Disk space outlook", "Compared with the previous period"],
     "sent_to": "to_summary of the weekly schedule"},
    {"id": "executive-monthly", "name": "Executive summary - monthly", "ptype": "monthly", "variant": "executive", "audience": "CEO, board and customers",
     "pages": "2", "formats": "PDF, HTML, Excel",
     "about": "The month on two pages: service level achieved, incidents, capacity outlook and the decisions needed. Suitable for a management review.",
     "sections": ["At a glance", "In plain words", "Are we meeting the service target?", "What needs a decision", "Disk space outlook", "Compared with the previous period"],
     "sent_to": "to_summary of the monthly schedule"},
    {"id": "daily", "name": "Daily server checklist", "ptype": "daily", "variant": "summary", "audience": "IT operations",
     "pages": "6-10", "formats": "PDF, HTML, Excel, CSV, text, JSON",
     "about": "A colour-coded checklist of the last 24 hours: CPU (every core), memory, network, filesystems, databases, folders, top programs and what needs attention.",
     "sections": ["In short", "Overall status", "SLA achieved", "Daily checklist", "CPU and memory", "Network", "Filesystem usage and growth", "Database size and growth",
                  "Top folders and growth", "Top 10 resource users", "Accepted exceptions", "What needs attention"], "sent_to": "to and cc of the daily schedule"},
    {"id": "weekly", "name": "Weekly server report", "ptype": "weekly", "variant": "summary", "audience": "IT operations and team leads",
     "pages": "12-18", "formats": "PDF, HTML, Excel, CSV, text, JSON",
     "about": "The daily content for 7 days plus growth by date, health by day, this week against last week, repeating problems, patch compliance and capacity forecast.",
     "sections": ["In short", "Overall status", "SLA achieved", "Checklist for the period", "CPU and memory", "Network", "Capacity forecast", "Data growth by date",
                  "Health by day", "This period against the last one", "Repeating problems and log errors", "Patch and compliance", "What needs attention"],
     "sent_to": "to and cc of the weekly schedule"},
    {"id": "monthly", "name": "Monthly server report (summary)", "ptype": "monthly", "variant": "summary", "audience": "IT management",
     "pages": "15-20", "formats": "PDF, HTML, Excel, CSV, text, JSON",
     "about": "30 days: trends, capacity forecast, growth of every filesystem and database, this month against last month, backups, certificates and licences.",
     "sections": ["In short", "Overall status", "SLA achieved", "Checklist for the period", "Capacity forecast", "Data growth by date", "This month against last month",
                  "Backup verification", "Certificate and licence calendar", "What needs attention"], "sent_to": "to and cc of the monthly schedule"},
    {"id": "monthly-comprehensive", "name": "Monthly server report (comprehensive)", "ptype": "monthly", "variant": "comprehensive", "audience": "IT, auditors",
     "pages": "28-35", "formats": "PDF, HTML, Excel, CSV, text, JSON",
     "about": "Everything in the monthly summary plus module health, every finding, run history, server roles, hardware and storage inventory, services, open ports and installed software.",
     "sections": ["Module health", "All findings", "Run history", "Server roles", "Server inventory", "Services and open ports", "Databases and virtual machines", "Installed software"],
     "sent_to": "to and cc of the monthly schedule"},
    {"id": "database", "name": "Database report", "ptype": "weekly", "variant": "database", "audience": "Database administrators",
     "pages": "4-8", "formats": "PDF, HTML, Excel, CSV, text, JSON",
     "about": "Only the database view: every database, biggest tables, Oracle tablespaces, backups and database findings.",
     "sections": ["Database size and growth", "Database health", "Backup verification", "Capacity forecast"], "sent_to": "to_database of the schedule"},
]

CONTENT_TYPES = {"html": "text/html; charset=utf-8", "pdf": "application/pdf"}


def catalog():
    return [dict(t) for t in TEMPLATES]


def get(tid):
    for t in TEMPLATES:
        if t["id"] == tid:
            return t
    raise ValueError("unknown template '%s' (see: vitals templates list)" % tid)


def latest_run(app):
    try:
        from lshme.core.model import RunResult
        with open(os.path.join(app.paths.data_dir, "latest.json"), encoding="utf-8") as f:
            return RunResult.from_dict(json.load(f))
    except (OSError, ValueError, KeyError):
        raise ValueError("There is no health-check result to build a preview from yet. Run a health check first (vitals run).")


def preview(app, tid, fmt="html"):
    """(bytes, content type) of a template built from the newest health-check result of this server. Nothing is stored."""
    from lshme import reports
    t = get(tid)
    fmt = (fmt or "html").lower()
    if fmt not in CONTENT_TYPES:
        raise ValueError("preview formats: html, pdf")
    run = latest_run(app)
    doc = periodic.build(run, app.cfg, app.paths, t["ptype"], t["variant"])
    tmp = tempfile.mkdtemp(prefix="lrs-preview-")
    try:
        files, notes = reports.write_periodic([doc], [fmt], tmp, "preview", app.cfg.get("main", "general", "logo", ""))
        path = [f for f in files if f.endswith("." + fmt)]
        if not path:
            raise ValueError("The preview could not be built: %s" % ("; ".join(notes) or "unknown error"))
        with open(path[0], "rb") as f:
            return f.read(), CONTENT_TYPES[fmt]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
