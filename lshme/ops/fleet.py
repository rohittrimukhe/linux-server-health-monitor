# Linux Server Health Monitor Enterprise
# Copyright (c) 2026 LRS Services Pvt Ltd, Mumbai. All rights reserved.
"""Central multi-server monitoring.

The central server runs `healthcheck agent` on every server in servers.conf
over SSH (key-based, BatchMode) in parallel, or reads JSON files that agents
pushed into a drop directory (for servers the central host cannot reach).
Results are stored in the central history and combined into a fleet report.
"""
import glob
import json
import os
import shlex
import time
from concurrent.futures import ThreadPoolExecutor

from lshme.core.config import as_bool
from lshme.core.model import RunResult


class Server(object):
    def __init__(self, name, s, defaults):
        g = lambda k: s.get(k) or defaults.get(k, "")
        self.name = name
        self.host = g("host") or name
        self.user = g("user")
        self.port = g("port") or "22"
        self.group = s.get("group", "")
        self.environment = s.get("environment", "")
        self.identity = g("identity_file")
        self.ssh_options = g("ssh_options")
        self.remote_command = g("remote_command")
        self.timeout = int(g("timeout") or 900)
        self.enabled = as_bool(s.get("enabled", "yes"), True)


def servers(cfg):
    defaults = cfg.section("servers", "defaults")
    out = []
    for section in cfg.sections("servers", "server:"):
        sv = Server(section.split(":", 1)[1], cfg.section("servers", section), defaults)
        if sv.enabled:
            out.append(sv)
    return out


def ssh_command(sv, days):
    cmd = ["ssh", "-p", str(sv.port)] + shlex.split(sv.ssh_options or "")
    if sv.identity:
        cmd += ["-i", sv.identity]
    target = "%s@%s" % (sv.user, sv.host) if sv.user else sv.host
    return cmd + [target, sv.remote_command.format(days=int(days))]


def parse_payload(text):
    start = text.find("{")
    if start < 0:
        raise ValueError("no JSON in agent output")
    return RunResult.from_dict(json.loads(text[start:]))


def collect_one(runner, sv, days):
    r = runner.run(ssh_command(sv, days), timeout=sv.timeout)
    if not r.ok and not r.out.strip().startswith("{"):
        raise RuntimeError(r.brief_error())
    run = parse_payload(r.out)
    if sv.group:
        run.meta["group"] = sv.group
    if sv.environment:
        run.meta["environment"] = sv.environment
    run.meta["fleet_name"] = sv.name
    return run


def collect(cfg, runner, days, only=None, local_runner=None, progress=None):
    svs = [s for s in servers(cfg) if not only or s.name in only or s.host in only]
    parallel = cfg.getint("servers", "defaults", "parallel", 8)
    runs, errors = [], {}

    def task(sv):
        try:
            if sv.host in ("local", "localhost") and local_runner is not None:
                run = local_runner(days)
                run.meta["group"], run.meta["environment"] = sv.group, sv.environment or run.meta.get("environment", "")
                return sv, run, None
            return sv, collect_one(runner, sv, days), None
        except Exception as e:
            return sv, None, str(e)
    with ThreadPoolExecutor(max_workers=max(1, parallel)) as pool:
        for sv, run, err in pool.map(task, svs):
            if run is not None:
                runs.append(run)
            else:
                errors[sv.name] = err
            if progress:
                progress(sv.name, err)
    return runs, errors


def read_dropdir(path, max_age_hours=48):
    """Load agent JSON files pushed to a directory (newest per host)."""
    newest = {}
    for f in glob.glob(os.path.join(path, "*.json")):
        try:
            with open(f, encoding="utf-8") as fh:
                run = RunResult.from_dict(json.load(fh))
        except (IOError, OSError, ValueError, KeyError):
            continue
        if time.time() - run.collected_at > max_age_hours * 3600:
            run.meta["stale"] = True
        cur = newest.get(run.host)
        if cur is None or run.collected_at > cur.collected_at:
            newest[run.host] = run
    return list(newest.values())
