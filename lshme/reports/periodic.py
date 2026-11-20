# Linux Server Health Monitor Enterprise
# Copyright (c) 2026 LRS Services Pvt Ltd, Mumbai. All rights reserved.
"""Daily, weekly and monthly checklist reports.

A short, colour-coded view for routine monitoring: overall status, a checklist,
CPU and memory, filesystem / database / folder usage and growth. The weekly and
monthly reports add growth by date; the monthly one comes as a summary plus a
comprehensive report with every detail.

The result is a format-neutral PDoc (sections of blocks) that periodic_html,
periodic_pdf and periodic_out render.
"""
import json
import os
import time

from lshme import APP_NAME, BRAND_LOGO, COPYRIGHT, VERSION
from lshme.core.util import fmt_bytes, fmt_duration, linear_forecast, redact
from lshme.engine.history import History

TYPES = ("daily", "weekly", "monthly")
WINDOW_DAYS = {"daily": 1, "weekly": 7, "monthly": 30}
TITLES = {"daily": "Daily Server Checklist", "weekly": "Weekly Server Report", "monthly": "Monthly Server Report"}
DAY = 86400.0

IMPACT = {
    "fs": "If a filesystem becomes full, the programs and databases that write to it stop working or fail.",
    "cpu": "When the CPU stays busy, applications and database queries become slow.",
    "mem": "When memory runs short the server slows down and the system may stop programs to free memory.",
    "swap": "Heavy use of swap means memory is short, and everything on the server feels slow.",
    "load": "A high load means work is waiting for a free CPU, so responses get slower.",
    "services": "A failed service means the application or function it provides is not running.",
    "updates": "Security updates that are not installed leave known weaknesses open to attackers.",
    "logs": "Errors in the system log can point to hardware or software faults that may get worse.",
    "backup": "An old backup means recent data could be lost if the server fails.",
    "db": "A database problem can stop every application that depends on that database.",
    "time": "A wrong clock can break logins, certificates, logs and database replication.",
    "reboot": "A restart means the services on this server were unavailable for a short time.",
    "generic": "This is outside the normal range and should be looked at.",
}
MODULE_KIND = {"storage": "fs", "cpu_memory": "cpu", "services": "services", "security": "updates", "logs": "logs", "backup": "backup",
               "database": "db", "system": "time", "folders": "fs"}


def impact_for_module(module):
    return IMPACT[MODULE_KIND.get(module, "generic")]

MB = 1024.0 * 1024.0


class Cell(object):
    """A table cell with a style: ok/wn/cr (filled), up/dn/fl (text colour), chip-ok/chip-wn/chip-cr."""
    __slots__ = ("text", "style")

    def __init__(self, text, style=""):
        self.text, self.style = text, style

    def __str__(self):
        return str(self.text)


class Bar(object):
    """Percentage bar: green below warn, amber below crit, red above."""
    __slots__ = ("pct", "warn", "crit")

    def __init__(self, pct, warn=80.0, crit=90.0):
        self.pct, self.warn, self.crit = pct, warn, crit

    def level(self):
        return "cr" if self.pct >= self.crit else "wn" if self.pct >= self.warn else "ok"

    def __str__(self):
        return "%.0f%%" % self.pct


def text_of(c):
    return str(c) if c is not None else ""


class PDoc(object):
    def __init__(self, ptype, variant="summary"):
        self.ptype = ptype
        self.variant = variant            # summary | comprehensive
        self.title = TITLES[ptype] + (" (comprehensive)" if variant == "comprehensive" else "")
        self.subtitle = ""
        self.host = ""
        self.status = "UNKNOWN"
        self.score = 0
        self.counts = {}
        self.findings = []
        self.banner = ""
        self.meta = []
        self.sections = []                # [(title, [block, ...])]
        self.growth_rows = []             # raw rows for Excel / CSV: [date, kind, name, bytes, change_bytes]
        self.tz_offset = 0
        self.customer = ""
        self.company = ""
        self.details = []
        self.customer_logo = ""
        self.generated = time.time()
        self.copyright = COPYRIGHT
        self.brand_logo = BRAND_LOGO if os.path.isfile(BRAND_LOGO) else ""
        self.report_type = ptype

    def add(self, title, blocks):
        self.sections.append((title, [b for b in blocks if b]))

    def basename_suffix(self):
        return "_comprehensive" if self.variant == "comprehensive" else ""


# -- block constructors ----------------------------------------------------------
def kpis(items):
    return {"type": "kpis", "items": items}


def table(title, headers, rows, align=None, legend="", note=""):
    return {"type": "table", "title": title, "headers": headers, "rows": rows, "align": align or [], "legend": legend, "note": note}


def charts(items):
    return {"type": "charts", "items": items} if items else None


def note(text):
    return {"type": "note", "text": text}


def actions(items):
    return {"type": "actions", "items": items}


def chip(level):
    return Cell({"ok": "OK", "wn": "WARN", "cr": "CRIT", "na": "N/A"}[level], "chip-" + level)


def level_for(value, warn, crit):
    return "cr" if value >= crit else "wn" if value >= warn else "ok"


def worst(levels):
    return "cr" if "cr" in levels else "wn" if "wn" in levels else "ok"


def stamp(ts, fmt="%d %b %H:%M"):
    return time.strftime(fmt, time.localtime(ts))


def day_key(ts):
    return time.strftime("%Y-%m-%d", time.localtime(ts))


# -- data access -------------------------------------------------------------------
class Data(object):
    def __init__(self, hist, host, now, days, run, cfg, paths):
        self.hist, self.host, self.now, self.days, self.run, self.cfg, self.paths = hist, host, now, days, run, cfg, paths
        self.since = now - days * DAY
        self.cur = run.metrics() if run else {}
        self._cache = {}

    def points(self, key, days=None):
        """[(ts, value)] for a metric: history plus the current run if it is not stored yet."""
        k = (key, days)
        if k not in self._cache:
            pts = [(t, v) for t, v in self.hist.series(self.host, key, self.now - (days or self.days) * DAY)] if self.hist else []
            if key in self.cur and (not pts or self.now - pts[-1][0] > 120):
                pts.append((self.now, self.cur[key]))
            self._cache[k] = pts
        return self._cache[k]

    def keys(self, prefix):
        names = set(k for k in self.cur if k.startswith(prefix))
        if self.hist:
            names |= set(self.hist.keys_like(self.host, prefix, self.now - 31 * DAY))
        return sorted(names)

    def baseline(self, pts, back_days):
        """Value about back_days ago: the latest point at or before that moment, else the first point if it is old enough."""
        if not pts:
            return None
        target = self.now - back_days * DAY
        before = [p for p in pts if p[0] <= target + 3600]
        if before:
            return before[-1][1]
        if pts[-1][0] - pts[0][0] >= 3600:
            return pts[0][1]
        return None

    def per_day(self, pts):
        """{date: last value of that day}"""
        out = {}
        for t, v in pts:
            out[day_key(t)] = v
        return out

    def dates(self):
        n = int(round(self.days)) if self.days >= 1 else 1
        return [day_key(self.now - i * DAY) for i in range(n - 1, -1, -1)]

    # CPU / memory series --------------------------------------------------------
    def usage(self):
        """{'cpu': {'avg': pts, 'max': pts}, ...} and the data source description."""
        rows, source = [], "none"
        if self.days <= 1 and self.paths:
            try:
                with open(os.path.join(self.paths.data_dir, "watch_metrics.json"), encoding="utf-8") as f:
                    raw = [p for p in json.load(f) if p.get("t", 0) >= self.since]
                if len(raw) >= 5:
                    out = {}
                    for key in ("cpu", "mem", "swap", "load"):
                        pts = [(p["t"], float(p[key])) for p in raw if p.get(key) is not None]
                        out[key] = {"avg": bucket(pts, 288, "avg"), "max": bucket(pts, 288, "max"), "all": pts}
                    return out, "real-time watcher, one sample per minute"
            except (IOError, OSError, ValueError, KeyError, TypeError):
                pass
        if self.hist:
            rows = self.hist.hourly(self.host, self.since)
        if len(rows) >= 3:
            out = {}
            for key in ("cpu", "mem", "swap", "load"):
                out[key] = {"avg": [(r["ts"] + 1800, r[key]) for r in rows], "max": [(r["ts"] + 1800, r[key + "_max"]) for r in rows],
                            "all": [(r["ts"] + 1800, r[key]) for r in rows]}
                out[key]["avg"] = bucket(out[key]["avg"], 400, "avg")
                out[key]["max"] = bucket(out[key]["max"], 400, "max")
            return out, "real-time watcher, hourly averages and peaks"
        out = {}
        for key, metric in (("cpu", "cpu.usage_percent"), ("mem", "memory.used_percent"), ("swap", "swap.used_percent"), ("load", "cpu.load5_per_core")):
            pts = self.points(metric)
            out[key] = {"avg": pts, "max": pts, "all": pts}
        have = any(out[k]["all"] for k in out)
        return out, ("health-check runs only (install the real-time watcher for continuous graphs)" if have else "no data yet")


def bucket(pts, limit, how):
    if len(pts) <= limit:
        return list(pts)
    size = len(pts) / float(limit)
    out, i = [], 0.0
    while i < len(pts):
        chunk = pts[int(i):max(int(i + size), int(i) + 1)]
        vals = [v for _, v in chunk]
        out.append((chunk[len(chunk) // 2][0], (max(vals) if how == "max" else sum(vals) / len(vals))))
        i += size
    return out


def stats(pts):
    """avg, min, max, time of max, last."""
    if not pts:
        return None
    vals = [v for _, v in pts]
    top = max(pts, key=lambda p: p[1])
    return {"avg": sum(vals) / len(vals), "min": min(vals), "max": top[1], "max_t": top[0], "last": pts[-1][1]}


# -- builders ------------------------------------------------------------------------
def fmt_delta(d, size=None):
    if d is None:
        return Cell("-", "fl")
    if abs(d) < 1024:
        return Cell("0", "fl")
    txt = ("+" if d > 0 else "-") + fmt_bytes(abs(d))
    if d < 0:
        return Cell(txt, "dn")
    big = d > max(50 * MB, 0.002 * (size or 0))
    return Cell(txt, "up" if big else "fl")


def fmt_size(v):
    return fmt_bytes(v) if v is not None else "-"


def growth_item(data, key, size_limit=None):
    """Size now and growth for one series. Returns a dict."""
    pts = data.points(key, 31)
    if not pts:
        return None
    now_v = pts[-1][1]
    out = {"key": key, "now": now_v, "pts": pts}
    for name, back in (("d1", 1), ("d7", 7), ("d30", 30)):
        b = data.baseline(pts, back)
        out[name] = None if b is None else now_v - b
    span = pts[-1][0] - pts[0][0]
    out["per_day"] = (now_v - pts[0][1]) / (span / DAY) if span >= 6 * 3600 else None
    out["pct_week"] = (out["d7"] / (now_v - out["d7"]) * 100.0) if out["d7"] is not None and now_v - out["d7"] > 0 else None
    out["full_days"] = None
    if size_limit and len(pts) >= 3 and span >= DAY:
        secs = linear_forecast(pts, size_limit)
        out["full_days"] = secs / DAY if secs is not None else None
        out["forecast_ok"] = True
    return out


def full_cell(item):
    if item is None or not item.get("forecast_ok"):
        return Cell("n/a", "fl")
    d = item["full_days"]
    if d is None:
        return Cell("not growing", "ok")
    if d > 365:
        return Cell("> 1 year", "ok")
    return Cell("%d days" % max(1, round(d)), "cr" if d <= 7 else "wn" if d <= 30 else "ok")


def filesystems(data):
    run, rows = data.run, []
    fs = []
    st = run.module("storage") if run else None
    if st and st.data.get("filesystems"):
        for f in st.data["filesystems"]:
            if f["fstype"] in ("tmpfs", "devtmpfs", "squashfs", "overlay", "iso9660"):
                continue
            fs.append(f)
    else:
        for k in data.keys("fs.used_bytes:"):
            mp = k.split(":", 1)[1]
            size, used = data.cur.get("fs.size_bytes:" + mp), data.cur.get(k)
            if size and used is not None:
                fs.append({"mountpoint": mp, "size": size, "used": used, "free": size - used, "used_percent": 100.0 * used / size})
    return sorted(fs, key=lambda f: -(f.get("used_percent") or 0))


def mount_for(path, mounts):
    best = "/"
    for m in mounts:
        if (path == m or path.startswith(m.rstrip("/") + "/")) and len(m) > len(best):
            best = m
    return best


def databases(data):
    out = []
    for prefix, suffix, label in (("oracle.", ".datafile_bytes", "Oracle"), ("postgresql.", ".total_bytes", "PostgreSQL"), ("mysql.", ".total_bytes", "MySQL/MariaDB")):
        for k in data.keys(prefix):
            if k.endswith(suffix):
                name = k[len(prefix):-len(suffix)]
                it = growth_item(data, k)
                if it:
                    other = []
                    fra = data.cur.get("oracle.%s.fra_used_percent" % name)
                    if label == "Oracle" and fra is not None:
                        other.append("FRA %.0f%% used" % fra)
                    ts = [v for kk, v in data.cur.items() if kk.startswith("oracle.%s.ts_used_percent:" % name)]
                    if ts:
                        other.append("fullest tablespace %.0f%%" % max(ts))
                    out.append({"name": name, "type": label, "item": it, "other": ", ".join(other)})
    return out


def db_level(data, name):
    mod = data.run.module("database") if data.run else None
    lv = ["ok"]
    for f in (mod.findings if mod else []):
        if name.lower() in (f.component or "").lower() or name.lower() in (f.message or "").lower():
            lv.append("cr" if f.severity == "CRITICAL" else "wn" if f.severity == "WARNING" else "ok")
    return worst(lv)


def folders(data, mounts):
    out = []
    for k in data.keys("folder.bytes:"):
        path = k.split(":", 1)[1]
        it = growth_item(data, k)
        if it and it["now"] >= 5 * MB:  # tiny folders are noise
            out.append({"path": path, "fs": mount_for(path, mounts), "item": it})
    return sorted(out, key=lambda r: -r["item"]["now"])


def trend_cell(item):
    d = item["d7"] if item["d7"] is not None else item["d1"]
    if d is None:
        return Cell("-", "fl")
    if d > max(50 * MB, 0.002 * item["now"]):
        return Cell("growing", "up")
    if d < -50 * MB:
        return Cell("shrinking", "dn")
    return Cell("stable", "fl")


def fs_table(data, fs, win):
    rows = []
    for f in fs:
        mp = f["mountpoint"]
        it = growth_item(data, "fs.used_bytes:" + mp, f["size"])
        pct = f.get("used_percent") or 0
        w, c = data.cfg.threshold("disk", "warning", 80), data.cfg.threshold("disk", "critical", 90)
        lv = level_for(pct, w, c)
        if it and it.get("forecast_ok") and it["full_days"] is not None and it["full_days"] <= 7:
            lv = "cr"
        elif it and it.get("forecast_ok") and it["full_days"] is not None and it["full_days"] <= 30 and lv == "ok":
            lv = "wn"
        row = [Cell(mp, "b"), Bar(pct, w, c), fmt_size(f["used"]), fmt_size(f["free"]), fmt_size(f["size"])]
        if it:
            row += [fmt_delta(it["d1"], f["size"]), fmt_delta(it["d7"], f["size"])]
            if win == "monthly":
                row.append(fmt_delta(it["d30"], f["size"]))
            row += [full_cell(it)]
        else:
            row += [Cell("-", "fl")] * (3 if win == "monthly" else 2) + [Cell("n/a", "fl")]
        row.append(chip(lv))
        rows.append(row)
    heads = ["Mount", "Usage", "Used", "Free", "Size", "Growth 24 h", "Growth 7 d"] + (["Growth 30 d"] if win == "monthly" else []) + ["Full in", "Status"]
    return heads, rows


# -- checklist -----------------------------------------------------------------------
def checklist(data, usage, fs):
    cfg, run = data.cfg, data.run
    th = lambda s, k, d: cfg.threshold(s, k, d)
    rows, acts = [], []

    def add(level, check, value, warn, crit, detail):
        rows.append([chip(level), check, Cell(value, ""), Cell(warn, ""), Cell(crit, ""), detail])

    def peak(key):
        s = stats(usage.get(key, {}).get("max") or [])
        return s
    for key, label, sec, unit in (("cpu", "CPU", "cpu", "%"), ("mem", "Memory", "memory", "%"), ("swap", "Swap", "swap", "%")):
        pk, av = peak(key), stats(usage.get(key, {}).get("avg") or [])
        w, c = th(sec, "warning", 85), th(sec, "critical", 95)
        if pk is None:
            continue
        lv = level_for(pk["max"], w, c)
        add(lv, "%s (peak)" % label, "%.0f %s" % (pk["max"], unit), "%.0f %s" % (w, unit), "%.0f %s" % (c, unit),
            "Average %.0f %s, peak at %s" % (av["avg"], unit, stamp(pk["max_t"])))
    pk = peak("load")
    if pk is not None:
        w, c = th("load", "per_core_warning", 1.5), th("load", "per_core_critical", 3.0)
        add(level_for(pk["max"], w, c), "Load per core (peak)", "%.2f" % pk["max"], "%.1f" % w, "%.1f" % c, "Peak at %s" % stamp(pk["max_t"]))
    if fs:
        top = fs[0]
        w, c = th("disk", "warning", 80), th("disk", "critical", 90)
        lv = level_for(top.get("used_percent") or 0, w, c)
        it = growth_item(data, "fs.used_bytes:" + top["mountpoint"], top["size"])
        extra = ""
        if it and it.get("forecast_ok") and it["full_days"] is not None and it["full_days"] < 365:
            extra = ", full in about %d days" % max(1, round(it["full_days"]))
            if it["full_days"] <= 30 and lv == "ok":
                lv = "wn"
        add(lv, "Fullest filesystem", "%.0f %%" % top["used_percent"], "%.0f %%" % w, "%.0f %%" % c, "%s%s" % (top["mountpoint"], extra))
        if lv != "ok":
            acts.append((lv, "%s is %.0f %% full%s" % (top["mountpoint"], top["used_percent"], extra),
                         IMPACT["fs"]))
    dbs = databases(data)
    if dbs:
        lvs = [db_level(data, d["name"]) for d in dbs]
        bad = [d["name"] for d, l in zip(dbs, lvs) if l != "ok"]
        add(worst(lvs), "Databases", "%d monitored" % len(dbs), "-", "-", ("Attention: " + ", ".join(bad)) if bad else "All databases healthy")
        if bad:
            acts.append((worst(lvs), "Database problem: %s" % ", ".join(bad), IMPACT["db"]))
    ages = [(k, v) for k, v in data.cur.items() if k.startswith("backup.") or k.endswith(".backup_age_hours")]
    ages = [(k, v) for k, v in ages if "age_hours" in k]
    if ages:
        k, v = max(ages, key=lambda kv: kv[1])
        lim = th("backup", "default_max_age_hours", 26)
        lv = level_for(v, lim, lim * 2)
        add(lv, "Last backup (oldest)", "%.0f h ago" % v, "%.0f h" % lim, "%.0f h" % (lim * 2), k.replace("backup.", "").replace("_age_hours", "").replace(".backup_age_hours", ""))
        if lv != "ok":
            acts.append((lv, "The newest backup is %.0f hours old" % v, IMPACT["backup"]))
    if "services.failed" in data.cur:
        n = int(data.cur["services.failed"])
        add("cr" if n else "ok", "Failed services", str(n), "1", "1", "All services running" if not n else "%d unit(s) failed" % n)
        if n:
            acts.append(("cr", "%d service%s failed" % (n, "" if n == 1 else "s"), IMPACT["services"]))
    if "security.pending_updates" in data.cur:
        n, sec = int(data.cur["security.pending_updates"]), int(data.cur.get("security.pending_security_updates", 0))
        lv = "cr" if sec >= 20 else "wn" if sec >= 1 else "ok"
        add(lv, "Pending updates", "%d (%d security)" % (n, sec), "1 security", "20 security", "Patch regularly" if sec else "Up to date")
        if lv != "ok":
            acts.append((lv, "%d security update%s not installed" % (sec, "" if sec == 1 else "s"), IMPACT["updates"]))
    mod = run.module("logs") if run else None
    if mod is not None and mod.status != "SKIPPED":
        bad = [f for f in mod.findings if f.severity in ("CRITICAL", "WARNING")]
        lv = worst(["cr" if f.severity == "CRITICAL" else "wn" for f in bad]) if bad else "ok"
        add(lv, "Log errors", str(len(bad)), "1", "1", bad[0].message[:80] if bad else "No serious log errors")
        if bad:
            acts.append((lv, bad[0].message[:110].rstrip('.'), IMPACT["logs"]))
    sysm = run.module("system") if run else None
    if sysm is not None and sysm.data:
        ts = sysm.data.get("time_sync")
        if ts is not None:
            add("ok" if ts else "wn", "Time sync", "yes" if ts else "NO", "-", "-", sysm.data.get("time_sync_source", ""))
            if not ts:
                acts.append(("wn", "The clock is not synchronised", IMPACT["time"]))
        up = data.cur.get("system.uptime_days")
        if up is not None:
            rebooted = up * DAY < data.days * DAY
            add("wn" if rebooted else "ok", "Reboot / uptime", "up %s" % fmt_duration(up * DAY), "-", "-",
                "Rebooted %s ago" % fmt_duration(up * DAY) if rebooted else "No reboot in this period")
    return rows, acts


def other_findings(data, have):
    """Warnings and criticals from modules the checklist does not already cover (cluster, kvm, certificates ...)."""
    acts = []
    skip = ("logs", "security", "services", "storage", "cpu_memory", "database", "backup", "system")
    for f in (data.run.findings("WARNING") if data.run else []):
        if f.module in skip or len(acts) >= 5:
            continue
        acts.append(("cr" if f.severity == "CRITICAL" else "wn", redact(f.message)[:140], impact_for_module(f.module)))
    return acts


# -- composition ----------------------------------------------------------------------
def overview_rows(run):
    s = run.module("system").data if run and run.module("system") else {}
    return [["Hostname", s.get("fqdn", run.host if run else ""), "IP address", s.get("ip", "")],
            ["OS / kernel", "%s, %s" % (s.get("distribution", ""), s.get("kernel", "")), "Architecture", s.get("architecture", "")],
            ["Uptime", "%s (boot %s)" % (s.get("uptime", ""), s.get("boot_time", "")), "Platform", s.get("virtualization", "")],
            ["Time zone", s.get("timezone", ""), "Reboot needed", {True: "yes", False: "no"}.get(s.get("reboot_required"), "unknown")]]


def chart_defs(data, usage, win):
    defs = (("cpu", "CPU used", "%", "cpu", "warning", 85, "critical", 95, 100, "#0b5ed7"),
            ("mem", "Memory used", "%", "memory", "warning", 85, "critical", 95, 100, "#7b3fe4"),
            ("swap", "Swap used", "%", "swap", "warning", 50, "critical", 80, 100, "#d9730d"),
            ("load", "Load per core", "", "load", "per_core_warning", 1.5, "per_core_critical", 3.0, None, "#0d8a72"))
    out = []
    for key, title, unit, sec, wk, wd, ck, cd, mx, color in defs:
        u = usage.get(key)
        if not u or not u["avg"]:
            continue
        pk = max(v for _, v in u["max"]) if u["max"] else 1
        w, c = data.cfg.threshold(sec, wk, wd), data.cfg.threshold(sec, ck, cd)
        top = mx or max(c * 1.1, pk * 1.1)
        out.append({"title": title, "unit": unit, "color": color, "warn": w, "crit": c, "max": top,
                    "series": [{"name": "Average", "pts": [[int(t), round(v, 2)] for t, v in u["avg"]]},
                               {"name": "Peak", "pts": [[int(t), round(v, 2)] for t, v in u["max"]]}] if u["max"] != u["avg"] else
                              [{"name": "Value", "pts": [[int(t), round(v, 2)] for t, v in u["avg"]]}]})
    return out


def usage_table(usage, win, source, ncores=0):
    rows = []
    for key, label, unit, dec in (("cpu", "CPU used %", "", 0), ("mem", "Memory used %", "", 0), ("swap", "Swap used %", "", 0), ("load", "Load per core", "", 2)):
        u = usage.get(key)
        if not u or not u["all"]:
            continue
        a = stats(u["all"])
        pk = stats(u["max"]) or a
        f = "%%.%df" % dec
        rows.append([label, f % a["last"], f % a["avg"], f % a["min"], Cell(f % pk["max"], "ok"), stamp(pk["max_t"], "%d %b %H:%M"), chip("ok")])
        if key == "load" and ncores > 1:
            rows.append(["Load on all %d cores together" % ncores, "%.2f" % (a["last"] * ncores), "%.2f" % (a["avg"] * ncores), "%.2f" % (a["min"] * ncores),
                         Cell("%.2f" % (pk["max"] * ncores), "ok"), stamp(pk["max_t"], "%d %b %H:%M"), chip("ok")])
    return table("CPU and memory, %s" % {"daily": "last 24 hours", "weekly": "last 7 days", "monthly": "last 30 days"}[win],
                 ["Metric", "Now", "Average", "Minimum", "Peak", "Peak time", "Status"], rows,
                 legend="Data source: %s." % source)


def usage_levels(data, rows_usage):
    """Recolour the peak cells and status chips by the configured thresholds."""
    pass


def day_usage_table(data, usage, title="CPU and memory by day"):
    """One row per date from the available samples."""
    per = {}
    for key in ("cpu", "mem", "swap", "load"):
        u = usage.get(key)
        if not u:
            continue
        for t, v in u["all"]:
            d = per.setdefault(day_key(t), {}).setdefault(key, {"sum": 0.0, "n": 0, "max": 0.0})
            d["sum"] += v
            d["n"] += 1
        for t, v in (u["max"] or []):
            d = per.setdefault(day_key(t), {}).setdefault(key, {"sum": 0.0, "n": 0, "max": 0.0})
            d["max"] = max(d["max"], v)
    th = data.cfg.threshold
    lim = {"cpu": (th("cpu", "warning", 85), th("cpu", "critical", 95)), "mem": (th("memory", "warning", 85), th("memory", "critical", 95)),
           "swap": (th("swap", "warning", 50), th("swap", "critical", 80)), "load": (th("load", "per_core_warning", 1.5), th("load", "per_core_critical", 3.0))}
    rows = []
    for d in data.dates():
        if d not in per:
            rows.append([Cell(d, "b")] + [Cell("-", "fl")] * 8)
            continue
        row = [Cell(d, "b")]
        for key in ("cpu", "mem", "swap", "load"):
            x = per[d].get(key)
            if not x or not x["n"]:
                row += [Cell("-", "fl")] * 2
                continue
            avg, mx = x["sum"] / x["n"], max(x["max"], x["sum"] / x["n"])
            f = "%.2f" if key == "load" else "%.0f"
            row += [f % avg, Cell(f % mx, level_for(mx, *lim[key]))]
        rows.append(row)
    return table(title, ["Date", "CPU avg %", "CPU peak %", "Memory avg %", "Memory peak %", "Swap avg %", "Swap peak %", "Load avg", "Load peak"],
                 rows, legend="Peak cells turn amber at the warning limit and red at the critical limit.")


def health_by_day(data):
    runs = data.hist.runs(data.host, data.days + 1, 1000) if data.hist else []
    per = {}
    for r in reversed(runs):
        per[day_key(r["ts"])] = r
    rows = []
    for d in data.dates():
        r = per.get(d)
        if not r:
            rows.append([Cell(d, "b"), Cell("no run", "fl"), "-", "-", "-", Cell("-", "fl")])
            continue
        lv = "cr" if r["critical"] else "wn" if r["warning"] else "ok"
        rows.append([Cell(d, "b"), chip(lv), "%s / 100" % r["score"], r["critical"], r["warning"], r["schedule"] or ""])
    return table("Health by day (last run of each day)", ["Date", "Status", "Score", "Critical", "Warning", "Run"], rows)


def recurring_table(data):
    rows = []
    if data.hist:
        for code, comp, sev, n, msg in data.hist.recurring(data.host, data.since, 2):
            rows.append([Cell(sev, "chip-cr" if sev == "CRITICAL" else "chip-wn"), code, comp, n, (msg or "")[:100]])
    if not rows:
        return None
    return table("Issues that repeated in this period", ["Severity", "Code", "Component", "Runs", "Latest message"], rows[:25])


def growth_matrix(data, kind, items, label, dates, doc, chunk=4):
    """Day-by-day size and change tables, a few items per table so every cell stays readable."""
    blocks = []
    per = [(name, data.per_day(it["pts"])) for name, it in items]
    for name, pd in per:
        prev = None
        for d in dates:
            v = pd.get(d)
            if v is not None:
                doc.growth_rows.append([d, kind, name, int(v), int(v - prev) if prev is not None else ""])
                prev = v
    for i in range(0, len(per), chunk):
        part = per[i:i + chunk]
        heads = ["Date"] + [n for n, _ in part]
        rows, prev = [], dict((n, None) for n, _ in part)
        for d in dates:
            row = [Cell(d, "b")]
            for n, pd in part:
                v = pd.get(d)
                if v is None:
                    row.append(Cell("-", "fl"))
                    continue
                delta = None if prev[n] is None else v - prev[n]
                prev[n] = v
                c = fmt_delta(delta, v)
                row.append(Cell("%s (%s)" % (fmt_bytes(v), c.text if delta is not None else "first"), c.style if delta is not None else "fl"))
            rows.append(row)
        blocks.append(table("%s by date (size at end of day, change vs previous day)%s" % (label, "" if len(per) <= chunk else " - part %d" % (i // chunk + 1)),
                            heads, rows, legend="Red = grew, green = shrank, grey = no change. '-' = no sample that day."))
    return blocks


def growth_charts(items, title_prefix, since=0):
    out = []
    for name, it in items:
        pts = [p for p in it["pts"] if p[0] >= since - 3600]
        if len(pts) < 2:
            continue
        gb = 1024.0 ** 3
        scale, unit = (gb, "GB") if max(v for _, v in pts) >= gb else (MB, "MB")
        out.append({"title": "%s %s" % (title_prefix, name), "unit": " " + unit, "color": "#0b5ed7", "warn": None, "crit": None, "max": None, "min": None,
                    "series": [{"name": "Size", "pts": [[int(t), round(v / scale, 2)] for t, v in pts]}]})
    return out


def build(run, cfg, paths, ptype, variant="summary", now=None):
    """Create one PDoc from the latest run, the history database and the watcher's metrics."""
    now = now or (run.collected_at if run else time.time())
    days = WINDOW_DAYS[ptype]
    hist = None
    try:
        hist = History(paths.history_db)
    except Exception:
        hist = None
    try:
        return _build(run, cfg, paths, ptype, variant, now, days, hist)
    finally:
        if hist:
            hist.close()


def _build(run, cfg, paths, ptype, variant, now, days, hist):
    data = Data(hist, run.host, now, days, run, cfg, paths)
    d = PDoc(ptype, variant)
    d.host = run.host
    d.tz_offset = time.localtime(now).tm_gmtoff if hasattr(time.localtime(now), "tm_gmtoff") else 0
    d.report_type = ptype
    meta = run.meta or {}
    custom = cfg.get("reports", "reports", "title", "") if cfg else ""
    d.title = (custom + " - " if custom else "") + d.title
    start = stamp(now - days * DAY, "%d %b %H:%M") if days <= 1 else stamp(now - days * DAY, "%d %b %Y")
    d.subtitle = "%s - %s - period %s to %s (%s)" % (
        run.fqdn or run.host, (run.module("system").data.get("distribution", "") if run.module("system") else ""), start,
        stamp(now, "%d %b %Y %H:%M" if days <= 1 else "%d %b %Y"), "last 24 hours" if days <= 1 else "last %d days" % days)
    d.meta = [(k, v) for k, v in (("Company", meta.get("company")), ("Customer", meta.get("customer")), ("Server", run.fqdn or run.host),
                                    ("IP address", run.ip), ("Environment", meta.get("environment")), ("Report", d.title),
                                    ("Generated by", "%s v%s" % (APP_NAME, VERSION))) if v]
    d.customer, d.company = meta.get("customer", ""), meta.get("company", "")
    d.customer_logo = (cfg.get("main", "general", "logo", "") if cfg else "") or meta.get("logo", "")
    d.details = [(k, v) for k, v in (("Customer", meta.get("customer")), ("Company", meta.get("company")), ("Server", run.fqdn or run.host),
                                       ("IP address", run.ip), ("Environment", meta.get("environment")), ("Location", meta.get("location")),
                                       ("Period", "%s to %s" % (start, stamp(now, "%d %b %H:%M" if days <= 1 else "%d %b %Y"))),
                                       ("Prepared by", meta.get("prepared_by")), ("Generated", stamp(time.time(), "%d %b %Y %H:%M"))) if v]
    d.counts = run.health.get("counts", run.counts())
    d.score = run.health.get("score", 0)
    for f in run.findings("INFO"):
        fd = f.to_dict()
        fd["message"] = redact(fd["message"])
        fd["evidence"] = ""
        d.findings.append(fd)
    if run.maintenance:
        d.banner = "MAINTENANCE WINDOW ACTIVE - alerts suppressed"

    usage, source = data.usage()
    fs = filesystems(data)
    mounts = [f["mountpoint"] for f in fs]
    rows, acts = checklist(data, usage, fs)
    levels = [("cr" if c[0].style.endswith("cr") else "wn" if c[0].style.endswith("wn") else "ok") for c in rows]
    passed = sum(1 for l in levels if l == "ok")
    d.status = "CRITICAL" if "cr" in levels else "WARNING" if "wn" in levels else "HEALTHY"
    tone = {"CRITICAL": "cr", "WARNING": "wn", "HEALTHY": "ok"}[d.status]
    attention = len(levels) - passed
    acts += other_findings(data, rows)
    uptime = data.cur.get("system.uptime_days")
    items = [("%s / 100" % d.score, "Health score", tone if d.score < 90 else "ok"),
             (str(sum(1 for l in levels if l == "cr")), "Critical", "cr" if "cr" in levels else "ok"),
             (str(sum(1 for l in levels if l == "wn")), "Warnings", "wn" if "wn" in levels else "ok"),
             ("%d of %d" % (passed, len(levels)), "Checks passed", "ok" if not attention else tone)]
    if uptime is not None:
        items.append(("%s" % fmt_duration(uptime * DAY).split(" ")[0] if uptime >= 1 else fmt_duration(uptime * DAY), "Uptime", "ok"))
    from lshme.reports import periodic_extra as X
    cores = X.core_usage(data)
    ncores = int(data.cur.get("cpu.cores") or (len(cores) if cores else 0))
    fs_pre = fs
    dbs_pre = databases(data)
    fo_pre = folders(data, mounts)
    d.add("In short", [X.narrative(data, usage, fs_pre, dbs_pre, fo_pre, levels, run.fqdn or run.host)])
    d.add("Overall status", [kpis(items)])
    d.add("Daily checklist" if ptype == "daily" else "Checklist for the period",
          [table("", ["Status", "Check", "Value", "Warn at", "Critical at", "Detail"], rows, align=["", "", "r", "r", "r", ""],
                 legend="OK = within limits. WARN = needs attention soon. CRIT = act now.")])

    win = ptype
    charts_blocks = [usage_table(usage, win, source, ncores), charts(chart_defs(data, usage, win))]
    d.add("CPU and memory", charts_blocks + X.cores_blocks(data, cores) + ([day_usage_table(data, usage)] if ptype != "daily" else []))

    heads, frows = fs_table(data, fs, win)
    blocks = [table("Filesystem usage and growth", heads, frows, align=["", "", "r", "r", "r", "r", "r", "r", "r", ""] if win == "monthly" else ["", "", "r", "r", "r", "r", "r", "r", ""],
                    legend="Bar: green under the warning limit, amber to the critical limit, red above. Growth in red = space used increased.")]
    fs_items = [(f["mountpoint"], growth_item(data, "fs.used_bytes:" + f["mountpoint"], f["size"])) for f in fs]
    fs_items = [(n, it) for n, it in fs_items if it]
    if ptype != "daily":
        blocks += [charts(growth_charts(fs_items[:6], "Used space on", data.since))]
    d.add("Filesystem usage and growth", blocks)

    dbs = databases(data)
    if dbs:
        rows = []
        for x in dbs:
            it = x["item"]
            rows.append([Cell(x["name"], "b"), x["type"], fmt_size(it["now"]), fmt_delta(it["d1"], it["now"]), fmt_delta(it["d7"], it["now"])] +
                        ([fmt_delta(it["d30"], it["now"])] if ptype == "monthly" else []) +
                        [Cell("%.1f %%" % it["pct_week"], "ok" if it["pct_week"] < 5 else "wn") if it["pct_week"] is not None else Cell("-", "fl"),
                         x["other"], chip(db_level(data, x["name"]))])
        heads = ["Database", "Type", "Size", "Growth 24 h", "Growth 7 d"] + (["Growth 30 d"] if ptype == "monthly" else []) + ["% per week", "Other", "Status"]
        blocks = [table("Database size and growth", heads, rows, align=["", "", "r", "r", "r"] + (["r"] if ptype == "monthly" else []) + ["r", "", ""],
                        legend="Sizes are datafile or total database size; Oracle uses datafiles, PostgreSQL and MySQL the sum of all databases.")]
        if ptype != "daily":
            blocks += [charts(growth_charts([(x["name"], x["item"]) for x in dbs][:6], "Size of", data.since))]
        d.add("Database size and growth", blocks)
    else:
        d.add("Database size and growth", [note("No database sizes recorded yet. They are measured by the health check for Oracle, PostgreSQL and MySQL/MariaDB.")])

    fo = folders(data, mounts)
    if fo:
        rows = []
        for x in fo[:(15 if variant == "summary" and ptype != "monthly" else 40)]:
            it = x["item"]
            rows.append([Cell(x["path"], "b"), x["fs"], fmt_size(it["now"]), fmt_delta(it["d1"], it["now"]), fmt_delta(it["d7"], it["now"])] +
                        ([fmt_delta(it["d30"], it["now"])] if ptype == "monthly" else []) + [trend_cell(it)])
        heads = ["Folder", "On filesystem", "Size", "Growth 24 h", "Growth 7 d"] + (["Growth 30 d"] if ptype == "monthly" else []) + ["Trend"]
        d.add("Top folders and growth", [table("Largest folders", heads, rows, align=["", "", "r", "r", "r"] + (["r"] if ptype == "monthly" else []) + [""],
                                               legend="Folders are measured once a day by the health check ([folders] in main.conf).")])
    else:
        d.add("Top folders and growth", [note("No folder sizes recorded yet. The next health check measures the folders listed under [folders] in main.conf.")])

    if ptype != "daily":
        dates = data.dates()
        pieces = []
        if variant == "comprehensive" or ptype == "weekly":
            pieces += growth_matrix(data, "filesystem", fs_items, "Filesystem usage", dates, d, 4)
            pieces += growth_matrix(data, "database", [(x["name"], x["item"]) for x in dbs], "Database size", dates, d, 4)
            pieces += growth_matrix(data, "folder", [(x["path"], x["item"]) for x in fo[:(12 if ptype == "weekly" else 30)]], "Folder size", dates, d, 4)
        if pieces:
            d.add("Data growth by date", pieces)
        elif ptype == "monthly":
            d.add("Data growth by date", [note("The comprehensive monthly report lists the growth of every filesystem, database and folder for each date of the month.")])
        d.add("Health by day", [health_by_day(data), recurring_table(data)])

    if variant == "comprehensive":
        d.add("Module health", [module_table(run)])
        d.add("All findings (latest run)", [findings_table(run)])
        d.add("Run history", [run_table(data)])
        if hist:
            d.add("Findings during the period", [period_findings(data)])

    d.add("Server overview", [{"type": "kv", "rows": overview_rows(run)}])
    if acts:
        d.add("What needs attention", [actions(acts[:10])])
    else:
        d.add("What needs attention", [note("Nothing needs attention. Every check is within its limits.")])
    if variant == "comprehensive":
        X.inventory_sections(d, cfg)
    return d


def module_table(run):
    rows = []
    for ms in run.health.get("modules", []):
        lv = "na" if ms["score"] is None else ("cr" if ms["critical"] else "wn" if ms["warning"] else "ok")
        m = run.module(ms["module"])
        rows.append([ms["title"], chip(lv), "" if ms["score"] is None else ms["score"], ms["critical"], ms["warning"], (m.summary if m else "")[:110]])
    return table("Module health", ["Module", "Status", "Score", "Critical", "Warning", "Summary"], rows)


def findings_table(run):
    rows = []
    for f in run.findings("WARNING"):
        rows.append([Cell(f.severity, "chip-cr" if f.severity == "CRITICAL" else "chip-wn"), f.module, f.component, redact(f.message)[:140],
                     (f.recommendation or "")[:160]])
    if not rows:
        rows = [[Cell("OK", "chip-ok"), "", "", "No warning or critical findings", ""]]
    return table("Warning and critical findings", ["Severity", "Module", "Component", "Finding", "Recommended action"], rows)


def run_table(data):
    rows = []
    for r in (data.hist.runs(data.host, data.days + 1, 400) if data.hist else []):
        lv = "cr" if r["critical"] else "wn" if r["warning"] else "ok"
        rows.append([stamp(r["ts"], "%Y-%m-%d %H:%M"), r["schedule"] or "", chip(lv), r["score"], r["critical"], r["warning"], "%.0f s" % (r["duration"] or 0)])
    return table("Health-check runs", ["Time", "Schedule", "Status", "Score", "Critical", "Warning", "Duration"], rows)


def period_findings(data):
    q = ("SELECT severity, code, component, COUNT(DISTINCT run_id), MIN(ts), MAX(ts), MAX(message) FROM findings "
         "WHERE host=? AND ts>=? GROUP BY severity, code, component ORDER BY severity, 4 DESC LIMIT 200")
    rows = []
    for sev, code, comp, n, first, last, msg in data.hist.con.execute(q, (data.host, data.since)).fetchall():
        rows.append([Cell(sev, "chip-cr" if sev == "CRITICAL" else "chip-wn"), code, comp, n, stamp(first, "%d %b %H:%M"), stamp(last, "%d %b %H:%M"), (msg or "")[:90]])
    if not rows:
        rows = [[Cell("OK", "chip-ok"), "", "", "", "", "", "No warnings or criticals in this period"]]
    return table("Warnings and criticals seen in the period", ["Severity", "Code", "Component", "Runs", "First seen", "Last seen", "Message"], rows)


def build_all(run, cfg, paths, ptype):
    """The documents for one report type: monthly gives a summary and a comprehensive report."""
    docs = [build(run, cfg, paths, ptype, "summary")]
    if ptype == "monthly":
        docs.append(build(run, cfg, paths, ptype, "comprehensive"))
    return docs
