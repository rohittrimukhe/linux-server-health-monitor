# Linux Server Health Monitor Enterprise
# Copyright (c) 2026 LRS Services Pvt Ltd, Mumbai. All rights reserved.
"""`vitals repair`: check and repair LRS Vitals itself on this server (see lshme.ops.selfrepair). --check only reports."""
import json

from lshme.ops import selfrepair
from lshme.ui import console as C


def add_parser(sub):
    a = sub.add_parser("repair", help="check and repair LRS Vitals itself: program, folders, failed jobs (with the reason), watcher, web, timers")
    a.add_argument("--check", action="store_true", help="only report, change nothing")
    a.add_argument("--json", action="store_true", help="machine-readable answer (used by the central server)")
    return a


def run(app, args):
    steps = selfrepair.repair(app, fix=not args.check)
    if args.json:
        print(json.dumps({"steps": steps, "ok": all(s["ok"] for s in steps)}))
        return 0
    for s in steps:
        mark = C.c("FIXED", "green") if s["fixed"] else (C.c("OK   ", "green") if s["ok"] else C.c("FAIL ", "red"))
        C.out("%s %-24s %s" % (mark, s["step"], s["detail"]))
    bad = [s for s in steps if not s["ok"]]
    C.out(C.c("Everything is working." if not bad else "%d item(s) still need attention (see above)." % len(bad), "green" if not bad else "red"))
    return 0 if not bad else 1
