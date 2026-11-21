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
                    self.latest[name] = dict(prev, ok=False, error=p["error"], fetched=now, status="UNREACHABLE")
                    continue
                self.latest[name] = dict(p, ok=True, error="", fetched=now)
                h = self.history.setdefault(name, deque(maxlen=KEEP))
                h.append([now, p.get("cpu"), p.get("mem")])
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
                out.append(self._row(sv.name, meta, p) if p else dict(meta, ok=False, status="WAITING", error="", series=[]))
            return out

    def _row(self, key, meta, p):
        row = dict(meta)
        row.update(p)
        row["name"] = meta["name"]
        row["series"] = [[t, c, m] for t, c, m in list(self.history.get(key, []))[-60:]] or p.get("series", [])
        return row

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


def enabled(cfg):
    return as_bool(cfg.get("main", "fleet", "live", "yes"), True)
