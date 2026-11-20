# Linux Server Health Monitor Enterprise
# Copyright (c) 2026 LRS Services Pvt Ltd, Mumbai. All rights reserved.
"""Browse the reports that have been written (newest first) and summarise one for the screen.

Used by the terminal home screen and the web interface. Reports live in
<report_dir>/<host>/<category>/<YYYYmmdd_HHMMSS>/ and, when the JSON format was chosen, carry the full result."""
import json
import os
import re
import textwrap
import time

from lshme.core.model import SEV_RANK, RunResult, iso
from lshme.ops import archive

STAMP = re.compile(r"^(\d{8})_(\d{6})")
_cache = {}


EMPTY = {"score": None, "status": "", "critical": 0, "warning": 0, "unknown": 0, "modules": 0}


def _summary(path):
    """score/status/counts from a report's JSON, cached by modification time."""
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return dict(EMPTY)
    hit = _cache.get(path)
    if hit and hit[0] == mtime:
        return hit[1]
    try:
        data = json.loads(archive.read_text(path))
        h = data.get("health", {}) if isinstance(data, dict) else {}
        if not h and isinstance(data, dict) and "score" in data:     # daily / weekly / monthly reports keep these at the top
            h = {"score": data.get("score"), "status": data.get("status", ""), "counts": data.get("counts") or {}}
        counts = h.get("counts", {}) or {}
        info = {"score": h.get("score"), "status": h.get("status", ""), "critical": counts.get("CRITICAL", 0),
                "warning": counts.get("WARNING", 0), "unknown": counts.get("UNKNOWN", 0),
                "modules": len(data.get("modules", [])) if isinstance(data, dict) else 0}
    except (OSError, ValueError, EOFError):
        info = dict(EMPTY)
    _cache[path] = (mtime, info)
    return info


def list_reports(paths, limit=300):
    """Every report run on disk, newest first: dicts with ts, host, category, dir, files, json, score, status..."""
    root = paths.report_dir
    out = []
    if not os.path.isdir(root):
        return out
    for host in sorted(os.listdir(root)):
        hp = os.path.join(root, host)
        if not os.path.isdir(hp):
            continue
        for cat in sorted(os.listdir(hp)):
            cp = os.path.join(hp, cat)
            if not os.path.isdir(cp):
                continue
            for stamp in os.listdir(cp):
                d = os.path.join(cp, stamp)
                if not os.path.isdir(d):
                    continue
                m = STAMP.match(stamp)
                try:
                    ts = time.mktime(time.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S")) if m else os.path.getmtime(d)
                except (ValueError, OverflowError, OSError):
                    ts = 0
                files = [f[0] for f in archive.list_files(d)]          # original names, whether stored plain or as name.gz
                jpath = next((archive.real_path(os.path.join(d, f)) for f in files if f.endswith(".json")), "")
                entry = {"ts": ts, "when": iso(ts) if ts else "?", "host": host, "category": cat, "dir": d, "files": files, "json": jpath}
                entry.update(_summary(jpath) if jpath else EMPTY)
                out.append(entry)
    out.sort(key=lambda r: -r["ts"])
    return out[:limit]


def load_run(json_path):
    return RunResult.from_dict(json.loads(archive.read_text(json_path)))


def latest_summary(paths):
    """The most recent full health check (data/latest.json), or None."""
    try:
        with open(os.path.join(paths.data_dir, "latest.json"), encoding="utf-8") as f:
            run = RunResult.from_dict(json.load(f))
    except (OSError, ValueError):
        return None
    return {"ts": run.collected_at, "when": iso(run.collected_at), "host": run.host, "score": run.health.get("score"),
            "status": run.health.get("status", ""), "counts": run.counts(),
            "modules": [{"name": m.name, "title": m.title, "status": m.status, "score": m.score, "summary": m.summary,
                         "findings": len(m.findings)} for m in run.modules]}


def score_series(paths, days=90, host=None):
    """[(timestamp, score)] oldest first, from the history database."""
    from lshme.engine.history import History
    try:
        h = History(paths.history_db)
        rows = h.runs(host, days)
        h.close()
    except Exception:
        return []
    pts = [(r["ts"], r["score"]) for r in rows if r["score"] is not None]
    return sorted(pts)


def run_lines(run, files=None, width=76):
    """A readable text summary of a run for the screen: headline, modules, findings with advice, files."""
    h = run.health or {}
    c = run.counts()
    lines = ["%s   %s" % (run.host, iso(run.collected_at)),
             "Health score %s / 100 - %s     Critical %d   Warning %d   Unknown %d   Info %d" % (
                 h.get("score", "?"), h.get("status", "?"), c["CRITICAL"], c["WARNING"], c["UNKNOWN"], c["INFO"]), "",
             "%-30s %-8s %-6s %s" % ("Area", "Status", "Score", "Summary"), "-" * min(width, 78)]
    for m in run.modules:
        lines.append(("%-30s %-8s %-6s %s" % (m.title[:30], m.status, "" if m.score is None else m.score, m.summary))[:width])
    findings = [f for f in run.findings("WARNING")]
    if findings:
        lines += ["", "Findings that need attention", "-" * min(width, 78)]
        for f in findings:
            head = "[%s] %s" % (f.severity, f.message)
            lines += textwrap.wrap(head, width, subsequent_indent="    ") or [head]
            if f.recommendation:
                lines += textwrap.wrap("-> " + f.recommendation, width, initial_indent="    ", subsequent_indent="       ")
    else:
        lines += ["", "No warnings or critical findings."]
    infos = [f for f in run.findings() if SEV_RANK[f.severity] < SEV_RANK["WARNING"]]
    if infos:
        lines += ["", "For information"]
        for f in infos:
            lines += textwrap.wrap("- %s" % f.message, width, subsequent_indent="  ")
    if files:
        lines += ["", "Report files", "-" * min(width, 78)] + files
    return lines
