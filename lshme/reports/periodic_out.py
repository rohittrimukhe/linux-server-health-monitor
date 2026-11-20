# Linux Server Health Monitor Enterprise
# Copyright (c) 2026 LRS Services Pvt Ltd, Mumbai. All rights reserved.
"""Excel, CSV, text and JSON output for the daily, weekly and monthly reports."""
import csv
import json
import time

from lshme.reports import xlsx
from lshme.reports.periodic import Bar, text_of


def cells(row):
    return [text_of(c) for c in row]


def sheets(doc):
    used = set()
    out = []
    summary = [[doc.title], [doc.subtitle], [doc.copyright], []]
    summary += [[k, v] for k, v in doc.meta]
    summary += [[], ["Overall status", doc.status], ["Health score", doc.score]]
    hdr = []
    for title, blocks in doc.sections[:4]:      # in short, status, SLA, checklist
        for b in blocks:
            if b["type"] == "points":
                summary += [[], [title]] + [[txt] for _, txt in b["items"]]
            elif b["type"] == "kpis":
                summary += [[], [title]] + [[lbl, v] for v, lbl, _ in b["items"]]
            elif b["type"] == "table":
                summary += [[], [title]]
                hdr.append(len(summary))
                summary.append(list(b["headers"]))
                summary += [cells(r) for r in b["rows"]]
    out.append((xlsx.sheet_name("Summary", used), summary, hdr))
    for title, blocks in doc.sections[4:]:
        rows, hdrs = [[title]], []
        for b in blocks:
            if b["type"] == "table":
                rows += [[], [b["title"]] if b["title"] else []]
                hdrs.append(len(rows))
                rows.append(list(b["headers"]))
                rows += [cells(r) for r in b["rows"]]
            elif b["type"] == "note":
                rows += [[], [b["text"]]]
            elif b["type"] == "actions":
                rows += [[]] + [["%d. %s" % (i, m), "Why it matters: " + r] for i, (_, m, r) in enumerate(b["items"], 1)]
            elif b["type"] == "kv":
                rows += [[]] + [cells(r) for r in b["rows"]]
        if len(rows) > 1:
            out.append((xlsx.sheet_name(title, used), rows, hdrs))
    if doc.growth_rows:
        out.append((xlsx.sheet_name("Growth data (bytes)", used),
                    [["Date", "Kind", "Name", "Size bytes", "Change bytes"]] + [list(r) for r in doc.growth_rows], [0]))
    return out


def render_xlsx(doc, path):
    sh = sheets(doc)
    empty = {"severity": [], "scores": [], "filesystems": [], "score_title": ""}
    if xlsx.available():
        xlsx._render_openpyxl(sh, path, doc.brand_logo, empty)
        return path, "openpyxl"
    xlsx._render_builtin(sh, path)
    return path, "builtin"


def render_csv(doc, path):
    """One CSV with the growth data (date, kind, name, size, change) - ready for pivot tables."""
    files = []
    if doc.growth_rows:
        p = path
        with open(p, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["date", "kind", "name", "size_bytes", "change_bytes"])
            w.writerows(doc.growth_rows)
        files.append(p)
    else:
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            for title, blocks in doc.sections:
                for b in blocks:
                    if b["type"] == "table" and b["rows"]:
                        w.writerow([title])
                        w.writerow(b["headers"])
                        w.writerows(cells(r) for r in b["rows"])
                        w.writerow([])
        files.append(path)
    return files


def render_txt(doc, path):
    out = [doc.title, doc.subtitle, "=" * 78, "Status: %s   Score: %s/100" % (doc.status, doc.score), ""]
    for n, (title, blocks) in enumerate(doc.sections, 1):
        out += ["", "%d. %s" % (n, title.upper()), "-" * 78]
        for b in blocks:
            t = b["type"]
            if t == "kpis":
                out.append("   ".join("%s: %s" % (lbl, v) for v, lbl, _ in b["items"]))
            elif t == "points":
                out += ["* " + txt for _, txt in b["items"]]
            elif t == "table":
                if b["title"]:
                    out.append(b["title"])
                rows = [list(map(str, b["headers"]))] + [cells(r) for r in b["rows"]]
                wid = [min(40, max(len(r[i]) if i < len(r) else 0 for r in rows)) for i in range(len(rows[0]))]
                for ri, r in enumerate(rows):
                    out.append("  ".join(((r[i] if i < len(r) else "").replace("\n", " ")[:wid[i]]).ljust(wid[i]) for i in range(len(wid))).rstrip())
                    if ri == 0:
                        out.append("  ".join("-" * w for w in wid))
            elif t == "note":
                out.append(b["text"])
            elif t == "actions":
                for i, (lv, m, r) in enumerate(b["items"], 1):
                    out += ["%d. [%s] %s" % (i, lv.upper(), m), "   Why it matters: " + r]
            elif t == "kv":
                out += ["%-14s %-40s %-14s %s" % tuple(map(str, r)) for r in b["rows"]]
            out.append("")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(out) + "\n")
    return path


def render_json(doc, path):
    def conv(c):
        return {"bar": c.pct} if isinstance(c, Bar) else text_of(c)
    data = {"report": doc.ptype, "variant": doc.variant, "title": doc.title, "host": doc.host, "status": doc.status, "score": doc.score,
            "generated": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(doc.generated)),
            "sections": [{"title": t, "tables": [{"title": b["title"], "headers": b["headers"], "rows": [[conv(c) for c in r] for r in b["rows"]]}
                                                 for b in blocks if b["type"] == "table"]} for t, blocks in doc.sections],
            "growth": [{"date": r[0], "kind": r[1], "name": r[2], "size_bytes": r[3], "change_bytes": r[4]} for r in doc.growth_rows]}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1)
    return path
