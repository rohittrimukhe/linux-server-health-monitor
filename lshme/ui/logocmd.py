# Linux Server Health Monitor Enterprise
# Copyright (c) 2026 LRS Services Pvt Ltd, Mumbai. All rights reserved.
"""`vitals logo ...`: the customer logo shown on every report."""
from lshme.core.util import fmt_bytes
from lshme.ops import branding
from lshme.ui import console as C


def add_parser(sub):
    a = sub.add_parser("logo", help="customer logo for the reports: show, set FILE, remove, push")
    a.add_argument("action", choices=("show", "set", "remove", "push"))
    a.add_argument("file", nargs="?", default="", help="set: a PNG or JPEG file")
    a.add_argument("--server", default="all", help="set/remove: a server name from servers.conf (default: the logo for every server)")
    a.add_argument("--servers", default="", help="push: only these servers (comma separated)")
    return a


def _line(d):
    return "%s  %dx%d  %s" % (d["path"], d["width"], d["height"], fmt_bytes(d["bytes"])) if d else "none"


def run(app, args):
    if args.action == "show":
        info = branding.listing(app)
        C.out("Default logo: " + _line(info["default"]))
        for s in info["servers"]:
            C.out("  %-20s %s%s" % (s["name"], _line(s["logo"]), "" if s["own"] or not s["logo"] else "  (default)"))
        return 0
    if args.action == "set":
        if not args.file:
            raise ValueError("give the image: vitals logo set /path/logo.png [--server NAME]")
        try:
            with open(args.file, "rb") as f:
                raw = f.read()
        except OSError as e:
            raise ValueError("cannot read %s: %s" % (args.file, e))
        d = branding.save(app, raw, args.server)
        C.out(C.c("Stored %s (%dx%d, %s). New reports use it." % (d["path"], d["width"], d["height"], fmt_bytes(d["bytes"])), "green"))
        C.out("Other servers: vitals logo push")
        return 0
    if args.action == "remove":
        C.out("Removed." if branding.remove(app, args.server) else "There was no logo to remove.")
        return 0
    names = [x.strip() for x in args.servers.split(",") if x.strip()] or None
    bad = 0
    for r in branding.push(app, names):
        C.out("%-20s %s" % (r["server"], C.c("sent", "green") if r["ok"] else C.c("FAILED: " + r["detail"], "red")))
        bad += 0 if r["ok"] else 1
    return 1 if bad else 0
