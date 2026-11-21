# Linux Server Health Monitor Enterprise
# Copyright (c) 2026 LRS Services Pvt Ltd, Mumbai. All rights reserved.
"""Moving data between the raw day files and the rest of LRS Vitals.

backfill: copy everything recorded before the raw store existed (health-check results in saved reports, the history
          database, hourly figures, the live watcher's last 24 hours, incidents, e-mail and audit logs) into the raw day files,
          so that the past is in the same place as the present. Runs once per source; never duplicates live records.
replay:   rebuild the history database from the raw files (health checks and hourly CPU, memory, swap, load, per-core and
          network figures) for any period it is missing, so new or changed reports can show past days."""
import glob
import json
import os
import time

from lshme.ops import rawstore


def _earliest(base, rtype):
    """Time of the first live (not back-filled) record of a type, or now."""
    first = time.time()
    for e in sorted(rawstore.days(base), key=lambda e: e["day"]):
        for p in e["files"]:
            for r in rawstore.iter_file(p):
                if r.get("type") == rtype and not (r.get("data") or {}).get("_backfill"):
                    return min(first, r.get("ts", first))
    return first


def _state(base):
    p = os.path.join(base, "backfill.json")
    try:
        with open(p, encoding="utf-8") as f:
            return p, json.load(f)
    except (OSError, ValueError):
        return p, {"done": {}}


def backfill(app, force=False):
    """Returns {source: records written}."""
    base = rawstore.root()
    if not base:
        raise ValueError("the raw data store is switched off ([retention] raw_enabled)")
    os.makedirs(base, exist_ok=True)
    spath, st = _state(base)
    host = rawstore.host_name()
    out = {}
    from lshme.core.util import redact_obj

    def run_once(name, fn):
        if st["done"].get(name) and not force:
            out[name] = "already done"
            return
        recs = fn()
        n = rawstore.write_many(recs, host, base) if recs else 0
        st["done"][name] = time.strftime("%Y-%m-%d %H:%M:%S")
        out[name] = n

    def full_runs():
        cut = _earliest(base, "run")
        recs, seen = [], set()
        for p in glob.glob(os.path.join(app.paths.report_dir, "*", "*", "*", "*.json*")):
            if "_fleet" in p or os.path.basename(p).startswith("."):
                continue
            try:
                from lshme.ops import archive
                d = json.loads(archive.read_text(p))
            except Exception:
                continue
            if not isinstance(d, dict) or "modules" not in d or "collected_at" not in d:
                continue
            ts = float(d["collected_at"])
            if ts >= cut or round(ts) in seen:
                continue
            seen.add(round(ts))
            d = redact_obj(d)
            d["_backfill"], d["_schedule"], d["_full"] = "report " + os.path.relpath(p, app.paths.report_dir), d.get("schedule"), True
            recs.append(("run", d, ts))
        st["run_times"] = sorted(seen)
        return recs

    def history_runs():
        from lshme.engine.history import History
        cut = _earliest(base, "run")
        have = set(st.get("run_times", []))
        h = History(app.paths.history_db)
        recs = []
        try:
            for rid, h_host, ts, sched, score, status, c, w, u, dur, ver in h.con.execute(
                    "SELECT id, host, ts, schedule, score, status, critical, warning, unknown, duration, version FROM runs ORDER BY ts").fetchall():
                if ts >= cut or round(ts) in have or h_host != os.uname()[1]:
                    continue
                metrics = dict(h.con.execute("SELECT key, value FROM metrics WHERE run_id=?", (rid,)).fetchall())
                finds = [dict(zip(("severity", "code", "component", "module", "message"), r)) for r in
                         h.con.execute("SELECT severity, code, component, module, message FROM findings WHERE run_id=?", (rid,)).fetchall()]
                recs.append(("run", {"_backfill": "history database", "host": h_host, "collected_at": ts, "schedule": sched,
                                     "health": {"score": score, "status": status, "counts": {"CRITICAL": c, "WARNING": w, "UNKNOWN": u}},
                                     "duration": dur, "version": ver, "metrics": metrics, "findings": finds}, ts))
        finally:
            h.close()
        return recs

    def hourly():
        from lshme.engine.history import History
        cut = _earliest(base, "sample")
        me = os.uname()[1]
        h = History(app.paths.history_db)
        recs = []
        try:
            cores, nets = {}, {}
            for ts, core, n, s, mx in h.con.execute("SELECT ts, core, n, sum, max FROM hourly_core WHERE host=?", (me,)).fetchall():
                cores.setdefault(ts, {})[core] = {"avg": s / float(n or 1), "max": mx, "n": n}
            for ts, iface, n, rs, rm, ts_, tm in h.con.execute("SELECT ts, iface, n, rx_sum, rx_max, tx_sum, tx_max FROM hourly_net WHERE host=?", (me,)).fetchall():
                nets.setdefault(ts, {})[iface] = {"rx_avg": rs / float(n or 1), "rx_max": rm, "tx_avg": ts_ / float(n or 1), "tx_max": tm, "n": n}
            for r in h.con.execute("SELECT ts, n, cpu_sum, cpu_max, mem_sum, mem_max, swap_sum, swap_max, load_sum, load_max FROM hourly "
                                   "WHERE host=? ORDER BY ts", (me,)).fetchall():
                if r[0] + 3600 > cut:
                    continue
                n = float(r[1] or 1)
                recs.append(("hourly", {"_backfill": "history database (hourly averages; minute samples were not kept then)", "hour": r[0], "n": r[1],
                                        "cpu": {"avg": r[2] / n, "max": r[3]}, "mem_pct": {"avg": r[4] / n, "max": r[5]},
                                        "swap_pct": {"avg": r[6] / n, "max": r[7]}, "load_per_cpu": {"avg": r[8] / n, "max": r[9]},
                                        "cores": cores.get(r[0], {}), "net": nets.get(r[0], {})}, r[0]))
        finally:
            h.close()
        return recs

    def minute_points():
        cut = _earliest(base, "sample")
        try:
            with open(os.path.join(app.paths.data_dir, "watch_metrics.json"), encoding="utf-8") as f:
                pts = json.load(f)
        except (OSError, ValueError):
            return []
        return [("sample", {"_backfill": "live watcher last 24 h (fewer fields than live raw samples)", "cpu": p.get("cpu"), "cores": p.get("cores"),
                            "mem_pct": p.get("mem"), "swap_pct": p.get("swap"), "load_per_cpu": p.get("load"),
                            "net": dict((k, {"rx_bps": v[0], "tx_bps": v[1]}) for k, v in (p.get("net") or {}).items()),
                            "filesystems": dict((k, {"used_pct": v}) for k, v in (p.get("disk") or {}).items()),
                            "open_critical": p.get("crit"), "open_warning": p.get("warn")}, p["t"]) for p in pts if p.get("t", 0) < cut]

    def jsonl(path, rtype, ts_of):
        cut = _earliest(base, rtype)
        recs = []
        try:
            with open(path, encoding="utf-8") as f:
                for line in f:
                    try:
                        e = json.loads(line)
                        ts = ts_of(e)
                    except (ValueError, TypeError, KeyError):
                        continue
                    if ts and ts < cut:
                        e["_backfill"] = os.path.basename(path)
                        recs.append((rtype, e, ts))
        except OSError:
            pass
        return recs

    stamp = lambda e: time.mktime(time.strptime(e["time"], "%Y-%m-%d %H:%M:%S"))
    run_once("reports", full_runs)
    run_once("history-runs", history_runs)
    run_once("hourly", hourly)
    run_once("watch-24h", minute_points)
    run_once("incidents", lambda: jsonl(os.path.join(app.paths.data_dir, "watch_events.jsonl"), "event", lambda e: e["ts"]))
    run_once("email-log", lambda: jsonl(os.path.join(app.paths.log_dir, "email.log"), "email", stamp))
    run_once("audit-log", lambda: jsonl(os.path.join(app.paths.log_dir, "audit.log"), "audit", stamp))
    with open(spath + ".tmp", "w", encoding="utf-8") as f:
        json.dump(st, f, indent=1)
    os.replace(spath + ".tmp", spath)
    rawstore.seal_due(force=True, base=base)
    return out


def replay(app, start=None, end=None, host=None):
    """Refill the history database from the raw files for days in [start, end] (YYYY-MM-DD). Returns counts."""
    from lshme.core.model import RunResult
    from lshme.engine.history import History
    host = host or rawstore.host_name()
    me = os.uname()[1]
    out = {"days": 0, "runs": 0, "hours": 0}
    h = History(app.paths.history_db)
    try:
        have_runs = set(round(r[0]) for r in h.con.execute("SELECT ts FROM runs WHERE host=?", (me,)).fetchall())
        have_hours = set(r[0] for r in h.con.execute("SELECT ts FROM hourly WHERE host=?", (me,)).fetchall())
        for e in sorted(rawstore.days(host=host), key=lambda e: e["day"]):
            if (start and e["day"] < start) or (end and e["day"] > end):
                continue
            out["days"] += 1
            hours = {}
            for r in rawstore.read(host, e["day"], ("run", "sample", "hourly")):
                d = r.get("data") or {}
                if r["type"] == "run" and "modules" in d and round(r["ts"]) not in have_runs:
                    try:
                        run = RunResult.from_dict(d)
                        if not run.finished_at:
                            run.finished_at = run.collected_at
                        h.save(run)
                        have_runs.add(round(r["ts"]))
                        out["runs"] += 1
                    except Exception:
                        continue
                elif r["type"] == "sample":
                    hr = History.hour_start(r["ts"])
                    if hr not in have_hours:
                        hours.setdefault(hr, []).append(d)
                elif r["type"] == "hourly" and d.get("hour") not in have_hours:
                    hr = int(d["hour"])
                    n = int(d.get("n") or 1)
                    h.con.execute("INSERT OR IGNORE INTO hourly VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                                  (me, hr, n, d["cpu"]["avg"] * n, d["cpu"]["max"], d["mem_pct"]["avg"] * n, d["mem_pct"]["max"],
                                   d["swap_pct"]["avg"] * n, d["swap_pct"]["max"], d["load_per_cpu"]["avg"] * n, d["load_per_cpu"]["max"]))
                    for core, c in (d.get("cores") or {}).items():
                        h.con.execute("INSERT OR IGNORE INTO hourly_core VALUES(?,?,?,?,?,?)", (me, hr, int(core), c["n"], c["avg"] * c["n"], c["max"]))
                    for iface, x in (d.get("net") or {}).items():
                        h.con.execute("INSERT OR IGNORE INTO hourly_net VALUES(?,?,?,?,?,?,?,?)",
                                      (me, hr, iface, x["n"], x["rx_avg"] * x["n"], x["rx_max"], x["tx_avg"] * x["n"], x["tx_max"]))
                    have_hours.add(hr)
                    out["hours"] += 1
            for hr, samples in sorted(hours.items()):
                for s in samples:
                    net = dict((k, (v.get("rx_bps", 0), v.get("tx_bps", 0))) for k, v in (s.get("net") or {}).items())
                    h.add_hourly(me, hr + 60, s.get("cpu"), s.get("mem_pct"), s.get("swap_pct"), s.get("load_per_cpu"), s.get("cores"), net)
                have_hours.add(hr)
                out["hours"] += 1
        h.con.commit()
    finally:
        h.close()
    return out
