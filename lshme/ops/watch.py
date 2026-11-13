# Linux Server Health Monitor Enterprise
# Copyright (c) 2026 LRS Services Pvt Ltd, Mumbai. All rights reserved.
"""Real-time (continuous) monitoring with alerts.

Designed to be cheap enough to run every minute on production servers:

* Fast probes every `interval` seconds read /proc and statvfs directly (no
  subprocesses), except one `systemctl is-active` call for critical services.
* Heavier modules (database, kvm, services, ...) run sequentially: at most one
  per tick, each repeated every `rotation_minutes`.
* An alert is sent only after `confirm` consecutive bad samples (no flapping),
  on escalation, as a reminder every `repeat_minutes`, and once on recovery.
* The process runs at the lowest CPU/IO priority, keeps no growing state, and
  exits if its own memory grows past `max_rss_mb` (systemd restarts it).
"""
import json
import os
import signal
import threading
import time

from lshme import APP_NAME, COPYRIGHT
from lshme.core import logs
from lshme.core.config import as_bool, as_list
from lshme.core.model import ModuleResult, SEV_RANK
from lshme.core.util import NETWORK_FS, PSEUDO_FS, listening_ports_from_proc, owned_by, to_int

DEFAULTS = {"interval": "60", "confirm": "2", "repeat_minutes": "60", "recovery_alerts": "yes",
            "rotation_modules": "services,database,kvm,cockpit,network,backup,cluster,certificates",
            "rotation_minutes": "15", "alert_min_severity": "WARNING", "alert_email": "yes", "alert_to": "",
            "syslog": "yes", "webhook_url": "", "max_rss_mb": "100", "netfs_timeout": "5", "metrics_hours": "24"}


def setting(cfg, key):
    return cfg.get("main", "watch", key, DEFAULTS[key])


# --- fast probes ------------------------------------------------------------------
def _read(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read()
    except (IOError, OSError):
        return ""


def cpu_sample():
    parts = (_read("/proc/stat").split("\n", 1)[0].split() or ["cpu"])[1:]
    try:
        return [int(x) for x in parts[:8]]
    except ValueError:
        return []


def cpu_percent(prev, cur):
    if not prev or not cur or len(prev) != len(cur):
        return None
    d = [b - a for a, b in zip(prev, cur)]
    total = float(sum(d))
    if total <= 0:
        return None
    idle = d[3] + (d[4] if len(d) > 4 else 0)
    return round(100.0 * (total - idle) / total, 1)


def meminfo():
    m = {}
    for line in _read("/proc/meminfo").splitlines():
        k, _, v = line.partition(":")
        if v.split():
            m[k] = int(v.split()[0])
    total = m.get("MemTotal", 0)
    avail = m.get("MemAvailable", m.get("MemFree", 0))
    st, sf = m.get("SwapTotal", 0), m.get("SwapFree", 0)
    return (100.0 * (total - avail) / total if total else None,
            100.0 * (st - sf) / st if st else None)


class NetfsProber(object):
    """statvfs on network mounts in a helper thread so a hung NFS never blocks the watcher."""

    def __init__(self, timeout):
        self.timeout = timeout
        self.pending = {}

    def probe(self, mount):
        t = self.pending.get(mount)
        if t is not None and t.is_alive():
            return None, "still blocked from a previous check"
        box = {}

        def work():
            try:
                box["st"] = os.statvfs(mount)
            except OSError as e:
                box["err"] = str(e)
        t = threading.Thread(target=work, name="netfs-probe")
        t.daemon = True
        t.start()
        t.join(self.timeout)
        if t.is_alive():
            self.pending[mount] = t
            return None, "no response within %ss" % self.timeout
        self.pending.pop(mount, None)
        return box.get("st"), box.get("err", "")


def fast_probes(cfg, state, prober):
    """Return {key: (severity, component, message, recommendation)} for current problems."""
    t = cfg.threshold
    out = {}

    def check(key, value, warn, crit, component, text, rec=""):
        if value is None:
            return
        if value >= crit:
            out[key] = ("CRITICAL", component, text % value, rec)
        elif value >= warn:
            out[key] = ("WARNING", component, text % value, rec)

    cur = cpu_sample()
    cpu = cpu_percent(state.get("cpu_prev"), cur)
    state["cpu_prev"] = cur
    sample = state["sample"] = {"cpu": cpu, "disk": {}}
    check("cpu", cpu, t("cpu", "warning", 85), t("cpu", "critical", 95), "cpu", "CPU utilisation %.0f%%",
          "Check top CPU consumers.")
    try:
        ncpu = os.cpu_count() or 1
        load5 = float(_read("/proc/loadavg").split()[1])
        sample["load"] = round(load5 / ncpu, 2)
        check("load", load5 / ncpu, t("load", "per_core_warning", 1.5), t("load", "per_core_critical", 3.0), "load",
              "Load %%.2f per CPU (5-min load %.2f on %d CPUs)" % (load5, ncpu))
    except (IndexError, ValueError):
        pass
    mem, swap = meminfo()
    sample["mem"] = round(mem, 1) if mem is not None else None
    sample["swap"] = round(swap, 1) if swap is not None else None
    check("memory", mem, t("memory", "warning", 85), t("memory", "critical", 95), "memory", "Memory used %.0f%%",
          "Check top memory consumers.")
    check("swap", swap, t("swap", "warning", 50), t("swap", "critical", 80), "swap", "Swap used %.0f%%")

    overrides = {}
    for mp, val in cfg.section("thresholds", "disk_overrides").items():
        p = [x.strip() for x in val.split(",")]
        if len(p) == 2:
            try:
                overrides[mp] = (float(p[0]), float(p[1]))
            except ValueError:
                pass
    seen = set()
    for line in _read("/proc/mounts").splitlines():
        f = line.split()
        if len(f) < 4:
            continue
        mp, fstype, opts = f[1].replace("\\040", " "), f[2], f[3].split(",")
        if fstype in PSEUDO_FS or mp in seen or mp.startswith(("/proc", "/sys", "/dev", "/run/user", "/snap/")):
            continue
        seen.add(mp)
        if fstype in NETWORK_FS:
            st, err = prober.probe(mp)
            if st is None:
                out["netfs:" + mp] = ("CRITICAL", mp, "Network filesystem %s (%s) is not responding: %s" % (mp, fstype, err),
                                      "Check the NFS/CIFS server and network.")
                continue
        else:
            try:
                st = os.statvfs(mp)
            except OSError:
                continue
        if st.f_blocks:
            used = 100.0 * (st.f_blocks - st.f_bfree) / (st.f_blocks - st.f_bfree + st.f_bavail or 1)
            sample["disk"][mp] = round(used, 1)
            w, c = overrides.get(mp, (t("disk", "warning", 80), t("disk", "critical", 90)))
            check("disk:" + mp, used, w, c, mp, "Filesystem " + mp.replace("%", "%%") + " is %.0f%% full",
                  "Free space or extend the filesystem.")
        if st.f_files:
            ipct = 100.0 * (st.f_files - st.f_ffree) / st.f_files
            check("inode:" + mp, ipct, t("inode", "warning", 80), t("inode", "critical", 90), mp,
                  "Inode usage on " + mp.replace("%", "%%") + " is %.0f%%")
        if "ro" in opts and fstype not in ("iso9660", "squashfs", "udf"):
            out["ro:" + mp] = ("CRITICAL", mp, "Filesystem %s is mounted read-only" % mp,
                               "Check dmesg/journal for I/O or filesystem errors.")
    return out


def service_probe(cfg, runner, state):
    out = {}
    services = cfg.getlist("services", "critical", "services", "sshd, crond")
    if services and runner.has("systemctl") and os.path.isdir("/run/systemd/system"):
        units = [s if "." in s else s + ".service" for s in services]
        r = runner.run(["systemctl", "is-active"] + units, timeout=15)
        for unit, st in zip(units, r.out.splitlines()):
            if st.strip() not in ("active", "reloading", "activating"):
                out["svc:" + unit] = ("CRITICAL", unit, "Critical service %s is %s" % (unit, st.strip() or "unknown"),
                                      "Check 'journalctl -u %s'." % unit)
    ports = cfg.section("services", "ports")
    if ports:
        listening = set(p for _, p in listening_ports_from_proc(runner))
        for name, port in ports.items():
            p = to_int(port)
            if p and p not in listening:
                out["port:%s" % p] = ("CRITICAL", "%s:%d" % (name, p), "Expected port %d (%s) is not listening" % (p, name), "")
    procs = cfg.section("services", "processes")
    if procs:
        import re
        cmdlines = []
        for pid in os.listdir("/proc"):
            if pid.isdigit():
                c = _read("/proc/%s/cmdline" % pid).replace("\x00", " ").strip()
                if c:
                    cmdlines.append(c)
        for name, pattern in procs.items():
            try:
                rx = re.compile(pattern)
            except re.error:
                rx = re.compile(re.escape(pattern))
            if not any(rx.search(c) for c in cmdlines):
                out["proc:" + name] = ("CRITICAL", name, "Required process '%s' is not running" % name, "")
    return out


# --- sequential (rotating) module checks -------------------------------------------
def rotation_probe(app, state, now):
    """Run at most one due module per tick; keep its findings until it runs again."""
    from lshme import modules as modreg
    from lshme.core.context import Context
    names = [n for n in as_list(setting(app.cfg, "rotation_modules")) if n in modreg.MODULE_ORDER]
    every = max(1, int(float(setting(app.cfg, "rotation_minutes")))) * 60
    last = state.setdefault("rotation_last", {})
    cache = state.setdefault("rotation_findings", {})
    due = [n for n in names if now - last.get(n, 0) >= every]
    if due:
        name = min(due, key=lambda n: last.get(n, 0))
        mod = modreg.load(name)
        res = ModuleResult(mod.NAME, mod.TITLE, mod.WEIGHT)
        try:
            mod.run(Context(app.cfg, app.paths, app.runner, 1, "watch"), res)
        except Exception as e:
            res.add("UNKNOWN", "MODULE_ERROR", name, "Module failed: %s" % e)
        cache[name] = [[f.severity, f.code, f.component, f.message, f.recommendation] for f in res.findings
                       if SEV_RANK[f.severity] >= SEV_RANK["WARNING"]]
        last[name] = now
    out = {}
    for name, items in cache.items():
        if name not in names:
            continue
        for sev, code, comp, msg, rec in items:
            out["mod:%s:%s:%s" % (name, code, comp)] = (sev, comp, msg, rec)
    return out


# --- exclusions ---------------------------------------------------------------------
PROBE_CODES = {"cpu": ("CPU_HIGH", "cpu_memory"), "load": ("LOAD_HIGH", "cpu_memory"), "memory": ("MEMORY_HIGH", "cpu_memory"),
               "swap": ("SWAP_HIGH", "cpu_memory"), "disk": ("DISK_USAGE", "storage"), "inode": ("INODE_USAGE", "storage"),
               "ro": ("FS_READ_ONLY", "storage"), "netfs": ("NETFS_UNRESPONSIVE", "storage"),
               "svc": ("CRITICAL_SERVICE_DOWN", "services"), "port": ("PORT_NOT_LISTENING", "services"),
               "proc": ("PROCESS_MISSING", "services")}


def code_of(key):
    """Map a watcher problem key to (finding code, module) so exclusion rules apply the same way."""
    if key.startswith("mod:"):
        _, module, code, _ = key.split(":", 3)
        return code, module
    return PROBE_CODES.get(key.split(":", 1)[0], ("WATCH", "watch"))


def filter_excluded(cfg, current):
    from lshme.engine import exclusions
    rules = exclusions.active_rules(exclusions.load(cfg))
    if not rules:
        return current
    out = {}
    for key, val in current.items():
        code, module = code_of(key)
        if exclusions.match(rules, code, val[1], module) is None:
            out[key] = val
    return out


# --- alert state machine ------------------------------------------------------------
def evaluate(state, current, now, confirm, repeat_seconds, min_sev, recovery):
    """Update per-problem state; return (new_or_repeat_alerts, recoveries)."""
    alerts, recovered = [], []
    problems = state.setdefault("problems", {})
    events = state.setdefault("pending_events", [])
    floor = SEV_RANK[min_sev]
    for key, (sev, comp, msg, rec) in current.items():
        p = problems.get(key)
        if p is None:
            p = problems[key] = {"severity": sev, "count": 0, "since": now, "alerted": None, "last_alert": 0}
        p.update(count=p["count"] + 1, component=comp, message=msg, recommendation=rec)
        escalated = p["alerted"] and SEV_RANK[sev] > SEV_RANK[p["alerted"]]
        p["severity"] = sev
        if p["count"] >= confirm and SEV_RANK[sev] >= SEV_RANK["WARNING"]:  # incident log (for SLA reports)
            if not p.get("logged"):
                events.append({"ts": p["since"], "event": "open", "key": key, "severity": sev, "component": comp, "message": msg})
                p["logged"] = sev
            elif SEV_RANK[sev] > SEV_RANK[p["logged"]]:
                events.append({"ts": now, "event": "escalate", "key": key, "severity": sev, "component": comp, "message": msg})
                p["logged"] = sev
            elif SEV_RANK[sev] < SEV_RANK[p["logged"]]:  # e.g. CRITICAL -> WARNING: critical downtime ends here
                events.append({"ts": now, "event": "improve", "key": key, "severity": sev, "component": comp, "message": msg})
                p["logged"] = sev
        if p["alerted"] and SEV_RANK[sev] < SEV_RANK[p["alerted"]] and p["count"] >= confirm:
            # tell the on-call people a critical problem has eased (not yet resolved)
            if recovery:
                recovered.append(dict(p, key=key, kind="IMPROVED", previous=p["alerted"]))
            p["alerted"], p["last_alert"] = sev, now
            if SEV_RANK[sev] < floor:
                p["alerted"] = None
            continue
        if SEV_RANK[sev] < floor or p["count"] < confirm:
            continue
        if not p["alerted"] or escalated or (repeat_seconds and now - p["last_alert"] >= repeat_seconds):
            kind = "ESCALATED" if escalated else ("STILL" if p["alerted"] else "NEW")
            alerts.append(dict(p, key=key, kind=kind))
            p["alerted"], p["last_alert"] = sev, now
    for key in list(problems):
        if key not in current:
            p = problems.pop(key)
            if p.get("logged"):
                events.append({"ts": now, "event": "close", "key": key, "severity": p["logged"],
                               "component": p.get("component", ""), "message": p.get("message", "")})
            if p["alerted"] and recovery:
                recovered.append(dict(p, key=key, kind="RECOVERED", duration=now - p["since"]))
    return alerts, recovered


def format_alert(host, alerts, recovered):
    crit = sum(1 for a in alerts if a["severity"] == "CRITICAL")
    head = []
    if alerts:
        head.append("%d %s" % (len(alerts), "CRITICAL" if crit else "WARNING"))
    fixed = [r for r in recovered if r["kind"] == "RECOVERED"]
    if fixed:
        head.append("%d recovered" % len(fixed))
    if len(fixed) < len(recovered):
        head.append("%d improved" % (len(recovered) - len(fixed)))
    subject = "[LSHME ALERT] %s: %s" % (host, ", ".join(head))
    lines = ["%s - real-time alert for %s at %s" % (APP_NAME, host, time.strftime("%Y-%m-%d %H:%M:%S")), ""]
    for a in alerts:
        lines.append("[%s] %s%s: %s" % (a["severity"], "" if a["kind"] == "NEW" else a["kind"] + " ", a["component"], a["message"]))
        if a.get("recommendation"):
            lines.append("    Action: %s" % a["recommendation"])
    for r in recovered:
        if r["kind"] == "IMPROVED":
            lines.append("[IMPROVED] %s: %s -> %s: %s" % (r["component"], r["previous"], r["severity"], r["message"]))
        else:
            lines.append("[RECOVERED] %s: %s (after %d min)" % (r["component"], r["message"], r["duration"] // 60))
    lines += ["", COPYRIGHT]
    return subject, "\n".join(lines), ("CRITICAL" if crit else "WARNING" if alerts else "HEALTHY")


def deliver(app, subject, body, status):
    """Email + syslog + webhook. Each channel is optional and failures never stop the watcher."""
    results = {}
    log = logs.get()
    cfg = app.cfg
    from lshme.engine import maintenance
    if maintenance.active(app.paths):
        log.info("watch alert suppressed (maintenance): %s", subject)
        return {"suppressed": "maintenance"}
    if as_bool(setting(cfg, "syslog"), True):
        try:
            import syslog
            syslog.openlog("lshme", 0, syslog.LOG_DAEMON)
            syslog.syslog(syslog.LOG_CRIT if status == "CRITICAL" else syslog.LOG_WARNING, subject)
            results["syslog"] = True
        except Exception as e:
            results["syslog"] = str(e)
    if as_bool(setting(cfg, "alert_email"), True):
        from lshme.delivery import mailer
        ms = mailer.MailSettings(cfg)
        to = as_list(setting(cfg, "alert_to")) or None
        if (ms.host or ms.method == "sendmail") and (to or ms.to or ms.critical_to):
            msg, rcpts, _, _ = mailer.build_message(ms, subject, body, None, [], status, to)
            ms.retries = min(ms.retries, 2)
            results["email"] = mailer.send(ms, msg, rcpts, app.paths.log_dir)["ok"]
        else:
            results["email"] = "not configured"
    url = setting(cfg, "webhook_url")
    if url:
        try:
            from urllib.request import Request, urlopen
            data = json.dumps({"text": "%s\n%s" % (subject, body), "status": status}).encode("utf-8")
            urlopen(Request(url, data=data, headers={"Content-Type": "application/json"}), timeout=10).read(1024)
            results["webhook"] = True
        except Exception as e:
            results["webhook"] = str(e)[:200]
    log.warning("watch alert: %s -> %s", subject, results)
    return results


# --- runner -------------------------------------------------------------------------
def lower_priority():
    """Lowest CPU priority and idle I/O class so the watcher never competes with workloads."""
    from lshme.core.priority import set_priority
    set_priority(19, True)


def rss_mb():
    try:
        return int(_read("/proc/self/statm").split()[1]) * os.sysconf("SC_PAGE_SIZE") / 1048576.0
    except (IndexError, ValueError, OSError):
        return 0.0


class Watcher(object):
    def __init__(self, app):
        self.app = app
        self.state_path = os.path.join(app.paths.data_dir, "watch_state.json")
        self.prober = NetfsProber(float(setting(app.cfg, "netfs_timeout")))
        self.stop = False
        self.state = self.load()

    def load(self):
        try:
            with open(self.state_path, encoding="utf-8") as f:
                return json.load(f)
        except (IOError, OSError, ValueError):
            return {}

    def save(self):
        tmp = self.state_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.state, f)
        os.replace(tmp, self.state_path)

    @property
    def events_path(self):
        return os.path.join(self.app.paths.data_dir, "watch_events.jsonl")

    def flush_events(self, now):
        """Append confirmed incident events and keep a per-day check count (monitoring coverage)."""
        events = self.state.pop("pending_events", [])
        if events:
            with open(self.events_path, "a", encoding="utf-8") as f:
                for e in events:
                    f.write(json.dumps(e, sort_keys=True) + "\n")
        cov = self.state.setdefault("coverage", {})
        day = time.strftime("%Y-%m-%d", time.localtime(now))
        cov[day] = cov.get(day, 0) + 1
        self.state["interval"] = max(10, int(float(setting(self.app.cfg, "interval"))))
        for old in sorted(cov)[:-400]:
            del cov[old]

    @property
    def metrics_path(self):
        return os.path.join(self.app.paths.data_dir, "watch_metrics.json")

    def record_metrics(self, now):
        """Rolling history for live graphs (default 24 h; about 200 KB)."""
        s = self.state.pop("sample", None)
        if not s:
            return
        probs = self.state.get("problems", {})
        disks = sorted(s["disk"].items(), key=lambda kv: -kv[1])[:8]  # the fullest filesystems only
        point = {"t": round(now), "cpu": s.get("cpu"), "mem": s.get("mem"), "swap": s.get("swap"), "load": s.get("load"),
                 "disk": dict(disks), "crit": sum(1 for p in probs.values() if p.get("severity") == "CRITICAL"),
                 "warn": sum(1 for p in probs.values() if p.get("severity") == "WARNING")}
        keep = float(setting(self.app.cfg, "metrics_hours")) * 3600
        try:
            with open(self.metrics_path, encoding="utf-8") as f:
                series = json.load(f)
        except (IOError, OSError, ValueError):
            series = []
        series = [p for p in series if now - p.get("t", 0) < keep] + [point]
        tmp = self.metrics_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(series, f, separators=(",", ":"))
        os.replace(tmp, self.metrics_path)

    def tick(self, now=None):
        now = now or time.time()
        cpu0 = time.process_time()
        cfg = self.app.cfg
        current = {}
        for probe in (lambda: fast_probes(cfg, self.state, self.prober),
                      lambda: service_probe(cfg, self.app.runner, self.state),
                      lambda: rotation_probe(self.app, self.state, now)):
            try:
                current.update(probe())
            except Exception as e:
                logs.get().exception("watch probe failed")
                current["watch:probe-error"] = ("UNKNOWN", "watch", "Probe error: %s" % e, "")
        current = filter_excluded(cfg, current)
        alerts, recovered = evaluate(self.state, current, now, max(1, int(setting(cfg, "confirm"))),
                                     float(setting(cfg, "repeat_minutes")) * 60, setting(cfg, "alert_min_severity").upper(),
                                     as_bool(setting(cfg, "recovery_alerts"), True))
        delivery = None
        if alerts or recovered:
            subject, body, status = format_alert(os.uname()[1], alerts, recovered)
            delivery = deliver(self.app, subject, body, status)
        self.flush_events(now)
        self.record_metrics(now)
        self.state["last_tick"] = now
        self.state["last_tick_cpu_ms"] = round((time.process_time() - cpu0) * 1000, 1)
        self.state["rss_mb"] = round(rss_mb(), 1)
        self.state["ticks"] = self.state.get("ticks", 0) + 1
        self.save()
        return alerts, recovered, delivery

    def run_forever(self):
        lower_priority()
        signal.signal(signal.SIGTERM, lambda *a: setattr(self, "stop", True))
        interval = max(10, int(float(setting(self.app.cfg, "interval"))))
        limit = float(setting(self.app.cfg, "max_rss_mb"))
        logs.get().info("watch started: interval %ss", interval)
        while not self.stop:
            start = time.time()
            self.tick(start)
            if limit and self.state.get("rss_mb", 0) > limit:
                logs.get().error("watch memory %.0f MB above max_rss_mb %.0f; exiting for a clean restart", self.state["rss_mb"], limit)
                return 3
            while not self.stop and time.time() - start < interval:
                time.sleep(min(1.0, interval - (time.time() - start)))
        logs.get().info("watch stopped")
        return 0


# --- installation -------------------------------------------------------------------
UNIT = """[Unit]
Description=LSHME real-time health monitoring
After=network-online.target

[Service]
Type=simple
ExecStart={cmd} watch run
Restart=on-failure
RestartSec=30
Nice=19
IOSchedulingClass=idle
CPUQuota={cpu}%
MemoryMax={mem}M
TasksMax=16
# LSHME_MANAGED

[Install]
WantedBy=multi-user.target
"""


def install(app, cpu_quota=5, memory_mb=128):
    import shlex
    cmd = " ".join(shlex.quote(a) for a in [app.launcher()] + app.global_args())
    if app.runner.has("systemctl") and os.path.isdir("/run/systemd/system"):
        d = app.cfg.get("schedule", "scheduler", "systemd_dir", "/etc/systemd/system")
        path = os.path.join(d, "lshme-watch.service")
        with open(path, "w", encoding="utf-8") as f:
            f.write(UNIT.format(cmd=cmd, cpu=cpu_quota, mem=memory_mb))
        app.runner.run(["systemctl", "daemon-reload"])
        r = app.runner.run(["systemctl", "enable", "--now", "lshme-watch.service"])
        return "systemd", path, r.ok, r.brief_error() if not r.ok else ""
    path = "/etc/cron.d/lshme-watch"
    with open(path, "w", encoding="utf-8") as f:
        f.write("# Managed by LSHME: real-time checks every minute (no systemd on this host)\n"
                "* * * * * root nice -n 19 %s watch once >/dev/null 2>&1\n" % cmd)
    return "cron", path, True, ""


def uninstall(app):
    """Remove the watcher service/cron entry, but only if it runs this installation."""
    removed = []
    owner = app.global_args()
    d = app.cfg.get("schedule", "scheduler", "systemd_dir", "/etc/systemd/system")
    unit = os.path.join(d, "lshme-watch.service")
    if os.path.exists(unit) and owned_by(unit, owner):
        app.runner.run(["systemctl", "disable", "--now", "lshme-watch.service"])
        os.remove(unit)
        app.runner.run(["systemctl", "daemon-reload"])
        removed.append(unit)
    if os.path.exists("/etc/cron.d/lshme-watch") and owned_by("/etc/cron.d/lshme-watch", owner):
        os.remove("/etc/cron.d/lshme-watch")
        removed.append("/etc/cron.d/lshme-watch")
    return removed
