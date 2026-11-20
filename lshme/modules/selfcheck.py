# Linux Server Health Monitor Enterprise
# Copyright (c) 2026 LRS Services Pvt Ltd, Mumbai. All rights reserved.
"""Health of LRS Vitals itself: watcher, schedules, report freshness, e-mail delivery, disk space."""
from lshme.ops import selfhealth

NAME = "selfcheck"
TITLE = "LRS Vitals Health"
WEIGHT = 0


def run(ctx, res):
    checks = selfhealth.check(ctx.cfg, ctx.paths, ctx.runner)
    res.table("Health of the monitoring", ["Check", "Status", "Detail"], [[c["name"], c["status"].upper(), c["detail"]] for c in checks], kind="summary")
    for c in checks:
        if c["status"] != "ok":
            res.add("CRITICAL" if c["status"] == "critical" else "WARNING", c["code"] or "TOOL_PROBLEM", c["name"], "%s: %s" % (c["name"], c["detail"]))
    bad = sum(1 for c in checks if c["status"] != "ok")
    res.summary = "monitoring is working" if not bad else "%d problem(s) with the monitoring itself" % bad
