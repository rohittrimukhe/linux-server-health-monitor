# Linux Server Health Monitor Enterprise
# Copyright (c) 2026 LRS Services Pvt Ltd, Mumbai. All rights reserved.
"""`vitals bmc ...`: add, test, list and remove server hardware read through its management controller (iDRAC, iLO, XClarity,
IMM, iRMC, CIMC, Supermicro, any Redfish BMC, or IPMI). The password is asked without echo and kept in credentials.conf."""
import sys

from lshme.ops import bmc
from lshme.ui import console as C


def add_parser(sub):
    a = sub.add_parser("bmc", help="server hardware (iDRAC, iLO, XClarity, IPMI ...): types, list, add, test NAME, show NAME, remove NAME")
    a.add_argument("action", choices=("types", "list", "add", "test", "show", "remove"))
    a.add_argument("name", nargs="?", default="", help="a short name, e.g. kvm1-xcc")
    a.add_argument("--type", default="", help="make (see: vitals bmc types)")
    a.add_argument("--address", default="", help="the BMC's management IP or name")
    a.add_argument("--user", default=None, help="read-only user on the BMC")
    a.add_argument("--port", type=int, default=0)
    a.add_argument("--server", default=None, help="the server this hardware runs (name in servers.conf, or _local for this server)")
    a.add_argument("--verify-tls", action="store_true", help="check the BMC's HTTPS certificate (most use a self-signed one)")
    a.add_argument("--no-test", action="store_true", help="add: save without testing the connection")
    return a


def types():
    C.out("Supported makes (type):")
    for t, (label, port) in bmc.TYPES.items():
        C.out("  %-11s %s  (port %d)" % (t, label, port))
        C.out("              " + bmc.HOWTO[t])


def run(app, args):
    if args.action == "types":
        types()
        return 0
    if args.action == "list":
        rows = [[a["name"], bmc.TYPES.get(a["type"], (a["type"],))[0], a["address"], a["port"], a["username"] or "-", a["server"] or "-",
                 "set" if a["password"] else "MISSING", "yes" if a["enabled"] else "no"] for a in bmc.bmcs(app.cfg)]
        if not rows:
            C.out("No server hardware added yet. Add one with: vitals bmc add")
            return 0
        C.table(["Name", "Make", "Address", "Port", "User", "For server", "Password", "Enabled"], rows)
        return 0
    if args.action in ("test", "show", "remove") and not args.name:
        raise ValueError("give the name: vitals bmc %s NAME (see: vitals bmc list)" % args.action)
    if args.action == "remove":
        bmc.find(app.cfg, args.name)
        bmc.remove(app, args.name)
        C.out(C.c("Removed hardware %s (and its saved password)." % args.name, "green"))
        return 0
    if args.action in ("test", "show"):
        a = bmc.find(app.cfg, args.name)
        res = bmc.test(a)
        C.out(C.c(("OK  " if res["ok"] else "FAILED  ") + res["message"], "green" if res["ok"] else "red"))
        if res["ok"] and args.action == "show":
            d = bmc.collect(a)
            C.table(["Part", "Kind", "Reading", "Status"],
                    [[x["name"], "power supply", ("%.0f W" % x["watts"]) if x.get("watts") is not None else "", x["status"]] for x in d["psus"]] +
                    [[x["name"], "fan", ("%.0f rpm" % x["rpm"]) if x.get("rpm") is not None else "", x["status"]] for x in d["fans"]] +
                    [[x["name"], "temperature", ("%.0f C" % x["celsius"]) if x.get("celsius") is not None else "", x["status"]] for x in d["temps"]] +
                    [[x["name"], "drive", x.get("model", ""), x["status"]] for x in d["drives"]])
            for e in d["events"][:10]:
                C.out("  event %s  %-8s %s" % (e["time"], e["severity"], e["text"]))
        return 0 if res["ok"] else 1
    interactive = sys.stdin.isatty()
    name = args.name or (C.ask("Short name for this hardware (e.g. kvm1-xcc)") if interactive else "")
    t = args.type
    if not t and interactive:
        types()
        t = C.ask("Type", "redfish", choices=list(bmc.TYPES))
    t = bmc.kind(t or "redfish")
    if interactive:
        C.out(C.c("How to connect: " + bmc.HOWTO[t], "cyan"))
    address = args.address or (C.ask("BMC management IP or name") if interactive else "")
    port = args.port or (int(C.ask("Port", str(bmc.TYPES[t][1]))) if interactive else bmc.TYPES[t][1])
    user = args.user if args.user is not None else (C.ask("Read-only user") if interactive else "")
    server = args.server if args.server is not None else (C.ask("Which server runs on this hardware (name from servers.conf, _local for this one, Enter to skip)", "") if interactive else "")
    password = C.ask("Password (not shown; Enter keeps the saved one)", secret=True) if interactive else ""
    name = bmc.save(app, name, t, address, user, password or None, port, args.verify_tls, True, server)
    C.out(C.c("Saved hardware %s (%s)." % (name, bmc.TYPES[t][0]), "green"))
    if args.no_test:
        return 0
    res = bmc.test(bmc.find(app.cfg, name))
    C.out(C.c(("Connection OK: " if res["ok"] else "Connection FAILED: ") + res["message"], "green" if res["ok"] else "red"))
    if res["ok"]:
        C.out("It is now checked with every health check and appears in the reports and on the dashboard.")
    return 0 if res["ok"] else 1
