"""Service inventory, critical service policy, expected ports and processes."""
import re

from lshme.core.util import fmt_bytes, listening_ports, to_int

NAME = "services"
TITLE = "Services & Processes"
WEIGHT = 15

COMMON = ("sshd", "crond", "cron", "chronyd", "ntpd", "firewalld", "NetworkManager", "rsyslog",
          "auditd", "postfix", "httpd", "nginx", "multipathd", "libvirtd", "cockpit", "docker",
          "podman", "pacemaker", "corosync", "nfs-server", "smb", "named", "mariadb", "mysqld",
          "postgresql", "oracle-ohasd", "ohasd", "tuned", "kdump", "sssd", "snmpd", "zabbix-agent",
          "node_exporter", "VRTSralus.init", "netbackup", "vxpbx_exchanged", "nrpe", "splunkd")


def unit_name(name):
    return name if "." in name else name + ".service"


def systemd_units(ctx):
    r = ctx.run(["systemctl", "list-units", "--type=service", "--all", "--no-legend", "--no-pager", "--plain"])
    if not r.ok and "--plain" in r.err:
        r = ctx.run(["systemctl", "list-units", "--type=service", "--all", "--no-legend", "--no-pager"])
    units = []
    for line in r.lines():
        line = line.lstrip("● *").strip()
        p = line.split(None, 4)
        if len(p) >= 4 and p[0].endswith(".service"):
            units.append({"unit": p[0], "load": p[1], "active": p[2], "sub": p[3],
                          "description": p[4] if len(p) > 4 else ""})
    files = {}
    r = ctx.run(["systemctl", "list-unit-files", "--type=service", "--no-legend", "--no-pager"])
    for line in r.lines():
        p = line.split()
        if len(p) >= 2:
            files[p[0]] = p[1]
    for u in units:
        u["enabled"] = files.get(u["unit"], "")
    return units, files


def unit_detail(ctx, unit):
    props = "ActiveState,SubState,UnitFileState,MainPID,NRestarts,ActiveEnterTimestamp,MemoryCurrent,LoadState,Result"
    r = ctx.run(["systemctl", "show", unit, "-p", props, "--no-pager"])
    d = {}
    for line in r.lines():
        if "=" in line:
            k, v = line.split("=", 1)
            d[k] = v
    return d


def sysv_status(ctx, name):
    r = ctx.run(["service", name, "status"], timeout=20)
    return {"ActiveState": "active" if r.rc == 0 else ("unknown" if r.missing else "inactive"),
            "SubState": (r.out.splitlines() or [""])[0][:120], "LoadState": "sysv"}


def run(ctx, res):
    systemd = ctx.has("systemctl") and ctx.runner.isdir("/run/systemd/system")
    units, files = (systemd_units(ctx) if systemd else ([], {}))
    critical = ctx.cfg.getlist("services", "critical", "services", "sshd, crond")
    ignore = set(ctx.cfg.getlist("services", "ignore", "failed_units", ""))
    known = set(u["unit"] for u in units) | set(files)
    restart_warn = ctx.t("services", "restart_warning", 3)
    ports = listening_ports(ctx.runner)
    ctx.shared["listening_ports"] = ports
    listening = set(p["port"] for p in ports)

    crit_rows = []
    details = {}
    for name in critical:
        unit = unit_name(name)
        alt = {"crond.service": "cron.service", "cron.service": "crond.service",
               "sshd.service": "ssh.service", "ssh.service": "sshd.service"}.get(unit)
        if systemd and unit not in known and alt and alt in known:
            unit = alt
        if systemd:
            if unit not in known:
                crit_rows.append([name, "not installed", "", "", "", ""])
                res.add("CRITICAL", "CRITICAL_SERVICE_MISSING", name,
                        "Critical service %s is not installed/known to systemd" % name,
                        recommendation="Install the service or remove it from services.conf [critical].")
                continue
            d = unit_detail(ctx, unit)
        else:
            d = sysv_status(ctx, name)
        details[unit] = d
        restarts = to_int(d.get("NRestarts"), 0) or 0
        mem = to_int(d.get("MemoryCurrent"))
        crit_rows.append([unit, d.get("ActiveState", ""), d.get("SubState", ""), d.get("UnitFileState", ""),
                          restarts, fmt_bytes(mem) if mem and mem < 2 ** 62 else ""])
        if d.get("ActiveState") != "active":
            res.add("CRITICAL", "CRITICAL_SERVICE_DOWN", unit,
                    "Critical service %s is %s%s" % (unit, d.get("ActiveState", "?"), (" (%s)" % d["SubState"]) if d.get("SubState") else ""),
                    evidence="Result=%s" % d.get("Result", ""),
                    recommendation="Review 'journalctl -u %s' for the cause before restarting it manually." % unit)
        elif d.get("UnitFileState") in ("disabled", "masked"):
            res.add("WARNING", "CRITICAL_SERVICE_NOT_ENABLED", unit,
                    "Critical service %s is running but %s at boot" % (unit, d.get("UnitFileState")),
                    recommendation="Enable the service so it starts after a reboot.")
        if restarts >= restart_warn:
            res.add("WARNING", "SERVICE_RESTARTING", unit, "%s has restarted %d times" % (unit, restarts),
                    recommendation="Frequent restarts indicate crashes; check the service logs.")
    res.table("Critical services", ["Service", "Active", "Sub-state", "Enabled", "Restarts", "Memory"],
              crit_rows, kind="summary")

    failed = [u for u in units if u["active"] == "failed" and u["unit"] not in ignore
              and u["unit"].replace(".service", "") not in ignore]
    crit_units = set(unit_name(c) for c in critical)
    for u in failed:
        if u["unit"] in crit_units:
            continue  # already reported as CRITICAL_SERVICE_DOWN
        res.add("WARNING", "SERVICE_FAILED", u["unit"], "Service %s is in failed state" % u["unit"],
                evidence=u["description"],
                recommendation="Inspect 'systemctl status %s'; fix or reset-failed if obsolete." % u["unit"])
    present = [u for u in units if u["unit"].replace(".service", "") in COMMON]
    res.table("Common infrastructure services", ["Service", "Active", "Sub-state", "Enabled", "Description"],
              [[u["unit"], u["active"], u["sub"], u["enabled"], u["description"]] for u in present])
    res.table("All services", ["Service", "Load", "Active", "Sub-state", "Enabled"],
              [[u["unit"], u["load"], u["active"], u["sub"], u["enabled"]] for u in units])

    # expected listening ports
    port_rows = []
    for svc, port in ctx.cfg.section("services", "ports").items():
        p = to_int(port)
        if not p:
            continue
        ok = p in listening
        port_rows.append([svc, p, "listening" if ok else "NOT LISTENING"])
        if not ok:
            res.add("CRITICAL", "PORT_NOT_LISTENING", "%s:%d" % (svc, p), "Expected port %d (%s) is not listening" % (p, svc),
                    recommendation="Check that %s is running and bound to the expected address." % svc)
    if port_rows:
        res.table("Expected ports", ["Service", "Port", "State"], port_rows)
    res.table("Listening ports", ["Proto", "Address", "Port", "Process"],
              [[p["proto"], p["address"], p["port"], p["process"]] for p in sorted(ports, key=lambda x: x["port"])])

    # required processes
    proc_rows = []
    procs = ctx.cfg.section("services", "processes")
    if procs:
        ps = ctx.run(["ps", "-eo", "pid,args"]).out.splitlines()[1:]
        for label, pattern in procs.items():
            try:
                rx = re.compile(pattern)
            except re.error:
                rx = re.compile(re.escape(pattern))
            hits = [l.strip() for l in ps if rx.search(l) and "lshme" not in l]
            proc_rows.append([label, pattern, len(hits)])
            if not hits:
                res.add("CRITICAL", "PROCESS_MISSING", label, "Required process '%s' (%s) is not running" % (label, pattern),
                        recommendation="Start the application component or update services.conf [processes].")
        res.table("Required processes", ["Name", "Pattern", "Running"], proc_rows)

    res.data = {"init": "systemd" if systemd else "sysv/other", "units": units, "critical": details,
                "failed": [u["unit"] for u in failed], "listening_ports": ports}
    running = sum(1 for u in units if u["active"] == "active")
    res.metric("services.failed", len(failed))
    res.metric("services.running", running)
    res.summary = "%d services (%d running, %d failed), %d critical monitored" % (
        len(units), running, len(failed), len(critical))
    if not systemd:
        res.skip("systemd not detected; using SysV service status")
