# Linux Server Health Monitor Enterprise
# Copyright (c) 2026 LRS Services Pvt Ltd, Mumbai. All rights reserved.
"""`vitals update ...`: check for a newer LRS Vitals, update safely now, or show the last result.
The daily automatic run (21:00) is 'vitals update auto'."""
import time

from lshme.ops import autoupdate
from lshme.ui import console as C


def add_parser(sub):
    a = sub.add_parser("update", help="safe self-update: status, check, now (back up, update, self-test, roll back on failure), auto")
    a.add_argument("action", choices=("status", "check", "now", "auto"))
    a.add_argument("--force", action="store_true", help="now: reinstall even when already up to date")
    a.add_argument("--quiet", "-q", action="store_true")
    return a


def run(app, args):
    up = autoupdate.Updater(app)
    s = autoupdate.settings(app.cfg)
    if args.action == "status":
        st = up.state()
        C.out("Installed commit: %s" % (up.installed()[:12] or "unknown (installed before automatic updates)"))
        C.out("Automatic update: %s, every day at %s from %s (%s)" % ("ON" if s["auto"] else "off", s["time"], s["repo"], s["ref"]))
        if st:
            C.out("Last check: %s   newest: %s %s" % (time.strftime("%Y-%m-%d %H:%M", time.localtime(st.get("checked_at", 0))) if st.get("checked_at") else "-",
                                                     (st.get("latest") or "")[:12], st.get("latest_message", "")))
            C.out("Last result: %s - %s" % (st.get("result", "-"), st.get("message", "")))
        return 0
    if args.action == "check":
        info = up.check()
        C.out(C.c("A newer version is available: %s %s" % (info["sha"][:12], info["message"]), "yellow") if info["available"]
              else C.c("Up to date (%s)." % info["sha"][:12], "green"))
        return 0
    res = up.update(auto=args.action == "auto", force=args.force)
    if not args.quiet:
        for line in res["log"]:
            C.out("  " + line)
    colour = {"updated": "green", "current": "green", "off": "cyan", "postponed": "yellow"}.get(res["result"], "red")
    C.out(C.c("%s: %s" % (res["result"].upper(), res["message"]), colour))
    return 0 if res["result"] in ("updated", "current", "off", "postponed") else 1
