# Linux Server Health Monitor Enterprise
# Copyright (c) 2026 LRS Services Pvt Ltd, Mumbai. All rights reserved.
"""Self-repair of LRS Vitals itself (never of anything else on the server).

`vitals repair` (also from the dashboard, and from the central server for each member over SSH):
  1. the program starts (imports, Python version)
  2. configuration is readable, data / report / log folders are writable
  3. every LRS Vitals job (lshme-*.service) in failed state: the reason is read from its log, then the failed state is cleared
     so the next scheduled run starts clean
  4. the live watcher and the web service: started again when they are enabled but not running
  5. the schedule timers: installed again when one is missing
The live watcher runs step 3 by itself, after the failure has been alerted once with its reason."""
import json
import os
import re
import sys
import time

SERVICES = ("lshme-watch", "lshme-web")
ERR = re.compile(r"(Traceback|Error|error|failed|Failed|refused|denied|No such|cannot|Cannot|timed out|exit code|status=\d)")


def failed_jobs(runner):
    """LRS Vitals units (lshme-*) that are in failed state."""
    if not runner.has("systemctl"):
        return []
    r = runner.run(["systemctl", "list-units", "lshme-*", "--all", "--state=failed", "--no-legend", "--plain"], timeout=20)
    out = []
    for line in (r.out or "").splitlines():
        parts = line.split()
        if parts and parts[0].startswith("lshme-") and parts[0].endswith((".service", ".timer")):
            out.append(parts[0])
    return out


def cause(runner, unit, lines=60):
    """The most telling line from the unit's last run, e.g. 'Error: SMTP host is not configured'."""
    if not runner.has("journalctl"):
        return ""
    r = runner.run(["journalctl", "-u", unit, "-n", str(lines), "--no-pager", "-o", "cat"], timeout=20)
    rows = [l.strip() for l in (r.out or "").splitlines() if l.strip()]
    rows = [l for l in rows if not l.startswith(("Started ", "Starting ", "Finished ", "Stopped "))]
    tb = [i for i, l in enumerate(rows) if l.startswith("Traceback")]
    if tb:                                                     # a Python error: its last line names it (systemd's own lines are skipped)
        tail = [l for l in rows[tb[-1] + 1:] if not l.startswith(("File ", "lshme-", unit)) and ":" in l]
        exc = [l for l in tail if re.match(r"^[\w.]+(Error|Exception|Exit|Interrupt|Busy)\b", l)]
        pick = (exc or tail or [rows[-1]])[-1 if not exc else 0]
        return re.sub(r"\s+", " ", pick)[:300]
    hits = [l for l in rows if ERR.search(l)]
    pick = hits[-1] if hits else (rows[-1] if rows else "")
    return re.sub(r"\s+", " ", pick)[:300]


def reset(runner, unit):
    r = runner.run(["systemctl", "reset-failed", unit], timeout=20)
    return r.ok


def repair(app, fix=True):
    """Run every step. Returns [{step, ok, detail, fixed}] (ok False only when it is still broken)."""
    runner, steps = app.runner, []

    def add(step, ok, detail, fixed=False):
        steps.append({"step": step, "ok": bool(ok), "detail": detail, "fixed": bool(fixed)})

    # 1. the program starts
    try:
        import lshme.app  # noqa: F401
        import lshme.reports.periodic  # noqa: F401
        import lshme.web.server  # noqa: F401
        from lshme import VERSION
        add("Program", True, "LRS Vitals %s on Python %d.%d" % (VERSION, sys.version_info[0], sys.version_info[1]))
    except Exception as e:
        add("Program", False, "does not start: %s (reinstall: bash /tmp/upgrade.sh)" % e)
    # 2. configuration and folders
    try:
        app.reload()
        add("Configuration", True, "readable (%s)" % app.paths.config_dir)
    except Exception as e:
        add("Configuration", False, "cannot be read: %s" % e)
    bad = []
    for d in (app.paths.data_dir, app.paths.report_dir, app.paths.log_dir):
        try:
            os.makedirs(d, exist_ok=True)
            probe = os.path.join(d, ".lrs-write-test")
            with open(probe, "w") as f:
                f.write("ok")
            os.remove(probe)
        except OSError as e:
            bad.append("%s (%s)" % (d, e.strerror or e))
    add("Folders", not bad, "writable" if not bad else "not writable: " + "; ".join(bad))
    # 3. failed jobs: say why, then clear them
    jobs = failed_jobs(runner)
    if not jobs:
        add("Jobs", True, "no LRS Vitals job has failed")
    for u in jobs:
        why = cause(runner, u) or "no reason in its log"
        done = reset(runner, u) if fix else False
        add("Job %s" % u, done, "failed: %s%s" % (why, " - failed state cleared, the next run starts clean" if done else ""), fixed=done)
    # 4. watcher and web service
    for s in SERVICES:
        if not runner.has("systemctl"):
            break
        enabled = runner.run(["systemctl", "is-enabled", s], timeout=15).out.strip() == "enabled"
        active = runner.run(["systemctl", "is-active", s], timeout=15).out.strip() == "active"
        if not enabled:
            continue
        if active:
            add("Service %s" % s, True, "running")
            continue
        if fix:
            runner.run(["systemctl", "restart", s], timeout=60)
            time.sleep(3)
            active = runner.run(["systemctl", "is-active", s], timeout=15).out.strip() == "active"
        why = "" if active else cause(runner, s + ".service")
        add("Service %s" % s, active, "was stopped - started again" if active else "does not start: %s" % (why or "see journalctl -u %s" % s), fixed=active)
    # 5. timers
    try:
        from lshme.ops import scheduler
        _, rows = scheduler.status(app.cfg, runner)
        missing = [x["name"] for x in rows if x.get("enabled") and not x.get("installed")]
        if missing and fix:
            rep = scheduler.install(app.cfg, runner, app.launcher(), app.global_args())
            ok = not rep["errors"]
            add("Timers", ok, ("installed again: " + ", ".join(missing)) if ok else "; ".join(rep["errors"]), fixed=ok)
        else:
            add("Timers", not missing, "all installed" if not missing else "missing: " + ", ".join(missing))
    except Exception as e:
        add("Timers", False, "could not be checked: %s" % e)
    try:
        app.audit("self-repair", steps=json.dumps(steps)[:2000])
    except Exception:
        pass
    return steps


def auto(app, problems, state, now=None):
    """Called by the live watcher: clear failed LRS Vitals jobs once their failure has been alerted (with its reason)."""
    now = now or time.time()
    done = state.setdefault("self_repair", {})
    out = []
    for key, p in problems.items():
        m = re.search(r"SERVICE_FAILED:(lshme-[\w.@-]+)$", key)
        if not m or not p.get("alerted"):
            continue
        unit = m.group(1)
        if now - done.get(unit, 0) < 3600:                         # at most once an hour per job
            continue
        done[unit] = now
        if reset(app.runner, unit):
            out.append(unit)
    return out
