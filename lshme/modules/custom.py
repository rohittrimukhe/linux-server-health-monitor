# Linux Server Health Monitor Enterprise
# Copyright (c) 2026 LRS Services Pvt Ltd, Mumbai. All rights reserved.
"""Administrator-defined checks using the Nagios plugin convention.

Exit code 0=OK, 1=WARNING, 2=CRITICAL, 3=UNKNOWN; the first output line is the
message. Checks come from custom.conf [check:NAME] sections and from an
optional checks directory. Scripts must be owned by root and not writable by
group/others, otherwise they are refused.
"""
import os
import shlex
import stat

NAME = "custom"
TITLE = "Custom Checks"
WEIGHT = 6

CODES = {0: None, 1: "WARNING", 2: "CRITICAL"}


def safe_script(path):
    try:
        st = os.stat(path)
    except OSError as e:
        return False, str(e)
    if st.st_uid != 0 and os.geteuid() == 0:
        return False, "not owned by root"
    if st.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        return False, "writable by group/others"
    if not os.access(path, os.X_OK):
        return False, "not executable"
    return True, ""


def run(ctx, res):
    checks = []
    for section in ctx.cfg.sections("custom", "check:"):
        s = ctx.cfg.section("custom", section)
        if s.get("command"):
            checks.append((section.split(":", 1)[1], s))
    d = ctx.cfg.get("custom", "general", "checks_dir", "")
    if d and ctx.runner.isdir(d):
        for f in ctx.runner.listdir(d):
            checks.append((os.path.splitext(f)[0], {"command": os.path.join(d, f)}))
    if not checks:
        res.status = "SKIPPED"
        res.summary = "no custom checks configured"
        res.skip("define [check:NAME] in custom.conf")
        return
    rows = []
    default_timeout = ctx.cfg.getint("custom", "general", "default_timeout", 60)
    for name, s in checks:
        args = shlex.split(s["command"])
        ok, why = safe_script(args[0]) if os.path.isabs(args[0]) else (True, "")
        if not ok:
            rows.append([name, s["command"], "REFUSED", why])
            res.add("WARNING", "CUSTOM_CHECK_REFUSED", name, "Custom check %s refused: %s" % (name, why))
            continue
        r = ctx.run(args, timeout=int(s.get("timeout", default_timeout)))
        msg = (r.out.splitlines() or [r.err or ""])[0][:300]
        if r.timed_out or r.missing or r.rc not in CODES:
            sev = "UNKNOWN"
        else:
            sev = CODES.get(r.rc)
        rows.append([name, s["command"], sev or "OK", msg])
        if sev:
            res.add(sev, "CUSTOM_CHECK", name, "%s: %s" % (name, msg or r.brief_error()), evidence=r.out[:2000],
                    recommendation=s.get("recommendation", ""))
        perf = r.out.split("|", 1)[1] if "|" in r.out else ""
        for item in perf.split():
            if "=" in item:
                k, v = item.split("=", 1)
                try:
                    res.metric("custom.%s.%s" % (name, k), float(v.split(";")[0].rstrip("%sBKMGTb")))
                except ValueError:
                    pass
    res.table("Custom checks", ["Check", "Command", "Result", "Message"], rows, kind="summary")
    res.summary = "%d custom check(s)" % len(rows)
