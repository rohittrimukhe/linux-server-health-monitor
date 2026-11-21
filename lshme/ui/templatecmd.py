# Linux Server Health Monitor Enterprise
# Copyright (c) 2026 LRS Services Pvt Ltd, Mumbai. All rights reserved.
"""`vitals templates ...`: explore the report templates and preview them with this server's data."""
import os

from lshme.reports import templates
from lshme.ui import console as C


def add_parser(sub):
    a = sub.add_parser("templates", help="report templates: list, preview ID [--format pdf|html] [-o FILE]")
    a.add_argument("action", choices=("list", "preview"))
    a.add_argument("id", nargs="?", default="", help="preview: template id (see list)")
    a.add_argument("--format", default="html", choices=("html", "pdf"))
    a.add_argument("-o", "--output", default="", help="preview: file to write (default: ./<id>.<format>)")
    return a


def run(app, args):
    if args.action == "list":
        C.table(["Id", "Name", "For", "Pages", "Sent to"], [[t["id"], t["name"], t["audience"], t["pages"], t["sent_to"]] for t in templates.catalog()])
        C.out("Preview one: vitals templates preview executive-weekly --format pdf")
        return 0
    if not args.id:
        raise ValueError("give a template id: vitals templates preview ID (see: vitals templates list)")
    body, _ = templates.preview(app, args.id, args.format)
    out = args.output or "%s.%s" % (args.id, args.format)
    with open(out, "wb") as f:
        f.write(body)
    C.out(C.c("Wrote %s (%d KB). It is built from the newest health check of this server." % (os.path.abspath(out), len(body) // 1024), "green"))
    return 0
