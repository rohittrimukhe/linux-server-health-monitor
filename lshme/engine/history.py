# Linux Server Health Monitor Enterprise
# Copyright (c) 2026 LRS Services Pvt Ltd, Mumbai. All rights reserved.
"""Historical metrics (SQLite), trend comparison, recurring incidents and
capacity forecasting. Timestamps are stored as epoch seconds."""
import os
import sqlite3
import time

from lshme.core.model import ModuleResult
from lshme.core.util import fmt_bytes, fmt_duration, linear_forecast

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (id INTEGER PRIMARY KEY, host TEXT, ts REAL, schedule TEXT, score INTEGER,
    status TEXT, critical INTEGER, warning INTEGER, unknown INTEGER, duration REAL, version TEXT, report_dir TEXT);
CREATE TABLE IF NOT EXISTS metrics (run_id INTEGER, host TEXT, ts REAL, key TEXT, value REAL);
CREATE TABLE IF NOT EXISTS findings (run_id INTEGER, host TEXT, ts REAL, severity TEXT, code TEXT,
    component TEXT, module TEXT, message TEXT);
CREATE TABLE IF NOT EXISTS deliveries (id INTEGER PRIMARY KEY, ts REAL, host TEXT, run_id INTEGER, ok INTEGER,
    recipients TEXT, attempts INTEGER, detail TEXT);
CREATE INDEX IF NOT EXISTS ix_runs_host_ts ON runs(host, ts);
CREATE INDEX IF NOT EXISTS ix_metrics_host_key_ts ON metrics(host, key, ts);
CREATE INDEX IF NOT EXISTS ix_findings_host_ts ON findings(host, ts);
"""

TREND_METRICS = [
    ("cpu.usage_percent", "CPU utilisation %"), ("cpu.load5_per_core", "Load per CPU (5 min)"),
    ("memory.used_percent", "Memory used %"), ("swap.used_percent", "Swap used %"),
    ("fs.max_used_percent", "Fullest filesystem %"), ("services.failed", "Failed services"),
    ("kvm.vms_running", "Running VMs"), ("security.pending_updates", "Pending updates"),
]


class History(object):
    def __init__(self, path):
        self.path = path
        d = os.path.dirname(path)
        if d:
            os.makedirs(d, exist_ok=True)
        self.con = sqlite3.connect(path, timeout=30)
        self.con.executescript(SCHEMA)

    def close(self):
        self.con.close()

    def save(self, run, report_dir=""):
        c = self.con.cursor()
        h = run.health
        counts = h.get("counts", {})
        c.execute("INSERT INTO runs(host, ts, schedule, score, status, critical, warning, unknown, duration, version, report_dir) "
                  "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                  (run.host, run.collected_at, run.schedule, h.get("score"), h.get("status"), counts.get("CRITICAL", 0),
                   counts.get("WARNING", 0), counts.get("UNKNOWN", 0), (run.finished_at or time.time()) - run.collected_at,
                   run.version, report_dir))
        rid = c.lastrowid
        c.executemany("INSERT INTO metrics VALUES(?,?,?,?,?)",
                      [(rid, run.host, run.collected_at, k, v) for k, v in run.metrics().items()])
        c.executemany("INSERT INTO findings VALUES(?,?,?,?,?,?,?,?)",
                      [(rid, run.host, run.collected_at, f.severity, f.code, f.component, f.module, f.message[:500])
                       for f in run.findings("WARNING")])
        self.con.commit()
        return rid

    def set_report_dir(self, run_id, report_dir):
        self.con.execute("UPDATE runs SET report_dir=? WHERE id=?", (report_dir, run_id))
        self.con.commit()

    def record_delivery(self, host, run_id, ok, recipients, attempts, detail):
        self.con.execute("INSERT INTO deliveries(ts, host, run_id, ok, recipients, attempts, detail) VALUES(?,?,?,?,?,?,?)",
                         (time.time(), host, run_id, 1 if ok else 0, ",".join(recipients or []), attempts, str(detail)[:1000]))
        self.con.commit()

    # -- queries --------------------------------------------------------------
    def runs(self, host=None, days=30, limit=500):
        cutoff = time.time() - days * 86400
        q = "SELECT id, host, ts, schedule, score, status, critical, warning, unknown, duration, report_dir FROM runs WHERE ts >= ?"
        args = [cutoff]
        if host:
            q += " AND host = ?"
            args.append(host)
        q += " ORDER BY ts DESC LIMIT ?"
        args.append(limit)
        cols = ("id", "host", "ts", "schedule", "score", "status", "critical", "warning", "unknown", "duration", "report_dir")
        return [dict(zip(cols, r)) for r in self.con.execute(q, args).fetchall()]

    def hosts(self):
        return [r[0] for r in self.con.execute("SELECT DISTINCT host FROM runs ORDER BY host").fetchall()]

    def series(self, host, key, since):
        return self.con.execute("SELECT ts, value FROM metrics WHERE host=? AND key=? AND ts>=? ORDER BY ts",
                                (host, key, since)).fetchall()

    def keys_like(self, host, prefix, since):
        return [r[0] for r in self.con.execute("SELECT DISTINCT key FROM metrics WHERE host=? AND key LIKE ? AND ts>=?",
                                               (host, prefix + "%", since)).fetchall()]

    def value_near(self, host, key, ts, tolerance):
        r = self.con.execute("SELECT value, ABS(ts-?) d FROM metrics WHERE host=? AND key=? AND ts BETWEEN ? AND ? ORDER BY d LIMIT 1",
                             (ts, host, key, ts - tolerance, ts + tolerance)).fetchone()
        return r[0] if r else None

    def recurring(self, host, since, min_count=2):
        return self.con.execute("SELECT code, component, severity, COUNT(DISTINCT run_id) n, MAX(message) FROM findings "
                                "WHERE host=? AND ts>=? GROUP BY code, component, severity HAVING n>=? ORDER BY n DESC LIMIT 30",
                                (host, since, min_count)).fetchall()

    def deliveries(self, days=30, limit=200):
        cutoff = time.time() - days * 86400
        return self.con.execute("SELECT ts, host, run_id, ok, recipients, attempts, detail FROM deliveries WHERE ts>=? ORDER BY ts DESC LIMIT ?",
                                (cutoff, limit)).fetchall()

    def prune(self, days):
        cutoff = time.time() - days * 86400
        c = self.con.cursor()
        ids = [r[0] for r in c.execute("SELECT id FROM runs WHERE ts < ?", (cutoff,)).fetchall()]
        c.execute("DELETE FROM metrics WHERE ts < ?", (cutoff,))
        c.execute("DELETE FROM findings WHERE ts < ?", (cutoff,))
        c.execute("DELETE FROM runs WHERE ts < ?", (cutoff,))
        c.execute("DELETE FROM deliveries WHERE ts < ?", (cutoff,))
        self.con.commit()
        try:
            self.con.execute("VACUUM")
        except sqlite3.Error:
            pass
        return len(ids)


def analyse(hist, run, cfg):
    """Attach trend data to the run and return a 'trends' ModuleResult."""
    now = run.collected_at
    host = run.host
    window = max(run.days, 7)
    res = ModuleResult("trends", "Trends & Capacity Forecast", 5)
    cur = run.metrics()
    rows, trend_data = [], []
    for key, label in TREND_METRICS:
        if key not in cur:
            continue
        series = [v for _, v in hist.series(host, key, now - window * 86400)] + [cur[key]]
        d1 = hist.value_near(host, key, now - 86400, 6 * 3600)
        d7 = hist.value_near(host, key, now - 7 * 86400, 86400)
        d30 = hist.value_near(host, key, now - 30 * 86400, 2 * 86400)
        avg = sum(series) / len(series)
        item = {"key": key, "label": label, "current": cur[key], "day_ago": d1, "week_ago": d7, "month_ago": d30,
                "avg": avg, "min": min(series), "max": max(series), "samples": len(series)}
        trend_data.append(item)
        f = lambda v: "" if v is None else ("%.1f" % v)
        rows.append([label, f(cur[key]), f(d1), f(d7), f(d30), f(avg), f(min(series)), f(max(series)), len(series)])
    res.table("Trend comparison (window %d days)" % window,
              ["Metric", "Now", "1 day ago", "7 days ago", "30 days ago", "Average", "Min", "Max", "Samples"], rows, kind="summary")

    # filesystem exhaustion forecast
    fc_rows, forecasts = [], []
    fw = cfg.threshold("history", "forecast_warning_days", 30)
    fcrit = cfg.threshold("history", "forecast_critical_days", 7)
    min_samples = int(cfg.threshold("history", "forecast_min_samples", 3))
    # samples must cover a real period, or a few runs minutes apart turn noise into "full in 1 day"
    min_span = float(cfg.threshold("history", "forecast_min_span_hours", 24)) * 3600
    enough = lambda pts: len(pts) >= min_samples and pts[-1][0] - pts[0][0] >= min_span
    since = now - 30 * 86400
    for key in [k for k in cur if k.startswith("fs.used_bytes:")]:
        mp = key.split(":", 1)[1]
        size = cur.get("fs.size_bytes:" + mp)
        if not size:
            continue
        pts = [(t, v) for t, v in hist.series(host, key, since)] + [(now, cur[key])]
        if not enough(pts):
            continue
        secs = linear_forecast(pts, size)
        growth = (pts[-1][1] - pts[0][1]) / max(1.0, (pts[-1][0] - pts[0][0]) / 86400.0)
        days_left = secs / 86400.0 if secs is not None else None
        forecasts.append({"mountpoint": mp, "days_to_full": days_left, "growth_per_day": growth, "samples": len(pts)})
        fc_rows.append([mp, fmt_bytes(size), fmt_bytes(cur[key]), fmt_bytes(growth) + "/day",
                        "%.0f days" % days_left if days_left is not None else "not growing", len(pts)])
        if days_left is not None:
            sev = "CRITICAL" if days_left <= fcrit else "WARNING" if days_left <= fw else None
            if sev:
                res.add(sev, "FS_FORECAST_FULL", mp,
                        "At the current growth rate (%s/day) %s will be full in about %s" % (fmt_bytes(growth), mp, fmt_duration(secs)),
                        recommendation="This is an estimate from %d samples; plan capacity before the date." % len(pts))
    if fc_rows:
        res.table("Filesystem capacity forecast (estimate, linear trend over 30 days)",
                  ["Mount point", "Size", "Used now", "Growth", "Estimated time to full", "Samples"], fc_rows, kind="summary")
    for key in [k for k in cur if k.startswith("kvm.pool_used_percent:")]:
        pts = [(t, v) for t, v in hist.series(host, key, since)] + [(now, cur[key])]
        if enough(pts):
            secs = linear_forecast(pts, 100.0)
            if secs is not None and secs / 86400 <= fw:
                res.add("WARNING", "KVM_POOL_FORECAST_FULL", key.split(":", 1)[1],
                        "Storage pool %s forecast to fill in about %s" % (key.split(":", 1)[1], fmt_duration(secs)))

    rec = hist.recurring(host, now - window * 86400)
    if rec:
        res.table("Recurring issues (last %d days)" % window, ["Code", "Component", "Severity", "Runs affected", "Latest message"],
                  [list(r) for r in rec])
        for code, comp, sev, n, msg in rec:
            if n >= 5 and sev == "CRITICAL":
                res.add("WARNING", "RECURRING_CRITICAL", comp, "%s on %s has been critical in %d runs" % (code, comp, n),
                        recommendation="A repeating critical issue needs a root-cause fix, not repeated manual recovery.")
    score_hist = [(r["ts"], r["score"]) for r in reversed(hist.runs(host, window, 200))]
    run.trends = {"metrics": trend_data, "forecast": forecasts, "recurring": [list(r) for r in rec],
                  "score_history": score_hist + [(now, None)]}
    res.summary = "%d trended metrics, %d forecasts, %d recurring issues" % (len(trend_data), len(forecasts), len(rec))
    res.data = {"window_days": window}
    if not trend_data:
        res.status = "SKIPPED"
    return res
