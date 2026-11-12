# Linux Server Health Monitor Enterprise
# Copyright (c) 2026 LRS Services Pvt Ltd, Mumbai. All rights reserved.
"""TXT, JSON and CSV renderers."""
import csv
import json
import textwrap
import time

from lshme.core.util import redact_obj


def _fmt_table(t, width=160):
    headers = [str(h) for h in t.headers]
    rows = [[str(c) for c in r] for r in t.rows]
    widths = [len(h) for h in headers]
    for r in rows:
        for i, c in enumerate(r[:len(widths)]):
            widths[i] = min(max(widths[i], len(c)), 60)
    total = sum(widths) + 3 * len(widths)
    if total > width and widths:
        widths[-1] = max(10, widths[-1] - (total - width))
    line = "+" + "+".join("-" * (w + 2) for w in widths) + "+"
    out = [line, "| " + " | ".join(h[:w].ljust(w) for h, w in zip(headers, widths)) + " |", line]
    for r in rows:
        r = r + [""] * (len(widths) - len(r))
        out.append("| " + " | ".join(c.replace("\n", " ")[:w].ljust(w) for c, w in zip(r, widths)) + " |")
    out.append(line)
    return "\n".join(out)


def render_txt(doc, path):
    L = []
    bar = "=" * 100
    L += [bar, doc.title.upper(), doc.subtitle, doc.copyright, bar]
    for k, v in doc.meta:
        L.append("%-16s: %s" % (k, v))
    if doc.banner:
        L += ["", "*** %s ***" % doc.banner]
    c = doc.counts
    L += ["", "OVERALL HEALTH: %s   SCORE: %s/100   critical=%d warning=%d unknown=%d info=%d" % (
        doc.status, doc.score, c.get("CRITICAL", 0), c.get("WARNING", 0), c.get("UNKNOWN", 0), c.get("INFO", 0)), ""]
    L.append("MODULE HEALTH")
    from lshme.core.model import Table
    hdr = ["Module", "Score", "Status", "Critical", "Warning", "Summary"] if doc.kind == "host" else ["Server", "Score", "Status", "Critical", "Warning", "Top issue"]
    L.append(_fmt_table(Table("", hdr, doc.module_rows)))
    L += ["", "FINDINGS (%d)" % len(doc.findings), "-" * 100]
    for f in doc.findings:
        L.append("[%-8s] %-28s %s" % (f["severity"], f["code"], f["component"]))
        for w in textwrap.wrap(f["message"], 96):
            L.append("           " + w)
        if f.get("recommendation") and f["severity"] in ("CRITICAL", "WARNING"):
            for w in textwrap.wrap("Action: " + f["recommendation"], 96):
                L.append("           " + w)
        if f.get("evidence") and doc.report_type != "executive":
            for ln in f["evidence"].splitlines()[:8]:
                L.append("           > " + ln[:150])
    if doc.recommendations:
        L += ["", "RECOMMENDED ACTIONS", "-" * 100]
        for i, (sev, comp, rec) in enumerate(doc.recommendations, 1):
            L.append("%2d. [%s] %s: %s" % (i, sev, comp, rec))
    for s in doc.sections:
        L += ["", bar, "%s   (score: %s, status: %s)" % (s.title.upper(), s.score if s.score is not None else "n/a", s.status), bar]
        if s.summary:
            L.append(s.summary)
        for n in s.notes:
            L.append("Note: " + n)
        for t in s.tables:
            L += ["", "-- %s --" % t.title]
            if t.note:
                L.append(t.note)
            L.append(_fmt_table(t))
    for title, text in doc.appendix:
        L += ["", bar, "APPENDIX: " + title, bar, text]
    L += ["", "End of report — generated %s" % time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(doc.generated)), doc.copyright]
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")
    return path


def render_json(run_or_runs, path):
    if isinstance(run_or_runs, list):
        data = {"fleet": [r.to_dict() for r in run_or_runs]}
    else:
        data = run_or_runs.to_dict()
    with open(path, "w", encoding="utf-8") as f:
        json.dump(redact_obj(data), f, indent=2, sort_keys=True, default=str)
    return path


def render_csv(doc, path, run=None):
    """Findings CSV, plus a metrics CSV alongside it for a single-host run."""
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["host", "severity", "code", "module", "component", "message", "recommendation"])
        for x in doc.findings:
            w.writerow([doc.host or "", x["severity"], x["code"], x["module"], x["component"], x["message"], x.get("recommendation", "")])
    files = [path]
    if run is not None:
        mpath = path[:-4] + "_metrics.csv"
        with open(mpath, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["host", "collected_at", "metric", "value"])
            ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(run.collected_at))
            for k, v in sorted(run.metrics().items()):
                w.writerow([run.host, ts, k, v])
        files.append(mpath)
    return files
