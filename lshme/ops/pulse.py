# Linux Server Health Monitor Enterprise
# Copyright (c) 2026 LRS Services Pvt Ltd, Mumbai. All rights reserved.
"""One server's live state in a single small JSON object (`vitals pulse`).

It reads what the live watcher already wrote (watch_metrics.json, watch_state.json, watch_events.jsonl) and the newest full health check
(latest.json), so it is instant and adds no load. The central server asks every member server for its pulse over SSH to show all servers live."""
import json
import os
import socket
import time

from lshme import VERSION


def _load(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (IOError, OSError, ValueError):
        return default


def collect(app, now=None):
    now = now or time.time()
    d = app.paths.data_dir
    state = _load(os.path.join(d, "watch_state.json"), {})
    series = _load(os.path.join(d, "watch_metrics.json"), [])
    latest = _load(os.path.join(d, "latest.json"), {})
    last = series[-1] if series else {}
    interval = float(state.get("interval", 60) or 60)
    age = (now - state["last_tick"]) if state.get("last_tick") else None
    messages = {}
    try:
        with open(os.path.join(d, "watch_events.jsonl"), encoding="utf-8") as f:
            for line in f.read().splitlines()[-300:]:
                try:
                    e = json.loads(line)
                    messages[e.get("key")] = e
                except ValueError:
                    continue
    except OSError:
        pass
    problems = []
    for key, p in (state.get("problems") or {}).items():
        e = messages.get(key) or {}
        problems.append({"key": key, "severity": p.get("severity"), "component": e.get("component") or key, "message": e.get("message") or key, "since": p.get("since")})
    problems.sort(key=lambda p: (0 if p["severity"] == "CRITICAL" else 1, -(p["since"] or 0)))
    crit = sum(1 for p in problems if p["severity"] == "CRITICAL")
    warn = sum(1 for p in problems if p["severity"] == "WARNING")
    status = "CRITICAL" if crit else "WARNING" if warn else "HEALTHY"
    if age is None or age > max(300, interval * 5):
        status = "STALE" if series else "UNKNOWN"                       # the watcher is not running (or never has)
    disks = sorted((last.get("disk") or {}).items(), key=lambda kv: -kv[1])[:4]
    net = last.get("net") or {}
    health = latest.get("health") or {}
    try:
        with open("/proc/uptime", encoding="utf-8") as f:
            uptime = float(f.read().split()[0])
    except (IOError, OSError, ValueError, IndexError):
        uptime = None
    pts = [p for p in series if now - p.get("t", 0) <= 3600][-60:]
    return {"host": socket.gethostname(), "ts": now, "version": VERSION, "status": status, "cpu": last.get("cpu"), "mem": last.get("mem"), "swap": last.get("swap"),
            "load": last.get("load"), "cores": len(last.get("cores") or []) or (os.cpu_count() or 0), "disks": [{"mount": k, "pct": v} for k, v in disks],
            "net_rx": sum(v[0] for v in net.values() if isinstance(v, (list, tuple)) and len(v) > 1), "net_tx": sum(v[1] for v in net.values() if isinstance(v, (list, tuple)) and len(v) > 1),
            "critical": crit, "warning": warn, "problems": problems[:8], "watcher_age": age, "uptime_s": uptime,
            "score": health.get("score"), "health_status": health.get("status"), "checked_at": latest.get("collected_at"),
            "series": [[p["t"], p.get("cpu"), p.get("mem")] for p in pts]}
