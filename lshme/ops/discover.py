# Linux Server Health Monitor Enterprise
# Copyright (c) 2026 Rohit Santosh Trimukhe. All rights reserved.
"""Server scan: find out what is actually present (KVM, databases, backup products, Cockpit, containers,
clusters, ...) so only the modules that apply are run and reported.

`scan(ctx)` is read-only. `apply(app, profile)` stores the result (profile.json in the data folder) and lists
the absent modules in main.conf [modules] auto_excluded; the module registry then leaves them out of every run,
schedule and report. A weekly rescan (see the scheduler) notices new software and brings modules back."""
import json
import os
import time

from lshme import VERSION
from lshme import modules as modreg
from lshme.core.config import as_list
from lshme.core.model import iso

ALWAYS = {"system": "every server has an operating system", "cpu_memory": "every server has CPU and memory",
          "storage": "every server has filesystems", "folders": "folder sizes feed the growth reports", "services": "every server runs services",
          "network": "every server has a network", "hardware": "every server has hardware",
          "logs": "every server writes logs", "security": "every server has a security posture",
          "selfcheck": "checks that LRS Vitals itself is working"}
KVM_DAEMONS = ("/usr/sbin/libvirtd", "/usr/sbin/virtqemud", "/run/libvirt", "/var/run/libvirt")
BACKUP_TOOLS = ("restic", "borg", "duplicity", "bacula-fd", "bconsole", "rsnapshot", "rear", "veeamconfig", "dsmc", "bpclimagelist")
PSEUDO_FS = ("proc", "sysfs", "devtmpfs", "tmpfs", "devpts", "cgroup", "cgroup2", "securityfs", "debugfs", "tracefs",
             "configfs", "fusectl", "mqueue", "hugetlbfs", "pstore", "bpf", "autofs", "binfmt_misc", "selinuxfs",
             "overlay", "squashfs", "ramfs", "rpc_pipefs", "nsfs", "efivarfs", "fuse.gvfsd-fuse", "fuse.portal")


# --- per-module detectors: each returns (present, why) ------------------------------------------------
def _kvm(ctx):
    if not ctx.has("virsh"):
        return False, "virsh is not installed"
    if any(ctx.runner.exists(p) for p in KVM_DAEMONS):
        return True, "libvirt is installed"
    return False, "only the libvirt client is installed (no libvirt daemon)"


def _cockpit(ctx):
    from lshme.modules import cockpit
    pk = cockpit.packages(ctx)
    if pk:
        return True, "package %s %s" % (pk[0][0], pk[0][1])
    if ctx.runner.exists("/usr/libexec/cockpit-ws") or ctx.runner.exists("/usr/lib/systemd/system/cockpit.socket"):
        return True, "Cockpit files found"
    return False, "Cockpit is not installed"


def database_engines(ctx):
    from lshme.modules import database
    procs = database.discover_processes(ctx)
    insts = database.instances(ctx, procs)
    return sorted(set(procs) | set(i["engine"] for i in insts))


def _database(ctx):
    engines = database_engines(ctx)
    return (True, "found: %s" % ", ".join(engines)) if engines else (False, "no database engine is running or configured")


def _backup(ctx):
    found = []
    if ctx.runner.exists("/usr/openv/netbackup/bin"):
        found.append("NetBackup")
    if ctx.runner.exists("/opt/VRTSralus") or ctx.runner.exists("/etc/init.d/VRTSralus.init"):
        found.append("Backup Exec")
    found += [t for t in BACKUP_TOOLS if ctx.has(t)]
    jobs = ctx.cfg.sections("backup", "job:")
    if jobs:
        found.append("%d configured job(s)" % len(jobs))
    return (True, "found: %s" % ", ".join(found)) if found else (False, "no backup product, tool or job found")


def _certificates(ctx):
    c = lambda key: ctx.cfg.getlist("certificates", "certificates", key, "")
    if c("files") or c("endpoints") or c("scan_dirs"):
        return True, "certificates are configured"
    return False, "no certificate files, folders or endpoints are configured"


def _containers(ctx):
    engines = [e for e in ("docker", "podman") if ctx.has(e)]
    return (True, "found: %s" % ", ".join(engines)) if engines else (False, "neither docker nor podman is installed")


def _cluster(ctx):
    if ctx.has("crm_mon") or ctx.has("pcs"):
        return True, "Pacemaker tools are installed"
    return False, "no Pacemaker cluster software"


def _applications(ctx):
    if ctx.cfg.getlist("main", "applications", "http_checks", ""):
        return True, "application URLs are configured"
    if ctx.has("postqueue"):
        return True, "a mail queue (postfix) is present"
    return False, "no application URLs configured and no mail queue"


def _custom(ctx):
    d = ctx.cfg.get("custom", "general", "checks_dir", "")
    if ctx.cfg.sections("custom", "check:") or (d and ctx.runner.isdir(d)):
        return True, "custom checks are defined"
    return False, "no custom checks defined"


DETECTORS = {"kvm": _kvm, "cockpit": _cockpit, "database": _database, "backup": _backup, "certificates": _certificates,
             "containers": _containers, "cluster": _cluster, "applications": _applications, "custom": _custom}


def detect(ctx):
    out = {}
    for name in modreg.MODULE_ORDER:
        title = modreg.load(name).TITLE
        if name in ALWAYS:
            out[name] = {"title": title, "present": True, "reason": ALWAYS[name], "always": True}
            continue
        try:
            present, why = DETECTORS[name](ctx)
        except Exception as e:  # a failing probe must never stop the scan: treat the module as present
            present, why = True, "could not be checked (%s), so it stays on" % e
        out[name] = {"title": title, "present": bool(present), "reason": why, "always": False}
    return out


# --- facts about the server --------------------------------------------------------------------------
def mounted_filesystems(ctx):
    mounts = set()
    for line in ctx.read("/proc/mounts").splitlines():
        p = line.split()
        if len(p) >= 3 and p[2] not in PSEUDO_FS and not p[1].startswith(("/proc", "/sys", "/dev/", "/run/user", "/var/lib/containers", "/var/lib/docker")):
            mounts.add(p[1].replace("\\040", " "))
    return sorted(mounts)


def inventory(ctx):
    osr = {}
    for line in ctx.read("/etc/os-release").splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            osr[k] = v.strip().strip('"')
    mem_kb = 0
    for line in ctx.read("/proc/meminfo").splitlines():
        if line.startswith("MemTotal:"):
            mem_kb = int(line.split()[1])
            break
    virt = ""
    if ctx.has("systemd-detect-virt"):
        virt = (ctx.run(["systemd-detect-virt"]).out or "").strip()
    if not virt or virt == "none":
        virt = (ctx.read("/sys/class/dmi/id/product_name") or "").strip() or "physical or unknown"
    try:
        engines = database_engines(ctx)
    except Exception:
        engines = []
    return {"hostname": ctx.hostname, "os": osr.get("PRETTY_NAME", ""), "kernel": os.uname()[2] if hasattr(os, "uname") else "",
            "cpus": os.cpu_count() or 0, "memory_mb": int(mem_kb / 1024), "virtualization": virt,
            "filesystems": mounted_filesystems(ctx), "databases": engines,
            "container_engines": [e for e in ("docker", "podman") if ctx.has(e)], "systemd": os.path.isdir("/run/systemd/system")}


def scan(ctx):
    """Read-only. Returns the profile dict."""
    now = time.time()
    return {"scanned_at": now, "scanned_at_text": iso(now), "version": VERSION, "host": ctx.hostname,
            "inventory": inventory(ctx), "modules": detect(ctx)}


# --- profile storage, comparison, applying ---------------------------------------------------------------
def profile_path(paths):
    return os.path.join(paths.data_dir, "profile.json")


def load_profile(paths):
    try:
        with open(profile_path(paths), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def save_profile(paths, profile):
    path = profile_path(paths)
    os.makedirs(paths.data_dir, exist_ok=True)
    if os.path.exists(path):
        try:
            os.replace(path, path.replace(".json", ".previous.json"))
        except OSError:
            pass
    with open(path, "w", encoding="utf-8") as f:
        json.dump(profile, f, indent=1, sort_keys=True)
    os.chmod(path, 0o640)


def compare(old, new):
    """What changed since the previous scan, as readable lines (empty on the first scan)."""
    if not old:
        return []
    out = []
    for name, m in new["modules"].items():
        was = old.get("modules", {}).get(name)
        if was is not None and was.get("present") != m["present"]:
            out.append("%s: %s (%s)" % (m["title"], "now present - will be checked" if m["present"] else "no longer present - left out", m["reason"]))
    a, b = old.get("inventory", {}), new["inventory"]
    for key, label in (("databases", "database engine"), ("container_engines", "container engine"), ("filesystems", "filesystem")):
        before, after = set(a.get(key, [])), set(b.get(key, []))
        out += ["New %s: %s" % (label, x) for x in sorted(after - before)]
        out += ["Gone %s: %s" % (label, x) for x in sorted(before - after)]
    return out


def excluded_modules(profile):
    return [n for n in modreg.MODULE_ORDER if not profile["modules"].get(n, {}).get("present", True)]


def apply(app, profile):
    """Store the profile and update which modules are left out. Returns (changes, excluded)."""
    old = load_profile(app.paths)
    changes = compare(old, profile)
    excluded = excluded_modules(profile)
    if as_list(app.cfg.get("main", "modules", "auto_excluded", "")) != excluded:
        app.cfg.set("main", "modules", "auto_excluded", ",".join(excluded))
        app.save_config("main")
        app.reload()
    save_profile(app.paths, profile)
    app.audit("server-scan", excluded=excluded, changes=changes)
    return changes, excluded


def rows(profile):
    """Table rows for display: [module, status, why]."""
    out = []
    for name in modreg.MODULE_ORDER:
        m = profile["modules"].get(name)
        if m:
            out.append([m["title"], "checked" if m["present"] else "not present", m["reason"]])
    return out
