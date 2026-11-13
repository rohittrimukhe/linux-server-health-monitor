# Linux Server Health Monitor Enterprise
# Copyright (c) 2026 LRS Services Pvt Ltd, Mumbai. All rights reserved.
"""Self-diagnostics: dependencies, permissions, configuration and integrations."""
import os
import sqlite3
import sys

from lshme.delivery.mailer import MailSettings
from lshme.ops import scheduler

TOOLS = {
    "core": ["df", "ps", "lsblk", "ip", "ss", "systemctl", "journalctl"],
    "storage": ["pvs", "vgs", "lvs", "multipath", "smartctl", "iostat"],
    "hardware": ["dmidecode", "sensors", "ipmitool", "storcli64", "perccli64", "MegaCli64", "ssacli"],
    "kvm": ["virsh"], "cockpit": ["openssl"],
    "database": ["sqlplus", "lsnrctl", "mysql", "psql", "mongosh", "sqlcmd"],
    "backup": ["bpclimagelist", "bpdbjobs", "veeamconfig"],
    "security": ["getenforce", "firewall-cmd", "sshd", "dnf", "yum", "apt-get"],
    "network": ["ping", "getent", "ethtool"], "cluster": ["pcs", "crm_mon"],
    "containers": ["docker", "podman"], "reports": ["openssl"], "fleet": ["ssh"],
}


def run(cfg, paths, runner):
    rows = []  # (area, check, status, detail)
    ok = lambda a, ch, d="": rows.append((a, ch, "OK", d))
    warn = lambda a, ch, d="": rows.append((a, ch, "WARNING", d))
    bad = lambda a, ch, d="": rows.append((a, ch, "CRITICAL", d))
    v = sys.version_info
    (ok if v >= (3, 6) else bad)("python", "Python version", "%d.%d.%d" % v[:3])
    (ok if runner.is_root() else warn)("python", "Running as root", "yes" if runner.is_root() else
                                       "no - some checks (shadow, sshd -T, smartctl, dmidecode, DB OS auth) will be skipped")
    for mod, pkg in (("reportlab", "python3-reportlab"), ("openpyxl", "python3-openpyxl"), ("pptx", "python3-pptx / pip install python-pptx")):
        try:
            __import__(mod)
            ok("reports", "Optional library %s" % mod, "installed")
        except ImportError:
            warn("reports", "Optional library %s" % mod, "missing - built-in fallback is used (%s)" % pkg)
    for e in cfg.load_errors:
        bad("config", "Config parse", e)
    for p in cfg.permission_problems():
        bad("config", "Secret file permissions", p)
    if not cfg.load_errors:
        ok("config", "Configuration files", cfg.config_dir)
    for d in (paths.data_dir, paths.report_dir, paths.log_dir):
        (ok if os.access(d, os.W_OK) else bad)("paths", "Writable %s" % d, "")
    try:
        con = sqlite3.connect(paths.history_db)
        con.execute("SELECT 1")
        con.close()
        ok("history", "SQLite history database", paths.history_db)
    except Exception as e:
        bad("history", "SQLite history database", str(e))
    for area, tools in TOOLS.items():
        present = [t for t in tools if runner.has(t)]
        missing = [t for t in tools if not runner.has(t)]
        rows.append(("tools", area, "OK" if present or not tools else "INFO",
                     "present: %s%s" % (", ".join(present) or "none", ("; not found: " + ", ".join(missing)) if missing else "")))
    ms = MailSettings(cfg)
    probs = ms.validate()
    (warn if probs else ok)("email", "SMTP settings", "; ".join(probs) if probs else "%s:%s %s -> %s" % (ms.host, ms.port, ms.security, ", ".join(ms.to)))
    try:
        be, sched = scheduler.status(cfg, runner)
        inst = [s["name"] for s in sched if s["installed"]]
        en = [s["name"] for s in sched if s["enabled"]]
        (ok if set(en) <= set(inst) else warn)("schedule", "Schedules (%s)" % be,
                                               "enabled: %s; installed: %s" % (", ".join(en) or "none", ", ".join(inst) or "none"))
    except Exception as e:
        warn("schedule", "Schedules", str(e))
    for s in scheduler.schedules(cfg):
        for e in s.validate():
            bad("schedule", "Schedule %s" % s.name, e)
    from lshme.ops import fleet
    svs = fleet.servers(cfg)
    if svs:
        (ok if runner.has("ssh") else bad)("fleet", "Fleet servers", "%d configured; ssh %s" % (len(svs), "available" if runner.has("ssh") else "missing"))
    return rows
