# Linux Server Health Monitor Enterprise
# Copyright (c) 2026 LRS Services Pvt Ltd, Mumbai. All rights reserved.
"""`vitals archive ...`: list, read, tail, grep, search and e-mail stored reports, compressed or not, without unpacking them."""
import os
import sys
import time

from lshme.core.util import fmt_bytes
from lshme.ops import archive
from lshme.ui import console as C


def add_parser(sub):
    a = sub.add_parser("archive", help="stored reports: list, cat, tail, grep, search, email, compress, reindex")
    a.add_argument("action", choices=("list", "stats", "cat", "tail", "grep", "search", "email", "compress", "reindex", "files"))
    a.add_argument("target", nargs="?", default="", help="report (host/category/stamp, latest, latest:daily, part of a stamp) or pattern/words")
    a.add_argument("file", nargs="?", default="", help="file in the report (name or extension, e.g. txt, json, html)")
    a.add_argument("-n", "--lines", type=int, default=20, help="tail: number of lines")
    a.add_argument("--host", default="")
    a.add_argument("--category", default="", help="daily, weekly, monthly, adhoc ...")
    a.add_argument("--limit", type=int, default=50)
    a.add_argument("--to", default="", help="email: recipients, comma separated (default: configured)")
    a.add_argument("--days", type=float, default=None, help="compress: reports older than this many days (default: retention setting)")
    return a


def _when(ts):
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(ts))


def run(app, args):
    act, paths = args.action, app.paths
    if act in ("list", "stats", "search", "reindex"):
        ix = archive.Index(paths)
        try:
            if act == "reindex" or not ix.stats()["files"]:
                ix.rebuild()
            else:
                ix.sync()
            if act == "reindex":
                s = ix.stats()
                C.out("Indexed %d files in %d reports." % (s["files"], s["reports"]))
            elif act == "stats":
                s = ix.stats()
                saved = s["original"] - s["stored"]
                C.out("Reports: %d   files: %d (%d compressed)" % (s["reports"], s["files"], s["compressed_files"]))
                C.out("Original size %s, stored %s, saved %s (%.0f%%)" % (fmt_bytes(s["original"]), fmt_bytes(s["stored"]), fmt_bytes(saved),
                                                                         100.0 * saved / s["original"] if s["original"] else 0))
                C.out("Full-text search: %s   index: %s" % ("yes" if s["fts"] else "basic", ix.path))
            elif act == "list":
                rows = ix.listing(args.host, args.category, args.limit)
                C.table(["Report", "Taken", "Files", "Size", "Stored", "Compressed"],
                        [[r["rid"], _when(r["ts"]), r["files"], fmt_bytes(r["size"]), fmt_bytes(r["stored"]),
                          "%d/%d" % (r["compressed"], r["files"])] for r in rows])
            else:
                words = " ".join([args.target, args.file]).strip()
                rows = ix.search(words, args.limit, args.host, args.category)
                if not rows:
                    C.out("Nothing found for '%s'." % words)
                    return 1
                for r in rows:
                    C.out("%s  %s/%s" % (_when(r["ts"]), r["rid"], r["name"]))
                    C.out("    " + " ".join((r["snippet"] or "").split())[:200])
        finally:
            ix.close()
        return 0
    if act == "compress":
        out = archive.compress_old(app.cfg, paths, args.days)
        archive.refresh(paths, full=True)
        app.audit("archive-compress", folders=out["folders"], files=out["files"], saved=out["saved"])
        C.out("Compressed %d files in %d report folders, saved %s." % (out["files"], out["folders"], fmt_bytes(out["saved"])))
        return 0
    if act == "grep":
        if not args.target:
            raise ValueError("give a pattern: vitals archive grep PATTERN [--host H] [--category C]")
        n = 0
        for rid, name, i, line in archive.grep(paths, args.target, args.host, args.category, limit=args.limit if args.limit != 50 else 500):
            C.out("%s/%s:%d: %s" % (rid, name, i, line))
            n += 1
        return 0 if n else 1
    rid, d = archive.resolve(paths, args.target)
    if act == "files":
        C.out(rid)
        C.table(["File", "Size", "Stored", "Compressed"],
                [[n, fmt_bytes(size), fmt_bytes(stored), "yes" if gz else "no"] for n, stored, size, gz in archive.list_files(d)])
        return 0
    if act == "email":
        to = [x.strip() for x in args.to.replace(";", ",").split(",") if x.strip()]
        names = [x for x in args.file.split(",") if x] if args.file else None
        res = archive.email_report(app, d, to or None, names)
        app.audit("report-email", report=rid, to=",".join(to), ok=res.get("ok"))
        if res.get("ok"):
            C.out(C.c("Sent %s (%s) to %s" % (rid, ", ".join(res["attached"]), ", ".join(res["recipients"])), "green"))
            return 0
        C.out(C.c("Not sent: %s" % res.get("error"), "red"))
        return 1
    path = archive.pick_file(d, args.file or None)
    if act == "tail":
        for line in archive.tail_lines(path, args.lines):
            C.out(line)
        return 0
    out = getattr(sys.stdout, "buffer", None)                  # cat: the original bytes, so it can be piped or redirected
    try:
        with archive.open_binary(path) as f:
            while True:
                block = f.read(1 << 16)
                if not block:
                    break
                if out is not None:
                    out.write(block)
                else:
                    sys.stdout.write(block.decode("utf-8", "replace"))
        if out is not None:
            out.flush()
    except BrokenPipeError:                                    # `vitals archive cat ... | head` closed the pipe early
        try:
            os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        except (OSError, ValueError, AttributeError):
            pass
    return 0
