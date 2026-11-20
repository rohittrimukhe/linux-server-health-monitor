# Linux Server Health Monitor Enterprise
# Copyright (c) 2026 LRS Services Pvt Ltd, Mumbai. All rights reserved.
"""Asset inventory for CMDB updates and audits.

Collects hardware, operating system, disks, filesystems, network interfaces,
installed packages, databases, virtual machines and listening services using
cheap read-only commands, and exports XLSX/CSV/JSON for one server or (via
SSH, like fleet monitoring) for every server in servers.conf.
"""
import csv
import json
import os
import re
import socket
import time

from lshme import APP_NAME, VERSION
from lshme.core.model import Table
from lshme.core.util import fmt_bytes, listening_ports, parse_pairs, primary_ip, to_int

SHEETS = ("Servers", "Disks", "Filesystems", "Network", "Packages", "Databases", "VMs", "Services")


def _read(runner, path):
    return runner.read(path, "").strip()


def collect(runner):
    """Return {"host": ..., sheet: [rows as dicts]} for this server."""
    from lshme.modules.system import os_release

    class _Ctx(object):  # minimal context for helpers that only need run/read/has
        def __init__(self, r):
            self.runner = r

        def read(self, p, d=""):
            return self.runner.read(p, d)

        def run(self, a, **kw):
            return self.runner.run(a, **kw)

        def has(self, n):
            return self.runner.has(n)
    ctx = _Ctx(runner)
    host = socket.gethostname()
    osr = os_release(ctx)
    cpuinfo = runner.read("/proc/cpuinfo")
    model = next((l.split(":", 1)[1].strip() for l in cpuinfo.splitlines() if l.startswith("model name")), "")
    sockets = len(set(l.split(":", 1)[1].strip() for l in cpuinfo.splitlines() if l.startswith("physical id"))) or 1
    cores = sum(1 for l in cpuinfo.splitlines() if l.startswith("processor"))
    mem_kb = to_int((re.search(r"MemTotal:\s+(\d+)", runner.read("/proc/meminfo")) or [None, "0"])[1], 0)
    dmi = lambda k: _read(runner, "/sys/class/dmi/id/" + k)
    virt = runner.run(["systemd-detect-virt"]).out.strip() if runner.has("systemd-detect-virt") else ""
    pkg_tool, packages = "", []
    if runner.has("rpm"):
        pkg_tool = "rpm"
        r = runner.run(["rpm", "-qa", "--qf", "%{NAME}\t%{VERSION}-%{RELEASE}\t%{ARCH}\t%{VENDOR}\t%{INSTALLTIME}\n"], timeout=120)
        for line in r.lines():
            p = (line.split("\t") + [""] * 5)[:5]
            packages.append({"host": host, "package": p[0], "version": p[1], "arch": p[2], "vendor": p[3],
                             "installed": time.strftime("%Y-%m-%d", time.localtime(to_int(p[4], 0))) if to_int(p[4]) else ""})
    elif runner.has("dpkg-query"):
        pkg_tool = "dpkg"
        r = runner.run(["dpkg-query", "-W", "-f", "${Package}\t${Version}\t${Architecture}\t${Maintainer}\t${db:Status-Abbrev}\n"], timeout=120)
        for line in r.lines():
            p = (line.split("\t") + [""] * 5)[:5]
            if p[4].startswith("ii"):
                packages.append({"host": host, "package": p[0], "version": p[1], "arch": p[2], "vendor": p[3][:60], "installed": ""})
    disks = []
    r = runner.run(["lsblk", "-P", "-b", "-d", "-o", "NAME,TYPE,SIZE,MODEL,SERIAL,ROTA,TRAN,VENDOR"])
    for line in r.lines():
        d = parse_pairs(line)
        if d.get("TYPE") in ("disk", "mpath") and to_int(d.get("SIZE"), 0) > 0:
            disks.append({"host": host, "device": d.get("NAME", ""), "size": fmt_bytes(to_int(d.get("SIZE"), 0)), "size_bytes": to_int(d.get("SIZE"), 0),
                          "model": d.get("MODEL", "").strip(), "vendor": d.get("VENDOR", "").strip(), "serial": d.get("SERIAL", ""),
                          "type": "HDD" if d.get("ROTA") == "1" else "SSD/virtual", "transport": d.get("TRAN", "")})
    filesystems = []
    r = runner.run(["df", "-P", "-T", "-B1", "-x", "tmpfs", "-x", "devtmpfs", "-x", "squashfs", "-x", "overlay"], timeout=45)
    for line in r.out.splitlines()[1:]:
        p = line.split()
        if len(p) >= 7:
            filesystems.append({"host": host, "mountpoint": " ".join(p[6:]), "device": p[0], "type": p[1],
                                "size": fmt_bytes(to_int(p[2], 0)), "used": fmt_bytes(to_int(p[3], 0)), "use_percent": p[5]})
    network = []
    addrs = {}
    for line in runner.run(["ip", "-o", "addr", "show"]).lines():
        p = line.split()
        if len(p) >= 4 and p[2] in ("inet", "inet6"):
            addrs.setdefault(p[1].rstrip(":"), []).append(p[3])
    for name in runner.listdir("/sys/class/net"):
        if name == "lo":
            continue
        b = "/sys/class/net/" + name
        network.append({"host": host, "interface": name, "mac": _read(runner, b + "/address"), "state": _read(runner, b + "/operstate"),
                        "speed_mbps": _read(runner, b + "/speed"), "mtu": _read(runner, b + "/mtu"),
                        "addresses": ", ".join(addrs.get(name, [])), "physical": "yes" if runner.exists(b + "/device") else "no"})
    databases = []
    from lshme.modules.database.oracle import discover as ora_discover
    found, homes = ora_discover(ctx)
    for sid, inf in found.items():
        home = inf.get("oracle_home", "")
        m = re.search(r"/(\d+(\.\d+)+)", home)
        databases.append({"host": host, "engine": "Oracle", "instance": sid, "version": m.group(1) if m else "",
                          "home_or_path": home, "owner": inf.get("os_user", "")})
    for sid, home in homes.items():
        if sid not in found and not sid.startswith("+"):
            databases.append({"host": host, "engine": "Oracle (not running)", "instance": sid, "version": "", "home_or_path": home, "owner": ""})
    ps = runner.run(["ps", "-eo", "user,args"]).out
    for engine, rx, ver_cmd in (("PostgreSQL", r"bin/postgres|postmaster", ["psql", "--version"]),
                                ("MySQL/MariaDB", r"\b(mysqld|mariadbd)\b", ["mysql", "--version"]),
                                ("MongoDB", r"\bmongod\b", ["mongod", "--version"]),
                                ("SQL Server", r"sqlservr", None)):
        if re.search(rx, ps):
            ver = ""
            if ver_cmd and runner.has(ver_cmd[0]):
                out = runner.run(ver_cmd).out
                # MariaDB's client prints "Ver 15.1 Distrib 10.11.14-MariaDB": the server version is after Distrib
                m = re.search(r"Distrib (\d+\.\d+\.\d+)", out) or re.search(r"(\d+\.\d+(\.\d+)?)", out)
                ver = m.group(1) if m else ""
            procs = [l for l in ps.splitlines() if re.search(rx, l) and not re.search(r"\b(runuser|sudo|su)\b", l)]
            user = procs[0].split()[0] if procs else ""
            databases.append({"host": host, "engine": engine, "instance": "local", "version": ver, "home_or_path": "", "owner": user})
    vms = []
    if runner.has("virsh"):
        r = runner.run(["virsh", "-c", "qemu:///system", "list", "--all", "--name"], timeout=30)
        for name in [x.strip() for x in r.lines() if x.strip()]:
            info = dict((k.strip(), v.strip()) for k, _, v in (l.partition(":") for l in runner.run(
                ["virsh", "-c", "qemu:///system", "dominfo", name]).lines()) if v)
            vms.append({"host": host, "vm": name, "state": info.get("State", ""), "vcpus": info.get("CPU(s)", ""),
                        "memory": info.get("Max memory", ""), "autostart": info.get("Autostart", ""), "uuid": info.get("UUID", "")})
    services = [{"host": host, "proto": p["proto"], "port": p["port"], "address": p["address"], "process": p["process"]}
                for p in sorted(listening_ports(runner), key=lambda x: (x["port"], x["proto"]))]
    server = {"host": host, "fqdn": socket.getfqdn(), "ip": primary_ip(), "os": osr.get("PRETTY_NAME", ""), "kernel": os.uname()[2],
              "architecture": os.uname()[4], "manufacturer": dmi("sys_vendor"), "model": dmi("product_name"),
              "serial": dmi("product_serial"), "bios": ("%s %s" % (dmi("bios_vendor"), dmi("bios_version"))).strip(),
              "virtualization": virt or "none", "cpu_model": model, "sockets": sockets, "logical_cpus": cores,
              "memory": fmt_bytes(mem_kb * 1024), "disks": len(disks), "disk_total": fmt_bytes(sum(d["size_bytes"] for d in disks)),
              "packages": len(packages), "package_tool": pkg_tool, "databases": len(databases), "vms": len(vms),
              "uptime_days": round(float((runner.read("/proc/uptime", "0").split() or ["0"])[0]) / 86400, 1),
              "collected": time.strftime("%Y-%m-%d %H:%M:%S")}
    for d in disks:
        d.pop("size_bytes", None)
    return {"host": host, "version": VERSION, "Servers": [server], "Disks": disks, "Filesystems": filesystems, "Network": network,
            "Packages": packages, "Databases": databases, "VMs": vms, "Services": services}


def merge(inventories):
    out = dict((s, []) for s in SHEETS)
    for inv in inventories:
        for s in SHEETS:
            out[s].extend(inv.get(s, []))
    return out


def as_doc(inv, cfg, title="Asset Inventory", errors=None):
    """Turn an inventory into a ReportDoc so the standard renderers (xlsx, html, pdf, txt) can be used."""
    from lshme.reports.doc import ReportDoc, Section
    d = ReportDoc()
    d.kind = "inventory"
    d.report_type = "inventory"
    d.title = title
    hosts = [s["host"] for s in inv.get("Servers", [])]
    d.host = hosts[0] if len(hosts) == 1 else ""
    d.subtitle = hosts[0] if len(hosts) == 1 else "%d servers" % len(hosts)
    g = cfg.section("main", "general") if cfg else {}
    d.meta = [(k, v) for k, v in (("Company", g.get("company")), ("Customer", g.get("customer")), ("Servers", str(len(hosts))),
                                   ("Collected", time.strftime("%Y-%m-%d %H:%M")), ("Generated by", "%s v%s" % (APP_NAME, VERSION))) if v]
    d.health = {"score": len(hosts), "status": "HEALTHY", "method": "Inventory snapshot (no health scoring)."}
    d.overview_title = "Servers"
    d.overview_headers = ["Server", "OS", "Model", "CPUs", "Memory", "Packages / DBs / VMs"]
    d.chart_title = "Servers"
    d.module_rows = [[s["host"], s["os"], ("%s %s" % (s["manufacturer"], s["model"])).strip(), s["logical_cpus"], s["memory"],
                      "%s / %s / %s" % (s["packages"], s["databases"], s["vms"])] for s in inv.get("Servers", [])]
    for host, err in (errors or {}).items():
        d.module_rows.append([host, "UNREACHABLE", "", "", "", err[:80]])
    d.counts = {"CRITICAL": 0, "WARNING": 0, "UNKNOWN": len(errors or {}), "INFO": 0}
    for sheet in SHEETS:
        rows = inv.get(sheet, [])
        if not rows:
            continue
        headers = list(rows[0].keys())
        t = Table(sheet, [h.replace("_", " ").title() for h in headers], [[r.get(h, "") for h in headers] for r in rows], kind="summary")
        d.sections.append(Section(sheet.lower(), sheet, "OK", None, "%d record(s)" % len(rows), [], [t]))
    return d


def write_csv(inv, outdir, base):
    files = []
    for sheet in SHEETS:
        rows = inv.get(sheet, [])
        if not rows:
            continue
        path = os.path.join(outdir, "%s_%s.csv" % (base, sheet.lower()))
        with open(path, "w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)
        files.append(path)
    return files


def export(app, formats=("xlsx", "csv", "json"), fleet=False, servers=None):
    """Collect (locally or from the fleet) and write the requested formats. Returns (inventory, files, errors)."""
    from lshme import reports
    errors = {}
    if fleet:
        from lshme.ops import fleet as fleetmod
        invs = []
        for sv in fleetmod.servers(app.cfg):
            if servers and sv.name not in servers and sv.host not in servers:
                continue
            if sv.host in ("local", "localhost"):
                invs.append(collect(app.runner))
                continue
            cmd = app.cfg.get("servers", "defaults", "inventory_command", "/usr/local/bin/healthcheck inventory --json")
            ssh = fleetmod.ssh_command(sv, 1)[:-1] + [cmd]
            r = app.runner.run(ssh, timeout=sv.timeout)
            try:
                invs.append(json.loads(r.out[r.out.find("{"):]))
            except ValueError:
                errors[sv.name] = r.brief_error() if not r.ok else "no inventory JSON returned"
        inv = merge(invs)
        host, category = "_fleet", "inventory"
    else:
        inv = collect(app.runner)
        host, category = socket.gethostname(), "inventory"
    outdir = reports.output_dir(app.paths.report_dir, host, category)
    base = "%s_inventory_%s" % (reports.safe(host), time.strftime("%Y%m%d"))
    files = []
    if "json" in formats:
        path = os.path.join(outdir, base + ".json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(inv, f, indent=1)
        files.append(path)
    if "csv" in formats:
        files += write_csv(inv, outdir, base)
    other = [f for f in formats if f in ("xlsx", "html", "pdf", "txt")]
    if other:
        doc = as_doc(inv, app.cfg, "Asset Inventory" if not fleet else "Fleet Asset Inventory", errors)
        written, _ = reports.write_all(doc, other, outdir, base)
        files += written
    return inv, files, errors
