# Linux Server Health Monitor Enterprise
# Copyright (c) 2026 LRS Services Pvt Ltd, Mumbai. All rights reserved.
"""Live monitoring of every server from the central server.

Every few seconds (default 30, [fleet] live_seconds) the central server asks each member server in servers.conf for its `vitals pulse` over SSH
(key login, one persistent connection per server) and keeps the newest answer plus the last hour of CPU and memory. The dashboard and Cockpit show it.
An unreachable server is shown as such with the reason, never silently dropped."""
import json
import shlex
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor

from lshme.core.config import as_bool
from lshme.ops import fleet, pulse

KEEP = 120                    # samples kept per server
MIN_SECONDS = 10
SAN_SECONDS = 300             # SAN storage is asked less often: its API calls are heavier ([fleet] san_live_seconds)
OUTDATED = "Runs an older LRS Vitals without live data. Upgrade it on that server: bash /tmp/upgrade.sh (or the curl command in docs/DEPLOYMENT.md)."


def outdated(message):
    """An SSH answer that means the server works but its LRS Vitals is too old to have 'vitals pulse'."""
    m = (message or "").lower()
    return "invalid choice: 'pulse'" in m or "invalid choice: \'pulse\'" in m or ("unknown" in m and "pulse" in m)


def ssh_command(sv, remote, sock_dir=None):
    """The fixed SSH command line for one member server; 'remote' is one of our own fixed commands, never text from a request."""
    return ssh_pulse_command(sv, sock_dir)[:-1] + [remote]


def ssh_pulse_command(sv, sock_dir=None):
    """The fixed command line that asks one server for its pulse. No part of it comes from a web request; the host, port and user come from servers.conf."""
    target = "%s@%s" % (sv.user, sv.host) if sv.user else sv.host
    cmd = ["ssh", "-p", str(sv.port), "-o", "BatchMode=yes", "-o", "ConnectTimeout=6"]
    if sock_dir:                                                              # one connection kept open per server (private folder, owner only)
        cmd += ["-o", "ControlMaster=auto", "-o", "ControlPath=%s/%%C" % sock_dir, "-o", "ControlPersist=120"]
    cmd += shlex.split(sv.ssh_options or "")
    if sv.identity:
        cmd += ["-i", sv.identity]
    return cmd + [target, "vitals pulse"]


def parse(text):
    start = (text or "").find("{")
    if start < 0:
        raise ValueError("no answer from 'vitals pulse' (is LRS Vitals installed and up to date there?)")
    return json.loads(text[start:])


class LiveFleet(object):
    def __init__(self, app):
        self.app = app
        self.lock = threading.Lock()
        self.latest = {}
        self.history = {}
        self.updated = 0.0
        self.thread = None
        self.stop = False
        self.san = {}                 # {array name: summary}
        self.san_updated = 0.0
        self.san_thread = None
        self.san_perf = {}            # {array name: deque of [t, read IOPS, write IOPS, read MB/s, write MB/s, response ms]}
        self.san_vols = {}            # {array name: busiest volumes now}
        self.perf_thread = None

    @property
    def interval(self):
        try:
            return max(MIN_SECONDS, int(float(self.app.cfg.get("main", "fleet", "live_seconds", "30") or 30)))
        except ValueError:
            return 30

    def members(self):
        return [sv for sv in fleet.servers(self.app.cfg)]

    def sock_dir(self):
        import os
        d = os.path.join(self.app.paths.data_dir, "ssh")
        try:
            os.makedirs(d, mode=0o700, exist_ok=True)
            os.chmod(d, 0o700)
        except OSError:
            return None
        return d if len(d) < 60 else None                                    # a unix socket path must stay short

    def _ask(self, sv):
        r = self.app.runner.run(ssh_pulse_command(sv, self.sock_dir()), timeout=15)
        if not r.ok and "{" not in (r.out or ""):
            raise RuntimeError((r.err or r.out or "no answer").strip().splitlines()[-1][:200])
        return parse(r.out)

    def poll_once(self):
        """Ask every server once (in parallel). Returns the snapshot."""
        now = time.time()
        got = {"": pulse.collect(self.app, now)}                           # this server needs no SSH
        mem = self.members()
        if mem:
            with ThreadPoolExecutor(max_workers=min(8, len(mem))) as pool:
                futures = [(sv, pool.submit(self._ask, sv)) for sv in mem]
                for sv, fut in futures:
                    try:
                        got[sv.name] = fut.result(timeout=25)
                    except Exception as e:
                        got[sv.name] = {"error": str(e)[:200]}
        with self.lock:
            for name, p in got.items():
                if "error" in p:
                    prev = self.latest.get(name) or {}
                    if outdated(p["error"]):                     # reachable, but too old for live data: say what to do
                        self.latest[name] = dict(prev, ok=False, error=OUTDATED, fetched=now, status="OUTDATED")
                    else:
                        self.latest[name] = dict(prev, ok=False, error=p["error"], fetched=now, status="UNREACHABLE")
                    continue
                self.latest[name] = dict(p, ok=True, error="", fetched=now)
                h = self.history.setdefault(name, deque(maxlen=KEEP))
                h.append([now, p.get("cpu"), p.get("mem"), p.get("net_rx"), p.get("net_tx")])
            self.updated = now
        return self.snapshot()

    def prime(self):
        """This server's own pulse at once (no SSH), so the first view never waits for slow or unreachable servers."""
        p = pulse.collect(self.app)
        with self.lock:
            if "" not in self.latest:
                self.latest[""] = dict(p, ok=True, error="", fetched=time.time())

    def snapshot(self):
        with self.lock:
            out = []
            here = self.latest.get("")
            if here:
                out.append(self._row("", {"name": here.get("host", "this server"), "group": "Central", "environment": "", "host": ""}, here))
            for sv in self.members():
                p = self.latest.get(sv.name)
                meta = {"name": sv.name, "group": sv.group, "environment": sv.environment, "host": sv.host}
                out.append(self._row(sv.name, meta, p) if p else dict(meta, key=sv.name, ok=False, status="WAITING", error="", series=[]))
            return out

    def _row(self, key, meta, p):
        row = dict(meta)
        row.update(p)
        row["name"] = meta["name"]
        row["key"] = key or "_local"                       # what the server page is opened with
        row["series"] = [list(x) for x in list(self.history.get(key, []))[-90:]] or p.get("series", [])
        return row

    # -- SAN storage --------------------------------------------------------------------------------
    @property
    def san_interval(self):
        try:
            return max(60, int(float(self.app.cfg.get("main", "fleet", "san_live_seconds", str(SAN_SECONDS)) or SAN_SECONDS)))
        except ValueError:
            return SAN_SECONDS

    def poll_san(self):
        """Ask every SAN storage system once and keep a short summary for the dashboard."""
        from lshme.ops import san
        arrays = [a for a in san.arrays(self.app.cfg) if a["enabled"]]
        got = {}
        for a in arrays:
            got[a["name"]] = summarize(a, now=time.time())
        with self.lock:
            self.san = got
            self.san_updated = time.time()
            for name, s in got.items():                          # makes without a light I/O reading get one point per full check
                p = s.get("perf") or {}
                if p and name not in self._perf_capable():
                    self._add_perf(name, s["fetched"], p)
        return got

    def _perf_capable(self):
        from lshme.ops import san
        return set(a["name"] for a in san.arrays(self.app.cfg) if a["enabled"] and san.kind(a["type"]) in san.PERF)

    def _add_perf(self, name, t, p):
        h = self.san_perf.setdefault(name, deque(maxlen=KEEP))
        h.append([t, round(p.get("read_iops") or 0, 1), round(p.get("write_iops") or 0, 1), round(p.get("read_mbps") or 0, 2),
                  round(p.get("write_mbps") or 0, 2), round(p.get("latency_ms") or 0, 2) if p.get("latency_ms") is not None else None])

    @property
    def san_perf_interval(self):
        try:
            return max(10, int(float(self.app.cfg.get("main", "fleet", "san_perf_seconds", "30") or 30)))
        except ValueError:
            return 30

    def poll_san_perf(self):
        from lshme.ops import san
        for a in san.arrays(self.app.cfg):
            if not a["enabled"] or san.kind(a["type"]) not in san.PERF:
                continue
            try:
                p = san.perf(a, prefer=(self.san.get(a["name"]) or {}).get("reached", ""))
            except Exception:
                continue
            if p is None:
                continue
            with self.lock:
                self._add_perf(a["name"], time.time(), p)
                self.san_vols[a["name"]] = p.get("volumes") or []

    def _perf_loop(self):
        while not self.stop:
            try:
                self.poll_san_perf()
            except Exception:
                pass
            for _ in range(self.san_perf_interval):
                if self.stop:
                    return
                time.sleep(1)

    def san_snapshot(self):
        from lshme.ops import san
        with self.lock:
            out = []
            for a in san.arrays(self.app.cfg):
                if not a["enabled"]:
                    continue
                s = self.san.get(a["name"])
                if s:
                    s = dict(s, series=list(self.san_perf.get(a["name"], []))[-90:], volumes_top=self.san_vols.get(a["name"], []))
                out.append(s if s else {"name": a["name"], "type": a["type"], "make": san.TYPES.get(a["type"], (a["type"],))[0], "status": "WAITING",
                                        "controllers": [], "pools": [], "error": ""})
            return out

    def _san_loop(self):
        while not self.stop:
            try:
                self.poll_san()
            except Exception:
                pass
            for _ in range(self.san_interval):
                if self.stop:
                    return
                time.sleep(1)

    def _loop(self):
        while not self.stop:
            try:
                self.poll_once()
            except Exception:
                pass
            for _ in range(self.interval):
                if self.stop:
                    return
                time.sleep(1)

    def start(self):
        if self.thread and self.thread.is_alive():
            return
        self.thread = threading.Thread(target=self._loop, name="lrs-livefleet", daemon=True)
        self.thread.start()
        if not (self.san_thread and self.san_thread.is_alive()):
            self.san_thread = threading.Thread(target=self._san_loop, name="lrs-livesan", daemon=True)
            self.san_thread.start()
        if not (self.perf_thread and self.perf_thread.is_alive()):
            self.perf_thread = threading.Thread(target=self._perf_loop, name="lrs-livesanio", daemon=True)
            self.perf_thread.start()


def summarize(a, now=None, collect=None):
    """One storage system for the dashboard card: status, controllers, drives, fullest pools, I/O. Never raises."""
    from lshme.ops import san
    now = now or time.time()
    make = san.TYPES.get(a["type"], (a["type"],))[0]
    try:
        d = (collect or san.collect)(a)
    except Exception as e:
        return {"name": a["name"], "type": a["type"], "make": make, "status": "UNREACHABLE", "error": str(e)[:200], "fetched": now,
                "controllers": [], "pools": [], "drives": 0, "failed_drives": 0}
    bad_hw = [h["name"] for h in d["hardware"] if h["status"] not in ("optimal", "ok", "unknown", "")]
    failed = [x["slot"] for x in d["drives"] if x["status"] in ("failed", "removed", "fault", "broken")]
    pools = sorted([{"name": p["name"], "pct": p["used_percent"], "free": p["free"], "status": p["status"]} for p in d["pools"] if p["used_percent"] is not None],
                   key=lambda p: -p["pct"])
    ctl_bad = [c for c in d["controllers"] if c["status"] != "optimal"]
    st = "HEALTHY"
    if d["status"] not in ("optimal",) or failed or ctl_bad or any(p["status"] not in ("optimal", "online", "unknown", "") for p in d["pools"]):
        st = "CRITICAL"
    elif bad_hw or d["unreachable"] or any(p["pct"] >= 80 for p in pools):
        st = "WARNING"
    return {"name": a["name"], "type": a["type"], "make": make, "model": d.get("model", ""), "system_name": d.get("system_name", ""), "firmware": d.get("firmware", ""),
            "status": st, "array_status": d["status"], "error": "", "fetched": now, "reached": d.get("reached", ""), "unreachable": d.get("unreachable", []),
            "controllers": [{"name": c["name"], "status": c["status"]} for c in d["controllers"]], "drives": len(d["drives"]), "failed_drives": len(failed),
            "failed_slots": failed[:6], "hardware_bad": bad_hw[:6], "pools": pools[:4], "volumes": len(d["volumes"]), "perf": d.get("perf") or {}}


def enabled(cfg):
    return as_bool(cfg.get("main", "fleet", "live", "yes"), True)
