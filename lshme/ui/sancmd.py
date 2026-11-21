# Linux Server Health Monitor Enterprise
# Copyright (c) 2026 LRS Services Pvt Ltd, Mumbai. All rights reserved.
"""`vitals san ...`: add, test, list and remove SAN storage arrays (any make). The password is asked without echo and kept in
credentials.conf; it is never printed."""
import sys

from lshme.ops import san
from lshme.ui import console as C


def add_parser(sub):
    a = sub.add_parser("san", help="SAN storage: types, list, add, test NAME, show NAME, remove NAME")
    a.add_argument("action", choices=("types", "list", "add", "test", "show", "remove"))
    a.add_argument("name", nargs="?", default="", help="the storage's short name, e.g. hhk-de")
    a.add_argument("--type", default="", help="make / driver (see: vitals san types)")
    a.add_argument("--controllers", default="", help="management IP of controller A and B, comma separated")
    a.add_argument("--user", default=None, help="read-only user on the storage")
    a.add_argument("--port", type=int, default=0)
    a.add_argument("--verify-tls", action="store_true", help="check the storage's HTTPS certificate (most arrays use a self-signed one)")
    a.add_argument("--no-test", action="store_true", help="add: save without testing the connection")
    return a


def types():
    C.out("Supported makes (type = driver):")
    for t, (label, port) in san.TYPES.items():
        C.out("  %-11s %s  (port %d)" % (t, label, port))
        C.out("              " + san.HOWTO[t])
    C.out("Full guide: /opt/lshme/docs/SAN_STORAGE.md")


def run(app, args):
    if args.action == "types":
        types()
        return 0
    if args.action == "list":
        rows = [[a["name"], a["type"], san.TYPES.get(a["type"], (a["type"],))[0], ", ".join(a["controllers"]), a["port"], a["username"] or "-",
                 "set" if a["password"] else "MISSING", "yes" if a["enabled"] else "no"] for a in san.arrays(app.cfg)]
        if not rows:
            C.out("No SAN storage added yet. Add one with: vitals san add")
            return 0
        C.out(C.table(["Name", "Type", "Make", "Controllers", "Port", "User", "Password", "Enabled"], rows))
        return 0
    if args.action in ("test", "show", "remove") and not args.name:
        raise ValueError("give the storage name: vitals san %s NAME (see: vitals san list)" % args.action)
    if args.action == "remove":
        san.find(app.cfg, args.name)
        san.remove(app, args.name)
        C.out(C.c("Removed SAN storage %s (and its saved password)." % args.name, "green"))
        return 0
    if args.action in ("test", "show"):
        a = san.find(app.cfg, args.name)
        res = san.test(a)
        C.out(C.c(("OK  " if res["ok"] else "FAILED  ") + res["message"], "green" if res["ok"] else "red"))
        if res["ok"] and args.action == "show":
            d = san.collect(a)
            C.out(C.table(["Controller", "Status", "Detail"], [[c["name"], c["status"], c.get("detail", "")] for c in d["controllers"]]))
            C.out(C.table(["Pool", "RAID", "Used %", "Status"], [[p["name"], p["raid"], p["used_percent"] if p["used_percent"] is not None else "-", p["status"]] for p in d["pools"]]))
            bad = [x for x in d["drives"] if x["status"] not in ("optimal", "spare", "present")]
            C.out("Drives: %d, not optimal: %d%s" % (len(d["drives"]), len(bad), (" (" + ", ".join("%s %s" % (x["slot"], x["status"]) for x in bad[:8]) + ")") if bad else ""))
            C.out("Volumes: %d    Hardware parts not optimal: %d" % (len(d["volumes"]), sum(1 for h in d["hardware"] if h["status"] not in ("optimal", "ok"))))
            if d.get("perf"):
                p = d["perf"]
                C.out("I/O now: %.0f read + %.0f write IOPS, %.1f + %.1f MB/s%s" % (p.get("read_iops") or 0, p.get("write_iops") or 0, p.get("read_mbps") or 0,
                                                                               p.get("write_mbps") or 0, (", %.1f ms" % p["latency_ms"]) if p.get("latency_ms") else ""))
        return 0 if res["ok"] else 1
    # add (or change): ask for what was not given on the command line
    interactive = sys.stdin.isatty()
    name = args.name or (C.ask("Short name for this storage (e.g. hhk-de)") if interactive else "")
    t = args.type
    if not t and interactive:
        types()
        t = C.ask("Type", "santricity", choices=list(san.TYPES))
    t = san.kind(t or "santricity")
    if interactive:
        C.out(C.c("How to connect: " + san.HOWTO[t], "cyan"))
    ctl = args.controllers or (C.ask("Controller A management IP") + "," + C.ask("Controller B management IP (Enter if none)") if interactive else "")
    port = args.port or (int(C.ask("Port", str(san.TYPES[t][1]))) if interactive else san.TYPES[t][1])
    user = args.user if args.user is not None else ("" if t in ("reach",) else (C.ask("Read-only user" + (" (leave empty for Pure: the API token goes in the password)" if t == "pure" else ""),
                                                                                        "monitor" if t in ("santricity", "msa") else "") if interactive else ""))
    password = ""
    if t != "reach":
        password = C.ask("Password" + (" / API token" if t == "pure" else "") + " (not shown; Enter keeps the saved one)", secret=True) if interactive else ""
    name = san.save(app, name, t, ctl, user, password or None, port, args.verify_tls)
    C.out(C.c("Saved SAN storage %s (%s)." % (name, san.TYPES[t][0]), "green"))
    if args.no_test:
        return 0
    res = san.test(san.find(app.cfg, name))
    C.out(C.c(("Connection OK: " if res["ok"] else "Connection FAILED: ") + res["message"], "green" if res["ok"] else "red"))
    if res["ok"]:
        C.out("It is now checked with every health check and by the live watcher; it appears in the reports.")
    return 0 if res["ok"] else 1
