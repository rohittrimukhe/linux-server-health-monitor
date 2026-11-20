# Linux Server Health Monitor Enterprise
# Copyright (c) 2026 Rohit Santosh Trimukhe. All rights reserved.
"""Plain-English parts of the daily, weekly and monthly reports: the short summary, what each problem means,
CPU by core, and the server inventory (roles, services, hardware, software) of the comprehensive monthly report."""
import json
import os

from lshme.core.util import fmt_bytes, fmt_duration
from lshme.reports import periodic as P
from lshme.reports.periodic import Bar, Cell, bucket, chip, day_key, fmt_delta, level_for, stamp, stats, table, text_of

from lshme.reports.periodic import IMPACT, impact_for_module  # noqa: E402,F401


# -- CPU by core ------------------------------------------------------------------------
def core_usage(data):
    """{core index: {'avg': pts, 'max': pts, 'all': pts}} from the watcher, or None."""
    out = {}
    if data.days <= 1 and data.paths:
        try:
            with open(os.path.join(data.paths.data_dir, "watch_metrics.json"), encoding="utf-8") as f:
                raw = [p for p in json.load(f) if p.get("t", 0) >= data.since and p.get("cores")]
            for p in raw:
                for i, v in enumerate(p["cores"]):
                    if v is not None:
                        out.setdefault(i, []).append((p["t"], float(v)))
            if out:
                return dict((i, {"avg": bucket(pts, 288, "avg"), "max": bucket(pts, 288, "max"), "all": pts}) for i, pts in out.items())
        except (IOError, OSError, ValueError, KeyError, TypeError):
            pass
    if data.hist:
        try:
            rows = data.hist.hourly_cores(data.host, data.since)
        except Exception:
            rows = {}
        for i, hrs in rows.items():
            out[i] = {"avg": bucket([(t + 1800, a) for t, a, _ in hrs], 400, "avg"), "max": bucket([(t + 1800, m) for t, _, m in hrs], 400, "max"),
                      "all": [(t + 1800, a) for t, a, _ in hrs]}
    return out or None


def cores_blocks(data, cores):
    """A table with every CPU core and a chart with one line per core."""
    if not cores:
        return [P.note("Per-core figures appear after the real-time watcher has been running for a while.")]
    warn, crit = data.cfg.threshold("cpu", "warning", 85), data.cfg.threshold("cpu", "critical", 95)
    rows = []
    for i in sorted(cores):
        s, pk = stats(cores[i]["all"]), stats(cores[i]["max"]) or stats(cores[i]["all"])
        lv = level_for(pk["max"], warn, crit)
        rows.append([Cell("Core %d" % i, "b"), "%.0f %%" % s["last"], "%.0f %%" % s["avg"], Cell("%.0f %%" % pk["max"], lv), stamp(pk["max_t"]),
                     Bar(s["avg"], warn, crit), P.chip(lv)])
    blocks = [table("CPU usage of every core (%d cores)" % len(cores), ["Core", "Now", "Average", "Busiest", "Busiest at", "Average usage", "Status"], rows,
                    align=["", "r", "r", "r", "", "", ""],
                    legend="Each core is one processor thread. A single core at 100 % while the others are idle usually means one program that cannot use more than one core.")]
    show = sorted(cores, key=lambda i: -(stats(cores[i]["all"])["avg"]))[:12]
    palette = ["#0b5ed7", "#d9730d", "#1e8e3e", "#7b3fe4", "#c5221f", "#0d8a72", "#b8860b", "#c2185b", "#5d4037", "#00838f", "#6a1b9a", "#455a64"]
    block = {"title": "CPU used by core" + ("" if len(show) == len(cores) else " (12 busiest)"), "unit": "%", "color": "#0b5ed7", "warn": warn, "crit": crit, "max": 100,
             "multi": True, "colors": palette[:len(show)],
             "series": [{"name": "Core %d" % i, "pts": [[int(t), round(v, 1)] for t, v in cores[i]["avg"]]} for i in show]}
    blocks.append({"type": "wide_charts", "items": [block]})
    return blocks


# -- plain English summary ---------------------------------------------------------------
def narrative(data, usage, fs, dbs, fo, levels, name):
    pts = []
    total, bad = len(levels), sum(1 for l in levels if l != "ok")
    worst_l = "cr" if "cr" in levels else "wn" if "wn" in levels else "ok"
    if worst_l == "ok":
        pts.append(("ok", "%s is healthy. All %d checks are within their limits." % (name, total)))
    else:
        pts.append((worst_l, "%s needs attention. %d of %d checks are outside their limits." % (name, bad, total)))
    per = {"daily": "in the last 24 hours", "weekly": "in the last 7 days", "monthly": "in the last 30 days"}[data_type(data)]
    cpu, mem = stats(usage.get("cpu", {}).get("all") or []), stats(usage.get("mem", {}).get("all") or [])
    cpk = stats(usage.get("cpu", {}).get("max") or [])
    if cpu:
        t = data.cfg.threshold("cpu", "warning", 85)
        peak = cpk or cpu
        pts.append((level_for(peak["max"], t, data.cfg.threshold("cpu", "critical", 95)),
                    "CPU was busy %.0f %% on average %s. The busiest moment was %.0f %% at %s." % (cpu["avg"], per, peak["max"], stamp(peak["max_t"]))))
    if mem:
        mpk = stats(usage.get("mem", {}).get("max") or []) or mem
        pts.append((level_for(mpk["max"], data.cfg.threshold("memory", "warning", 85), data.cfg.threshold("memory", "critical", 95)),
                    "Memory use averaged %.0f %% and reached %.0f %% at its highest." % (mem["avg"], mpk["max"])))
    if fs:
        top = fs[0]
        it = P.growth_item(data, "fs.used_bytes:" + top["mountpoint"], top["size"])
        lv = level_for(top["used_percent"], data.cfg.threshold("disk", "warning", 80), data.cfg.threshold("disk", "critical", 90))
        text = "The fullest disk is %s at %.0f %%, with %s free." % (top["mountpoint"], top["used_percent"], fmt_bytes(top["free"]))
        if it and it.get("d1") is not None and abs(it["d1"]) >= 1024 * 1024:
            text += " It %s %s in the last 24 hours." % ("grew by" if it["d1"] > 0 else "shrank by", fmt_bytes(abs(it["d1"])))
        if it and it.get("forecast_ok") and it["full_days"] is not None and it["full_days"] < 365:
            text += " At this speed it will be full in about %d days." % max(1, round(it["full_days"]))
            if it["full_days"] <= 30 and lv == "ok":
                lv = "wn"
        pts.append((lv, text))
    if dbs:
        grew = [(x["name"], x["item"]["d1"]) for x in dbs if x["item"]["d1"] is not None]
        if grew:
            big = max(grew, key=lambda g: g[1])
            tot = sum(g[1] for g in grew)
            pts.append(("ok", "The %d database%s grew by %s in the last 24 hours. The biggest growth was %s (%s)." % (
                len(dbs), "" if len(dbs) == 1 else "s", fmt_bytes(max(tot, 0)), big[0], fmt_bytes(max(big[1], 0)))))
        else:
            pts.append(("ok", "%d database%s found. Growth figures need at least a day of history." % (len(dbs), "" if len(dbs) == 1 else "s")))
    if fo:
        x = fo[0]
        d1 = x["item"]["d1"]
        pts.append(("ok", "The biggest folder is %s at %s%s." % (x["path"], fmt_bytes(x["item"]["now"]),
                                                               (" (%s%s in 24 hours)" % ("+" if d1 > 0 else "-", fmt_bytes(abs(d1)))) if d1 is not None and abs(d1) >= 1024 * 1024 else "")))
    return {"type": "points", "items": pts}


def data_type(data):
    return "daily" if data.days <= 1 else "weekly" if data.days <= 7 else "monthly"


# -- server inventory (comprehensive monthly report) -------------------------------------
ROLE_BY_PROCESS = (("sshd", "Remote administration (SSH)"), ("httpd", "Web server (Apache)"), ("nginx", "Web server (nginx)"),
                   ("postgres", "PostgreSQL database server"), ("tnslsnr", "Oracle database listener"), ("oracle", "Oracle database server"),
                   ("mysqld", "MySQL / MariaDB database server"), ("mariadbd", "MariaDB database server"), ("mongod", "MongoDB database server"),
                   ("sqlservr", "SQL Server database"), ("cockpit", "Cockpit web console"), ("libvirtd", "Virtualisation host (KVM)"),
                   ("virtqemud", "Virtualisation host (KVM)"), ("dockerd", "Container host (Docker)"), ("conmon", "Container host (Podman)"),
                   ("named", "DNS server"), ("dnsmasq", "DNS / DHCP server"), ("master", "Mail server (Postfix)"), ("nfsd", "File server (NFS)"),
                   ("smbd", "File server (Samba)"), ("haproxy", "Load balancer"), ("chronyd", "Time synchronisation"), ("ntpd", "Time synchronisation"),
                   ("nbdisco", "Backup client (NetBackup)"), ("bpcd", "Backup client (NetBackup)"), ("veeam", "Backup client (Veeam)"),
                   ("pacemaker", "Cluster node (Pacemaker)"), ("corosync", "Cluster node (Corosync)"), ("zabbix", "Monitoring agent (Zabbix)"),
                   ("node_exporter", "Monitoring agent (Prometheus)"), ("snmpd", "Monitoring agent (SNMP)"), ("rsyslogd", "Central logging"))


def roles(inv):
    found = {}
    for s in inv.get("Services", []):
        proc = (s.get("process") or "").lower()
        for key, role in ROLE_BY_PROCESS:
            if key in proc:
                found.setdefault(role, set()).add("%s port %s" % (s.get("proto", ""), s.get("port", "")))
    for d in inv.get("Databases", []):
        found.setdefault("%s database server" % d["engine"].split(" (")[0], set()).add("instance %s %s" % (d["instance"], d.get("version", "")))
    if inv.get("VMs"):
        found.setdefault("Virtualisation host (KVM)", set()).add("%d virtual machine(s)" % len(inv["VMs"]))
    return [[Cell(r, "b"), ", ".join(sorted(ev))[:120]] for r, ev in sorted(found.items())]


def inventory_sections(doc, cfg):
    """Add roles, hardware, disks, network, services, databases, VMs and software sections to a comprehensive report."""
    try:
        from lshme.core.runner import Runner
        from lshme.ops import inventory
        runner = Runner(cfg.getint("main", "general", "command_timeout", 30))
        inv = inventory.collect(runner)
    except Exception as e:
        doc.add("Server inventory", [P.note("The inventory could not be collected: %s" % e)])
        return
    srv = (inv.get("Servers") or [{}])[0]
    rows = roles(inv)
    doc.add("Server roles", [P.note("What this server is used for, worked out from the programs that are running and listening on the network."),
                             table("Roles found", ["Role", "Seen from"], rows or [["General purpose Linux server", ""]])])
    hw = [["Manufacturer", srv.get("manufacturer", ""), "Model", srv.get("model", "")],
          ["Serial number", srv.get("serial", ""), "BIOS", srv.get("bios", "")],
          ["Processor", srv.get("cpu_model", ""), "Sockets / threads", "%s / %s" % (srv.get("sockets", ""), srv.get("logical_cpus", ""))],
          ["Memory", srv.get("memory", ""), "Disks", "%s disks, %s" % (srv.get("disks", ""), srv.get("disk_total", ""))],
          ["Operating system", srv.get("os", ""), "Kernel", srv.get("kernel", "")],
          ["Virtualisation", srv.get("virtualization", ""), "Uptime", "%s days" % srv.get("uptime_days", "")]]
    blocks = [{"type": "kv", "rows": hw}]
    if inv.get("Disks"):
        blocks.append(table("Physical and virtual disks", ["Device", "Size", "Model", "Type", "Connection"],
                            [[d["device"], d["size"], d["model"] or d["vendor"], d["type"], d["transport"]] for d in inv["Disks"]]))
    if inv.get("Filesystems"):
        blocks.append(table("Filesystems", ["Mount point", "Device", "Type", "Size", "Used", "Use"],
                            [[Cell(f["mountpoint"], "b"), f["device"], f["type"], f["size"], f["used"], f["use_percent"]] for f in inv["Filesystems"]],
                            align=["", "", "", "r", "r", "r"]))
    doc.add("Server inventory: hardware and storage", blocks)
    if inv.get("Network"):
        doc.add("Network interfaces", [table("Network interfaces", ["Interface", "State", "Speed (Mb/s)", "Addresses", "MAC address", "Physical"],
                                            [[Cell(n["interface"], "b"), chip("ok" if n["state"] == "up" else "wn") if n["state"] in ("up", "down") else n["state"],
                                              n["speed_mbps"], n["addresses"], n["mac"], n["physical"]] for n in inv["Network"]])])
    svc = []
    try:
        r = runner.run(["systemctl", "list-units", "--type=service", "--state=running", "--no-legend", "--no-pager"], timeout=30)
        for line in r.lines():
            p = line.split(None, 4)
            if len(p) >= 4:
                svc.append([Cell(p[0].replace(".service", ""), "b"), p[3], (p[4] if len(p) > 4 else "")[:70]])
    except Exception:
        pass
    blocks = []
    if svc:
        blocks.append(table("Running services (%d)" % len(svc), ["Service", "State", "Description"], svc))
    if inv.get("Services"):
        blocks.append(table("Programs listening on the network (%d)" % len(inv["Services"]), ["Protocol", "Port", "Address", "Program"],
                            [[s["proto"], Cell(str(s["port"]), "b"), s["address"], s["process"]] for s in inv["Services"]]))
    if blocks:
        doc.add("Services and open ports", blocks)
    blocks = []
    if inv.get("Databases"):
        blocks.append(table("Database engines", ["Engine", "Instance", "Version", "Location", "Owner"],
                            [[Cell(d["engine"], "b"), d["instance"], d["version"], d["home_or_path"], d["owner"]] for d in inv["Databases"]]))
    if inv.get("VMs"):
        blocks.append(table("Virtual machines", ["Name", "State", "vCPUs", "Memory", "Starts automatically"],
                            [[Cell(v["vm"], "b"), v["state"], v["vcpus"], v["memory"], v["autostart"]] for v in inv["VMs"]]))
    if blocks:
        doc.add("Databases and virtual machines", blocks)
    pk = inv.get("Packages", [])
    if pk:
        newest = sorted(pk, key=lambda p: p.get("installed", ""), reverse=True)[:40]
        full = table("All installed software (%d packages)" % len(pk), ["Package", "Version", "Architecture", "Vendor", "Installed"],
                     [[p["package"], p["version"], p["arch"], p["vendor"][:40], p["installed"]] for p in sorted(pk, key=lambda p: p["package"])])
        full["excel_only"] = True
        doc.add("Installed software", [P.note("%d packages are installed (%s). The newest are listed here; the Excel file lists them all." % (len(pk), srv.get("package_tool", ""))),
                                       table("Most recently installed", ["Package", "Version", "Installed"],
                                             [[p["package"], p["version"], p["installed"]] for p in newest]), full])
