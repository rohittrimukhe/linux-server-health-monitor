# Linux Server Health Monitor Enterprise
# Copyright (c) 2026 LRS Services Pvt Ltd, Mumbai. All rights reserved.
"""Configuration baseline and drift detection.

A baseline is a snapshot of things that should change only under change
control: enabled services, listening ports, mounts, installed packages,
privileged accounts, kernel and scheduled jobs. Each run compares against it.
"""
import hashlib
import json
import os
import time

from lshme.core.model import ModuleResult
from lshme.core.util import listening_ports


def snapshot(ctx):
    r = ctx.runner
    snap = {"taken": time.time(), "host": ctx.hostname}
    snap["kernel"] = os.uname()[2]
    enabled = []
    if r.has("systemctl"):
        out = r.run(["systemctl", "list-unit-files", "--type=service", "--state=enabled", "--no-legend", "--no-pager"])
        enabled = sorted(l.split()[0] for l in out.lines() if l.split())
    snap["enabled_services"] = enabled
    snap["listening_ports"] = sorted(set("%s/%s" % (p["proto"].rstrip("6"), p["port"]) for p in listening_ports(r)))
    mounts = []
    for line in r.read("/proc/mounts").splitlines():
        p = line.split()
        if len(p) >= 3 and p[2] not in ("tmpfs", "proc", "sysfs", "cgroup", "cgroup2", "devpts", "mqueue", "overlay",
                                        "securityfs", "debugfs", "tracefs", "bpf", "pstore", "autofs", "nsfs", "fusectl",
                                        "configfs", "hugetlbfs", "rpc_pipefs", "binfmt_misc", "devtmpfs", "squashfs"):
            mounts.append("%s %s" % (p[1], p[2]))
    snap["mounts"] = sorted(set(mounts))
    pk = []
    if r.has("rpm"):
        pk = r.run(["rpm", "-qa", "--qf", "%{NAME}\n"], timeout=120).lines()
    elif r.has("dpkg-query"):
        pk = r.run(["dpkg-query", "-W", "-f", "${Package}\n"], timeout=120).lines()
    snap["packages"] = sorted(set(pk))
    passwd = r.read("/etc/passwd").splitlines()
    snap["uid0_accounts"] = sorted(l.split(":")[0] for l in passwd if l.count(":") >= 6 and l.split(":")[2] == "0")
    snap["login_accounts"] = sorted(l.split(":")[0] for l in passwd if l.count(":") >= 6 and
                                    not l.split(":")[6].endswith(("nologin", "false", "sync", "shutdown", "halt")))
    crons = {}
    for path in ["/etc/crontab"] + r.glob("/etc/cron.d/*") + r.glob("/var/spool/cron/*") + r.glob("/var/spool/cron/crontabs/*"):
        text = r.read(path)
        if text:
            crons[path] = hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()[:16]
    snap["cron_files"] = crons
    for f in ("/etc/ssh/sshd_config", "/etc/sudoers", "/etc/fstab", "/etc/hosts", "/etc/resolv.conf", "/etc/selinux/config"):
        text = r.read(f)
        snap.setdefault("config_hashes", {})[f] = hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()[:16] if text else ""
    return snap


def path_for(paths):
    return os.path.join(paths.data_dir, "baseline.json")


def save(ctx, paths):
    snap = snapshot(ctx)
    with open(path_for(paths), "w", encoding="utf-8") as f:
        json.dump(snap, f, indent=1, sort_keys=True)
    os.chmod(path_for(paths), 0o600)
    return snap


def load(paths):
    try:
        with open(path_for(paths), encoding="utf-8") as f:
            return json.load(f)
    except (IOError, OSError, ValueError):
        return None


def compare(base, cur):
    """Return list of (category, added, removed / changed)."""
    out = []
    for key in ("enabled_services", "listening_ports", "mounts", "packages", "uid0_accounts", "login_accounts"):
        a, b = set(base.get(key, [])), set(cur.get(key, []))
        if a != b:
            out.append((key, sorted(b - a), sorted(a - b)))
    if base.get("kernel") != cur.get("kernel"):
        out.append(("kernel", [cur.get("kernel")], [base.get("kernel")]))
    for key in ("cron_files", "config_hashes"):
        a, b = base.get(key, {}), cur.get(key, {})
        changed = sorted(k for k in set(a) | set(b) if a.get(k) != b.get(k))
        if changed:
            out.append((key, changed, []))
    return out


SEVERITY = {"uid0_accounts": "CRITICAL", "login_accounts": "WARNING", "listening_ports": "WARNING",
            "enabled_services": "WARNING", "mounts": "WARNING", "config_hashes": "WARNING", "cron_files": "WARNING",
            "packages": "INFO", "kernel": "INFO"}
LABEL = {"uid0_accounts": "UID 0 accounts", "login_accounts": "login-capable accounts", "listening_ports": "listening ports",
         "enabled_services": "enabled services", "mounts": "mounted filesystems", "config_hashes": "key configuration files",
         "cron_files": "cron files", "packages": "installed packages", "kernel": "running kernel"}


def drift_module(ctx, paths):
    base = load(paths)
    res = ModuleResult("drift", "Configuration Drift", 5)
    if not base:
        res.status = "SKIPPED"
        res.summary = "no baseline saved (run: healthcheck baseline save)"
        res.skip("no baseline")
        return res
    cur = snapshot(ctx)
    diffs = compare(base, cur)
    rows = []
    for key, added, removed in diffs:
        detail = []
        if added:
            detail.append(("changed: " if key in ("cron_files", "config_hashes") else "added: ") + ", ".join(added[:25]))
        if removed:
            detail.append("removed: " + ", ".join(removed[:25]))
        text = "; ".join(detail)
        rows.append([LABEL[key], len(added), len(removed), text[:500]])
        res.add(SEVERITY[key], "DRIFT_" + key.upper(), LABEL[key], "Drift in %s since baseline: %s" % (LABEL[key], text[:300]),
                recommendation="Confirm the change was approved; refresh the baseline afterwards (healthcheck baseline save).")
    res.table("Drift since baseline of %s" % time.strftime("%Y-%m-%d %H:%M", time.localtime(base.get("taken", 0))),
              ["Area", "Added/changed", "Removed", "Detail"], rows or [["none", 0, 0, "no drift detected"]], kind="summary")
    res.data = {"baseline_taken": base.get("taken"), "differences": diffs}
    res.summary = "%d area(s) drifted from baseline" % len(diffs)
    return res
