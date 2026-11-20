# Linux Server Health Monitor Enterprise
# Copyright (c) 2026 Rohit Santosh Trimukhe. All rights reserved.
"""Health of LRS Vitals itself, and what is kept for how long.

check() answers "is the monitoring working?": is the real-time watcher sampling, are the schedule timers installed,
did the reports run on time, did the last emails go out, is there disk space. It is shown in the web dashboard,
and the 'selfcheck' module turns every problem into a finding so it reaches the reports and alerts."""
import json
import os
import shutil
import time

from lshme.engine.history import History
from lshme.ops import reportlist, retention, scheduler

DAY = 86400.0
# how old the newest report of a schedule may be before something is wrong
MAX_AGE_DAYS = {"hourly": 0.2, "daily": 2.6, "weekly": 8.5, "monthly": 33.0}
REPORT_DEFAULT_DAYS = {"daily": 30, "weekly": 90, "monthly": 365, "adhoc": 30, "fleet": 90, "custom": 60}


def check(cfg, paths, runner, now=None):
    """[{'name', 'status': ok|warning|critical, 'detail', 'code'}]"""
    now = now or time.time()
    out = []

    def add(name, status, detail, code):
        out.append({"name": name, "status": status, "detail": detail, "code": code})

    # real-time watcher
    sd = cfg.get("schedule", "scheduler", "systemd_dir", "/etc/systemd/system")
    cron_text = runner.read(cfg.get("schedule", "scheduler", "cron_file", "/etc/cron.d/lshme"))
    installed = os.path.exists(os.path.join(sd, "lshme-watch.service")) or "watch run" in cron_text
    last = None
    try:
        with open(os.path.join(paths.data_dir, "watch_metrics.json"), encoding="utf-8") as f:
            pts = json.load(f)
        last = pts[-1]["t"] if pts else None
    except (IOError, OSError, ValueError, KeyError, IndexError, TypeError):
        pass
    if not installed:
        add("Real-time watcher", "warning", "Not installed: there are no live graphs and no real-time alerts. Install it from Operations or with 'vitals watch install'.", "TOOL_WATCHER_MISSING")
    elif last is None or now - last > 240:
        add("Real-time watcher", "critical", "Installed but not sampling%s. Real-time alerts are not being sent." % (
            "" if last is None else " since " + time.strftime("%d %b %H:%M", time.localtime(last))), "TOOL_WATCHER_STALE")
    else:
        add("Real-time watcher", "ok", "Sampling every minute (last sample %d s ago)." % max(0, now - last), "")
    # schedules and the reports they should have produced
    try:
        backend, rows = scheduler.status(cfg, runner)
    except Exception as e:
        backend, rows = "unknown", []
        add("Schedules", "warning", "Could not read the schedule state: %s" % e, "TOOL_SCHEDULE_UNKNOWN")
    reports = reportlist.list_reports(paths, 600)
    newest = {}
    for r in reports:
        if r["category"] not in newest or r["ts"] > newest[r["category"]]:
            newest[r["category"]] = r["ts"]
    for s in rows:
        if not s["enabled"]:
            continue
        if not s["installed"]:
            add("Schedule %s" % s["name"], "warning", "Enabled but its timer is not installed, so it will never run. Press 'Install / refresh timers'.", "TOOL_SCHEDULE_MISSING")
            continue
        sched = scheduler.get(cfg, s["name"])
        freq = sched.frequency if sched else "daily"
        limit = MAX_AGE_DAYS.get(freq)
        ts = newest.get(s["name"])
        if limit is None:
            add("Schedule %s" % s["name"], "ok", "Installed (%s)." % s["when"], "")
        elif ts is None:
            add("Schedule %s" % s["name"], "ok", "Installed (%s); no report has been written yet." % s["when"], "")
        elif now - ts > limit * DAY:
            add("Schedule %s" % s["name"], "warning", "The last %s report is %.1f days old (expected within %.0f). Check 'journalctl -u lshme-%s'." % (
                s["name"], (now - ts) / DAY, limit, s["name"]), "TOOL_REPORT_LATE")
        else:
            add("Schedule %s" % s["name"], "ok", "Last report %.1f days ago." % ((now - ts) / DAY), "")
        for e in (sched.validate() if sched else []):
            add("Schedule %s" % s["name"], "warning", e, "TOOL_SCHEDULE_INVALID")
    # e-mail delivery
    try:
        h = History(paths.history_db)
        try:
            recent = h.deliveries(7, 20)
        finally:
            h.close()
    except Exception:
        recent = []
    if recent:
        ts, host, run_id, ok, recipients, attempts, detail = recent[0]
        if ok:
            add("E-mail delivery", "ok", "Last message sent %.1f days ago to %s." % ((now - ts) / DAY, recipients or "the configured recipients"), "")
        else:
            add("E-mail delivery", "critical", "The last e-mail failed after %s attempt(s): %s" % (attempts, str(detail)[:160]), "TOOL_EMAIL_FAILED")
    # disk space for reports and the history database
    warn = retention.free_space_warning(cfg, paths)
    try:
        free = shutil.disk_usage(paths.report_dir if os.path.isdir(paths.report_dir) else paths.data_dir)
        detail = "%.1f GB free (%.0f%%)." % (free.free / 1024.0 ** 3, 100.0 * free.free / free.total)
    except OSError:
        detail = ""
    add("Disk space for reports", "warning" if warn else "ok", warn or detail, "TOOL_DISK_LOW" if warn else "")
    try:
        size = os.path.getsize(paths.history_db)
        add("History database", "ok", "%.1f MB; used for growth, trends and forecasts." % (size / 1048576.0), "")
    except OSError:
        add("History database", "warning", "Not created yet: growth and trend sections stay empty until a health check has run.", "TOOL_HISTORY_MISSING")
    return out


def retention_view(cfg, paths, now=None):
    """What is kept, for how long, and what it uses now: per report type, then the other data."""
    now = now or time.time()
    cats = {}
    root = paths.report_dir
    if os.path.isdir(root):
        for host in os.listdir(root):
            hp = os.path.join(root, host)
            if not os.path.isdir(hp) or host.startswith("_") and host != "_fleet":
                continue
            for cat in os.listdir(hp):
                cp = os.path.join(hp, cat)
                if not os.path.isdir(cp):
                    continue
                c = cats.setdefault(cat if cat in REPORT_DEFAULT_DAYS else "custom", {"count": 0, "bytes": 0, "oldest": None, "newest": None})
                for stamp in os.listdir(cp):
                    sp = os.path.join(cp, stamp)
                    if not os.path.isdir(sp):
                        continue
                    c["count"] += 1
                    mt = os.path.getmtime(sp)
                    c["oldest"] = mt if c["oldest"] is None else min(c["oldest"], mt)
                    c["newest"] = mt if c["newest"] is None else max(c["newest"], mt)
                    for dp, _, files in os.walk(sp):
                        for f in files:
                            try:
                                c["bytes"] += os.path.getsize(os.path.join(dp, f))
                            except OSError:
                                pass
    g = lambda key, default: cfg.getint("retention", "retention", key, default)
    types = []
    for cat in ("daily", "weekly", "monthly", "adhoc", "fleet", "custom"):
        c = cats.get(cat, {"count": 0, "bytes": 0, "oldest": None, "newest": None})
        keep = g(cat + "_days", REPORT_DEFAULT_DAYS[cat])
        types.append({"type": cat, "keep_days": keep, "count": c["count"], "bytes": c["bytes"], "oldest": c["oldest"], "newest": c["newest"],
                      "next_removal": (c["oldest"] + keep * DAY) if c["oldest"] else None})
    other = [{"name": "Application logs", "keep": "%d days" % g("logs_days", 30)}, {"name": "Error logs", "keep": "%d days" % g("error_logs_days", 90)},
             {"name": "Audit log (who changed what)", "keep": "%d days" % g("audit_logs_days", 365)}, {"name": "E-mail log", "keep": "%d days" % g("email_log_days", 90)},
             {"name": "History database (trends, growth)", "keep": "%d days" % g("history_days", 365)},
             {"name": "Configuration backups", "keep": "the newest %d" % g("config_backups_keep", 30)},
             {"name": "Compression", "keep": "reports older than %d days are compressed" % g("compress_after_days", 7)},
             {"name": "Newest report of each type", "keep": "always kept" if cfg.getbool("retention", "retention", "keep_latest", True) else "not protected"}]
    space = {"cleanup": cfg.getbool("retention", "retention", "space_cleanup", True), "min_free_percent": g("min_free_percent", 10), "free_target_percent": g("free_target_percent", 15),
             "max_report_dir_gb": g("max_report_dir_gb", 0), "auto_apply": cfg.getbool("retention", "retention", "auto_apply", True)}
    try:
        u = shutil.disk_usage(root if os.path.isdir(root) else paths.data_dir)
        space.update(total=u.total, free=u.free)
    except OSError:
        pass
    return {"types": types, "other": other, "space": space}
