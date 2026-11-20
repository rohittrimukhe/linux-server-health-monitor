# Linux Server Health Monitor Enterprise
# Copyright (c) 2026 LRS Services Pvt Ltd, Mumbai. All rights reserved.
"""Retention: per-category report expiry, compression, optional archiving, log
and history pruning, config-backup rotation. Always previewable (dry run);
every deletion is written to the audit log. The newest report of each
host/category is never deleted when keep_latest is on.

Space optimisation: besides age limits, reports can be removed to respect a
size cap for the report directory (max_report_dir_gb) or to restore free
space on its filesystem (min_free_percent -> free_target_percent). Space
cleanup deletes the least valuable reports first (adhoc, daily, custom,
weekly, fleet, monthly; oldest first within each), never touches reports
newer than space_min_keep_days, and never the newest report per host/category."""
import json
import os
import shutil
import tarfile
import time

from lshme.core import logs
from lshme.core.config import as_bool
from lshme.core.util import fmt_bytes

CATEGORY_KEYS = {"daily": "daily_days", "weekly": "weekly_days", "monthly": "monthly_days", "adhoc": "adhoc_days",
                 "fleet": "fleet_days"}
# lower number = deleted first when space must be freed
SPACE_PRIORITY = {"adhoc": 0, "agent": 0, "daily": 1, "weekly": 3, "fleet": 4, "monthly": 5}
CUSTOM_PRIORITY = 2


def disk_usage(path):
    """(total_bytes, free_bytes) of the filesystem holding path."""
    st = os.statvfs(path)
    return st.f_blocks * st.f_frsize, st.f_bavail * st.f_frsize


def _age_days(path, now):
    try:
        return (now - os.path.getmtime(path)) / 86400.0
    except OSError:
        return 0.0


def _size(path):
    if os.path.isfile(path):
        return os.path.getsize(path)
    total = 0
    for root, _, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total


def plan(cfg, paths, now=None, usage=None):
    """Return the list of retention actions (nothing is changed).

    usage: optional callable(path) -> (total, free) used for space decisions (tests).
    """
    now = now or time.time()
    r = cfg.section("retention", "retention")
    num = lambda k, d: float(r.get(k) or d)
    keep_latest = as_bool(r.get("keep_latest", "yes"), True)
    compress_after = num("compress_after_days", 7)
    archive_dir = r.get("archive_dir", "")
    actions = []
    candidates = []  # report items eligible for space cleanup
    root = paths.report_dir
    if os.path.isdir(root):
        for host in sorted(os.listdir(root)):
            hdir = os.path.join(root, host)
            if not os.path.isdir(hdir):
                continue
            for cat in sorted(os.listdir(hdir)):
                cdir = os.path.join(hdir, cat)
                if not os.path.isdir(cdir):
                    continue
                days = num(CATEGORY_KEYS.get(cat, "custom_days"), 60)
                items = sorted(os.listdir(cdir))
                latest = items[-1] if items else None
                for it in items:
                    p = os.path.join(cdir, it)
                    age = _age_days(p, now)
                    if keep_latest and it == latest:
                        continue
                    size = _size(p)
                    if age > days:
                        actions.append({"action": "archive" if archive_dir else "delete", "path": p, "age_days": round(age, 1),
                                        "bytes": size, "reason": "%s report older than %g days" % (cat, days)})
                        continue
                    if os.path.isdir(p) and compress_after and age > compress_after:
                        actions.append({"action": "compress", "path": p, "age_days": round(age, 1), "bytes": size,
                                        "reason": "older than %g days" % compress_after})
                    candidates.append({"path": p, "category": cat, "age_days": age, "bytes": size})
    if os.path.isdir(paths.log_dir):
        for f in sorted(os.listdir(paths.log_dir)):
            p = os.path.join(paths.log_dir, f)
            if not os.path.isfile(p):
                continue
            if f.startswith("lshme-") and f.endswith(".log"):
                limit = num("logs_days", 30)
            elif f.startswith("error-") and f.endswith(".log"):
                limit = num("error_logs_days", num("logs_days", 30) * 3)
            else:
                continue
            age = _age_days(p, now)
            if age > limit:
                actions.append({"action": "delete", "path": p, "age_days": round(age, 1), "bytes": _size(p),
                                "reason": "log older than %g days" % limit})
        for f, key in (("email.log", "email_log_days"), ("audit.log", "audit_logs_days")):
            p = os.path.join(paths.log_dir, f)
            if os.path.exists(p):
                actions.append({"action": "trim", "path": p, "age_days": None, "bytes": _size(p),
                                "reason": "drop entries older than %g days" % num(key, 90), "days": num(key, 90)})
        ev = os.path.join(paths.data_dir, "watch_events.jsonl")
        if os.path.exists(ev):
            actions.append({"action": "trim", "path": ev, "age_days": None, "bytes": _size(ev),
                            "reason": "drop incidents older than %g days" % num("history_days", 365), "days": num("history_days", 365)})
    actions += space_plan(cfg, paths, candidates, actions, usage or disk_usage)
    keep = int(num("config_backups_keep", 30))
    bdir = paths.config_backup_dir
    if os.path.isdir(bdir):
        backups = sorted(x for x in os.listdir(bdir) if x.startswith("config_"))
        for old in backups[:-keep] if keep else []:
            p = os.path.join(bdir, old)
            actions.append({"action": "delete", "path": p, "age_days": round(_age_days(p, now), 1), "bytes": _size(p),
                            "reason": "more than %d config backups" % keep})
    actions.append({"action": "prune-history", "path": paths.history_db, "age_days": None, "bytes": 0,
                    "reason": "history older than %g days" % num("history_days", 365), "days": num("history_days", 365)})
    return actions


def space_plan(cfg, paths, candidates, already, usage):
    """Extra deletions needed to meet the size cap / free-space target."""
    r = cfg.section("retention", "retention")
    num = lambda k, d: float(r.get(k) or d)
    if not as_bool(r.get("space_cleanup", "yes"), True) or not os.path.isdir(paths.report_dir):
        return []
    cap = num("max_report_dir_gb", 0) * 1024 ** 3
    min_free = num("min_free_percent", 10)
    target = max(min_free, num("free_target_percent", min_free + 5))
    min_keep = num("space_min_keep_days", 1)
    freed_already = sum(a["bytes"] for a in already if a["action"] in ("delete", "archive") and a["path"].startswith(paths.report_dir))
    current = _size(paths.report_dir) - freed_already
    need = 0.0
    reasons = []
    if cap and current > cap:
        need = current - cap
        reasons.append("report directory %s exceeds cap %s" % (fmt_bytes(current), fmt_bytes(cap)))
    try:
        total, free = usage(paths.report_dir)
    except OSError:
        total, free = 0, 0
    if total:
        free_after = free + freed_already
        if 100.0 * free_after / total < min_free:
            want = target / 100.0 * total - free_after
            if want > need:
                need = want
            reasons.append("filesystem free %.1f%% below %g%% (target %g%%)" % (100.0 * free_after / total, min_free, target))
    if need <= 0:
        return []
    pool = [c for c in candidates if c["age_days"] >= min_keep]
    pool.sort(key=lambda c: (SPACE_PRIORITY.get(c["category"], CUSTOM_PRIORITY), -c["age_days"]))
    out, freed = [], 0
    for c in pool:
        if freed >= need:
            break
        out.append({"action": "delete", "path": c["path"], "age_days": round(c["age_days"], 1), "bytes": c["bytes"],
                    "reason": "space: " + "; ".join(reasons), "space": True})
        freed += c["bytes"]
    if freed < need:
        out.append({"action": "space-warning", "path": paths.report_dir, "age_days": None, "bytes": int(need - freed),
                    "reason": "could not free enough by deleting eligible reports (%s); %s still needed" % (
                        "; ".join(reasons), fmt_bytes(need - freed))})
    # a compress action on a folder we now delete is pointless
    gone = set(a["path"] for a in out)
    for a in already:
        if a["action"] == "compress" and a["path"] in gone:
            a["action"], a["reason"] = "skip", "deleted for space"
    return out


def space_check(cfg, paths, usage=None):
    """Pre-run guard: free space now (if needed) so the new reports fit. Returns applied actions."""
    acts = [a for a in plan(cfg, paths, usage=usage) if a.get("space") or a["action"] == "space-warning"]
    if not acts:
        return []
    return apply(cfg, paths, acts)


def free_space_warning(cfg, paths):
    try:
        st = os.statvfs(paths.report_dir)
        free = 100.0 * st.f_bavail / st.f_blocks if st.f_blocks else 100.0
    except OSError:
        return None
    limit = cfg.getfloat("retention", "retention", "min_free_percent", 10)
    if free < limit:
        return "Report directory %s has only %.1f%% free space (minimum %g%%)" % (paths.report_dir, free, limit)
    return None


def _trim_jsonl(path, days, now):
    cutoff = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now - days * 86400))
    kept = removed = 0
    lines = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            try:
                rec = json.loads(line)
                t = rec.get("time", "")
                if not t and rec.get("ts"):  # watch incident log uses epoch "ts"
                    t = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(rec["ts"]))
            except (ValueError, TypeError, AttributeError):
                t = ""
            if t and t < cutoff:
                removed += 1
            else:
                lines.append(line)
                kept += 1
    if removed:
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.writelines(lines)
        os.replace(tmp, path)
    return removed


def apply(cfg, paths, actions, now=None):
    now = now or time.time()
    archive_dir = cfg.get("retention", "retention", "archive_dir", "")
    done = []
    for a in actions:
        p = a["path"]
        try:
            if a["action"] == "delete":
                if os.path.isdir(p) and not os.path.islink(p):
                    shutil.rmtree(p)
                elif os.path.exists(p):
                    os.remove(p)
            elif a["action"] == "archive":
                rel = os.path.relpath(p, paths.report_dir)
                dest = os.path.join(archive_dir, rel)
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                shutil.move(p, dest)
                a["destination"] = dest
            elif a["action"] == "compress":
                tgz = p.rstrip("/") + ".tar.gz"
                with tarfile.open(tgz, "w:gz") as tar:
                    tar.add(p, arcname=os.path.basename(p))
                os.utime(tgz, (os.path.getmtime(p), os.path.getmtime(p)))
                shutil.rmtree(p)
                a["destination"] = tgz
            elif a["action"] == "trim":
                a["removed_entries"] = _trim_jsonl(p, a["days"], now)
            elif a["action"] in ("skip", "space-warning"):
                a["result"] = a["action"]
                done.append(a)
                if a["action"] == "space-warning":
                    logs.get().warning("retention: %s", a["reason"])
                continue
            elif a["action"] == "prune-history":
                if os.path.exists(p):
                    from lshme.engine.history import History
                    h = History(p)
                    a["removed_runs"] = h.prune(a["days"])
                    h.close()
            a["result"] = "ok"
        except Exception as e:
            a["result"] = "error: %s" % e
        if a["action"] in ("delete", "archive", "compress"):
            logs.audit(paths.log_dir, "retention-" + a["action"], path=p, age_days=a.get("age_days"),
                       bytes=a.get("bytes"), result=a["result"], destination=a.get("destination", ""),
                       reason=a.get("reason", ""))
        done.append(a)
    return done
