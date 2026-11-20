# Linux Server Health Monitor Enterprise
# Copyright (c) 2026 LRS Services Pvt Ltd, Mumbai. All rights reserved.
"""`vitals raw ...`: the daily raw data files (one per server per day, 00:00:00 to 23:59:59)."""
import json
import re
import time

from lshme.core.util import fmt_bytes
from lshme.ops import rawstore
from lshme.ui import console as C


def add_parser(sub):
    a = sub.add_parser("raw", help="daily raw data: list, show, tail, grep, stats, seal, verify, backfill, replay")
    a.add_argument("action", choices=("list", "show", "tail", "grep", "stats", "seal", "verify", "backfill", "replay", "path"))
    a.add_argument("target", nargs="?", default="", help="day (YYYY-MM-DD, today, yesterday) or pattern for grep")
    a.add_argument("--type", default="", help="record types, comma separated (sample, run, event, alert, top, ...)")
    a.add_argument("--host", default="")
    a.add_argument("-n", "--lines", type=int, default=10)
    a.add_argument("--from", dest="start", default="", help="first day (YYYY-MM-DD)")
    a.add_argument("--to", dest="end", default="", help="last day (YYYY-MM-DD)")
    a.add_argument("--all", action="store_true", help="seal: every finished day now, without waiting until 02:00")
    a.add_argument("--force", action="store_true", help="backfill: run every source again")
    return a


def _day(text):
    t = (text or "today").strip().lower()
    if t == "today":
        return rawstore.day_of(time.time())
    if t == "yesterday":
        return rawstore.day_of(time.time() - 86400)
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", t):
        raise ValueError("give a day as YYYY-MM-DD, today or yesterday")
    return t


def _types(args):
    return tuple(x.strip() for x in args.type.split(",") if x.strip()) or None


def _brief(r):
    d = r.get("data") or {}
    t = r.get("type")
    if t == "sample":
        fs = d.get("filesystems") or {}
        full = max((v.get("used_pct") or 0 for v in fs.values()), default=0)
        return "cpu %s%%  mem %s%%  swap %s%%  load/cpu %s  fullest fs %.0f%%  problems %d" % (
            d.get("cpu"), d.get("mem_pct"), d.get("swap_pct"), d.get("load_per_cpu"), full, len(d.get("problems") or {}))
    if t in ("run", "fleet"):
        h = d.get("health") or {}
        return "%s score %s %s (%s)" % (d.get("host", ""), h.get("score"), h.get("status"), d.get("_schedule") or d.get("schedule", ""))
    if t == "event":
        return "%s %s %s" % (d.get("event"), d.get("severity", ""), d.get("message") or d.get("key"))
    if t == "alert":
        return "%s %s" % (d.get("status"), d.get("subject"))
    if t == "top":
        return "top CPU: " + ", ".join("%s %.1f%%" % (g["name"], g["cpu_pct"]) for g in (d.get("by_cpu") or [])[:4])
    return json.dumps(d, sort_keys=True, default=str)[:160]


def run(app, args):
    act = args.action
    host = args.host or rawstore.host_name()
    if not rawstore.root():
        raise ValueError("the raw data store is switched off ([retention] raw_enabled = no)")
    if act == "path":
        C.out(rawstore.root())
        return 0
    if act == "list":
        rows = []
        for e in rawstore.days(host=args.host or None)[:max(args.lines, 10) if args.lines != 10 else 60]:
            m = e["manifest"] or {}
            rows.append([e["host"], e["day"], "00:00:00-23:59:59", "sealed" if e["sealed"] and not e["open"] else ("open" if not e["sealed"] else "sealed+late"),
                         fmt_bytes(e["bytes"]), m.get("records", "-"), ("%.1f%%" % m["coverage_pct"]) if m.get("coverage_pct") is not None else "-",
                         ("yes" if m.get("complete") else "no (%d gaps)" % m.get("gap_count", 0)) if m else "-"])
        C.table(["Server", "Day", "Period", "State", "Size", "Records", "Coverage", "Complete"], rows)
        return 0
    if act == "stats":
        ds = rawstore.days(host=args.host or None)
        total = sum(e["bytes"] for e in ds)
        C.out("Raw data: %s   days: %d   servers: %d   size: %s" % (rawstore.root(), len(ds), len(set(e["host"] for e in ds)), fmt_bytes(total)))
        if ds:
            C.out("Oldest day %s, newest day %s" % (ds[-1]["day"], ds[0]["day"]))
        today = rawstore.read(host, _day("today"))
        counts = {}
        for r in today:
            counts[r["type"]] = counts.get(r["type"], 0) + 1
        C.out("Today so far: %s" % (", ".join("%s %d" % kv for kv in sorted(counts.items())) or "nothing yet"))
        return 0
    if act == "seal":
        done = rawstore.seal_due(force=args.all)
        for m in done:
            C.out("sealed %s %s: %d records, coverage %.1f%%, %s" % (m["host"], m["day"], m["records"], m["coverage_pct"], fmt_bytes(m["bytes"])))
        if not done:
            C.out("Nothing to seal (days are sealed at 02:00, two hours after they end; --all seals every finished day now).")
        return 0
    if act == "verify":
        bad = 0
        for h, day, ok, detail in rawstore.verify():
            C.out("%s %s %s: %s" % ("OK " if ok else C.c("BAD", "red"), h, day, detail))
            bad += 0 if ok else 1
        return 1 if bad else 0
    if act == "backfill":
        from lshme.ops import rawreplay
        res = rawreplay.backfill(app, args.force)
        app.audit("raw-backfill", **dict((k, str(v)) for k, v in res.items()))
        for k, v in res.items():
            C.out("%-14s %s" % (k, v))
        return 0
    if act == "replay":
        from lshme.ops import rawreplay
        res = rawreplay.replay(app, args.start or None, args.end or None, args.host or None)
        app.audit("raw-replay", **res)
        C.out("Read %d day(s); added %d health checks and %d hours to the history database." % (res["days"], res["runs"], res["hours"]))
        return 0
    if act == "grep":
        if not args.target:
            raise ValueError("give a pattern: vitals raw grep PATTERN [--from DAY --to DAY] [--type sample]")
        rx = re.compile(args.target, re.I)
        n = 0
        for e in sorted(rawstore.days(host=args.host or None), key=lambda e: e["day"]):
            if (args.start and e["day"] < args.start) or (args.end and e["day"] > args.end):
                continue
            for r in rawstore.read(e["host"], e["day"], _types(args)):
                line = json.dumps(r, sort_keys=True, default=str)
                if rx.search(line):
                    C.out("%s %s %s %s" % (e["host"], r["time"], r["type"], line[:240]))
                    n += 1
        return 0 if n else 1
    day = _day(args.target)
    recs = rawstore.read(host, day, _types(args))
    if act == "tail":
        recs = recs[-args.lines:]
    if not recs:
        C.out("No records for %s on %s." % (host, day))
        return 1
    for r in recs:
        C.out("%s  %-11s %s" % (r["time"], r["type"], _brief(r)))
    return 0
