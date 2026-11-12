"""Operating system identity, uptime, time synchronisation and reboot state."""
import platform
import re
import socket
import time

from lshme.core.util import fmt_duration, parse_kv_lines, primary_ip

NAME = "system"
TITLE = "Operating System"
WEIGHT = 8


def os_release(ctx):
    d = {}
    for line in ctx.read("/etc/os-release").splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            d[k.strip()] = v.strip().strip('"')
    if not d:
        rh = ctx.read("/etc/redhat-release")
        if rh:
            d["PRETTY_NAME"] = rh
            m = re.search(r"release (\d+(\.\d+)?)", rh)
            d["VERSION_ID"] = m.group(1) if m else ""
            d["ID"] = "rhel"
    return d


def time_sync(ctx):
    """Return (synchronized: True/False/None, source, detail)."""
    if ctx.has("timedatectl"):
        r = ctx.run(["timedatectl", "show"])
        if r.ok and r.out:
            kv = parse_kv_lines(r.out)
            synced = kv.get("NTPSynchronized")
            return ({"yes": True, "no": False}.get(synced), "timedatectl", kv.get("Timezone", ""))
        r = ctx.run(["timedatectl", "status"])  # RHEL 7 has no "show"
        if r.ok:
            m = re.search(r"(NTP synchronized|System clock synchronized):\s*(\w+)", r.out)
            tz = re.search(r"Time zone:\s*(\S+)", r.out)
            if m:
                return (m.group(2) == "yes", "timedatectl", tz.group(1) if tz else "")
    if ctx.has("chronyc"):
        r = ctx.run(["chronyc", "tracking"])
        if r.ok:
            st = re.search(r"Leap status\s*:\s*(.+)", r.out)
            return (bool(st and "Normal" in st.group(1)), "chronyc", "")
    if ctx.has("ntpstat"):
        r = ctx.run(["ntpstat"])
        if not r.missing:
            return (r.rc == 0, "ntpstat", "")
    return (None, "unavailable", "")


def reboot_required(ctx):
    if ctx.runner.exists("/var/run/reboot-required"):
        return True, ctx.read("/var/run/reboot-required.pkgs").replace("\n", ", ")
    if ctx.has("needs-restarting"):
        r = ctx.run(["needs-restarting", "-r"], timeout=60)
        if not r.missing and not r.timed_out and r.rc in (0, 1):
            return r.rc == 1, r.out.splitlines()[0] if r.out else ""
    return None, ""


def run(ctx, res):
    osr = os_release(ctx)
    uptime = float((ctx.read("/proc/uptime", "0").split() or ["0"])[0])
    boot = time.time() - uptime
    virt = ""
    if ctx.has("systemd-detect-virt"):
        r = ctx.run(["systemd-detect-virt"])
        virt = r.out.strip() if r.out else ("none" if r.rc == 1 else "")
    synced, tsource, tz = time_sync(ctx)
    reboot, reboot_detail = reboot_required(ctx)
    hostname = socket.gethostname()
    data = {
        "hostname": hostname, "fqdn": socket.getfqdn(), "ip": primary_ip(),
        "distribution": osr.get("PRETTY_NAME", platform.platform()), "distribution_id": osr.get("ID", ""),
        "version": osr.get("VERSION_ID", ""), "kernel": platform.release(),
        "architecture": platform.machine(), "python": platform.python_version(),
        "uptime_seconds": int(uptime), "uptime": fmt_duration(uptime),
        "boot_time": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(boot)),
        "timezone": tz or time.strftime("%Z"), "time_sync": synced, "time_sync_source": tsource,
        "virtualization": virt or "unknown", "reboot_required": reboot, "reboot_detail": reboot_detail,
        "selinux": ctx.read("/sys/fs/selinux/enforce", ""),
    }
    res.data = data
    res.metric("system.uptime_days", uptime / 86400.0)
    res.summary = "%s, kernel %s, up %s" % (data["distribution"], data["kernel"], data["uptime"])
    res.table("System identity", ["Item", "Value"], [
        ["Hostname", data["fqdn"]], ["IP address", data["ip"]], ["Operating system", data["distribution"]],
        ["Kernel", data["kernel"]], ["Architecture", data["architecture"]],
        ["Virtualization", data["virtualization"]], ["Uptime", data["uptime"]],
        ["Last boot", data["boot_time"]], ["Timezone", data["timezone"]],
        ["Time synchronized", {True: "yes", False: "NO", None: "unknown"}[synced] + " (%s)" % tsource],
        ["Reboot required", {True: "YES", False: "no", None: "unknown"}[reboot]],
    ], kind="summary")

    if synced is False:
        res.add("WARNING", "TIME_NOT_SYNCED", "time", "System clock is not synchronised (%s)" % tsource,
                recommendation="Check chronyd/ntpd configuration and reachability of NTP servers.")
    elif synced is None:
        res.add("UNKNOWN", "TIME_SYNC_UNKNOWN", "time", "Time synchronisation state could not be determined")
    if reboot:
        res.add("WARNING", "REBOOT_REQUIRED", "kernel", "A reboot is pending to activate updates",
                evidence=reboot_detail,
                recommendation="Plan a maintenance window to reboot and activate the updated kernel/libraries.")
    if uptime < 86400:
        res.add("INFO", "RECENT_REBOOT", "uptime", "Server was rebooted %s ago" % fmt_duration(uptime),
                recommendation="Confirm the reboot was planned; review logs if not.")
