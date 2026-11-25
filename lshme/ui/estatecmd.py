# Linux Server Health Monitor Enterprise
# Copyright (c) 2026 LRS Services Pvt Ltd, Mumbai. All rights reserved.
"""`vitals estate daily|weekly|monthly`: one report (and one e-mail) for every server and the SAN storage, on the central server."""
from lshme.reports import consolidated
from lshme.ui import console as C


def add_parser(sub):
    a = sub.add_parser("estate", help="one report for every server and the SAN storage: estate glance first, then each server (central server)")
    a.add_argument("type", nargs="?", default="weekly", choices=("daily", "weekly", "monthly"))
    a.add_argument("--comprehensive", action="store_true", help="monthly: the comprehensive version of every server's report")
    a.add_argument("--formats", default="html,pdf")
    a.add_argument("--email", action="store_true", help="send it as one e-mail (glance in the body, the report attached)")
    a.add_argument("--to", default="", help="other recipients (comma separated)")
    return a


def run(app, args):
    fmts = [f.strip() for f in args.formats.split(",") if f.strip()]
    doc, outdir, files, notes = consolidated.generate(app, args.type, "comprehensive" if args.comprehensive else "summary", fmts)
    C.out(C.c("%s: %s" % (doc.title, doc.status), "green" if doc.status == "HEALTHY" else "yellow" if doc.status == "WARNING" else "red"))
    for f in files:
        C.out("  " + f)
    for n in notes:
        C.out(C.c("  note: " + n, "yellow"))
    if args.email:
        to = [x.strip() for x in args.to.split(",") if x.strip()] or None
        res = consolidated.send(app, doc, files, to)
        C.out(C.c("E-mail sent to %s" % ", ".join(res.get("recipients", [])), "green") if res.get("ok") else C.c("E-mail failed: %s" % res.get("error"), "red"))
        return 0 if res.get("ok") else 1
    return 0
