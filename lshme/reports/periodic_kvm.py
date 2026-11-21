# Linux Server Health Monitor Enterprise
# Copyright (c) 2026 LRS Services Pvt Ltd, Mumbai. All rights reserved.
"""The KVM section of the daily, weekly and monthly reports.

Every report on a KVM host gets a short view: VMs on and off, vCPU and memory given to running VMs, the busiest and largest VMs
and the storage pools. The comprehensive report adds the full inventory: every VM and every disk with its size and growth, how long
each VM ran during the period and how often it stopped, host against VM use, snapshots, and charts of VM disk growth and VM CPU."""
from lshme.core.util import fmt_bytes
from lshme.reports.periodic import (Bar, Cell, charts, fmt_delta, growth_charts, growth_item, kpis, level_for, note, stamp, stats,
                                    table)

STATE = {"running": ("Running", "chip-ok"), "shut off": ("Off", "chip-na"), "paused": ("Paused", "chip-wn"), "pmsuspended": ("Suspended", "chip-wn"),
         "blocked": ("Blocked", "chip-wn"), "crashed": ("Crashed", "chip-cr"), "shutting down": ("Stopping", "chip-wn")}
COLORS = ["#0b5ed7", "#d9730d", "#1e8e3e", "#7b3fe4", "#c5221f", "#0d8a72"]


def kvm_data(run):
    m = run.module("kvm") if run else None
    if not m or m.status in ("SKIPPED", "ERROR") or not (m.data or {}).get("node"):
        return None
    return m.data


def _state(vm):
    txt, st = STATE.get(vm.get("state"), (str(vm.get("state", "?")).title(), "chip-na"))
    return Cell(txt, st)


def _pct(v):
    return Cell("%.0f %%" % v, "b") if v is not None else Cell("-", "fl")


def _cpu_stats(data, name):
    return stats([p for p in data.points("kvm.vm_cpu:" + name) if p[0] >= data.since])


def _availability(data, name):
    """(share of checks the VM was running, times it went from running to off, time of the last change) over the period."""
    pts = [p for p in data.points("kvm.vm_running:" + name) if p[0] >= data.since]
    if not pts:
        return None
    on = sum(1 for _, v in pts if v >= 0.5)
    stops, last = 0, None
    for (t0, a), (t1, b) in zip(pts, pts[1:]):
        if (a >= 0.5) != (b >= 0.5):
            last = t1
            if a >= 0.5:
                stops += 1
    return 100.0 * on / len(pts), stops, last, len(pts)


def summary_point(run):
    """One plain-English line for 'In short' and the email."""
    k = kvm_data(run)
    if not k:
        return None
    vms = k.get("vms") or []
    running = sum(1 for v in vms if v.get("state") == "running")
    bad = [v["name"] for v in vms if v.get("state") in ("crashed", "paused", "pmsuspended", "blocked")]
    down_auto = [v["name"] for v in vms if v.get("autostart") and v.get("state") == "shut off"]
    pools = [p for p in k.get("pools") or [] if p.get("used_percent") is not None]
    full = max(pools, key=lambda p: p["used_percent"]) if pools else None
    text = "%d of %d virtual machine%s running" % (running, len(vms), "" if len(vms) == 1 else "s are" if running != 1 else "s is")
    if len(vms) == 1:
        text = "The virtual machine is %s" % ("running" if running else "not running")
    text += "; vCPU %.2f:1 of the host CPUs" % (k.get("vcpu_overcommit") or 0)
    if full:
        text += "; fullest storage pool %s at %.0f %%" % (full["name"], full["used_percent"])
    lv = "ok"
    if bad:
        text += ". Needs attention: %s" % ", ".join(bad)
        lv = "cr"
    elif down_auto:
        text += ". Set to start automatically but off: %s" % ", ".join(down_auto)
        lv = "wn"
    if full and full["used_percent"] >= 80:
        lv = "cr" if full["used_percent"] >= 90 else ("wn" if lv == "ok" else lv)
    return lv, text + "."


def blocks(data, ptype, variant):
    k = kvm_data(data.run)
    if not k:
        return []
    vms = list(k.get("vms") or [])
    full = variant == "comprehensive"
    win = "d1" if ptype == "daily" else "d30" if ptype == "monthly" else "d7"
    win_label = {"d1": "24 h", "d7": "7 d", "d30": "30 d"}[win]
    host_cpus, host_mem = k.get("host_cpus") or 0, k.get("host_memory") or 0
    running = [v for v in vms if v.get("state") == "running"]
    vcpu_run = k.get("vcpu_running", sum(v.get("vcpus") or 0 for v in running))
    mem_run = k.get("memory_running", sum(v.get("memory") or 0 for v in running))
    pools = [p for p in k.get("pools") or []]
    used_pools = [p for p in pools if p.get("used_percent") is not None]
    fullest = max(used_pools, key=lambda p: p["used_percent"]) if used_pools else None
    th = lambda key, d: data.cfg.getfloat("thresholds", "kvm", key, d) if data.cfg else d
    pw, pc = th("pool_warning", 80), th("pool_critical", 90)
    off_auto = [v for v in vms if v.get("autostart") and v.get("state") == "shut off"]
    items = [("%d of %d" % (len(running), len(vms)), "VMs running", "ok" if not off_auto else "wn"),
             ("%d / %d" % (vcpu_run, host_cpus) if host_cpus else str(vcpu_run), "vCPU given / host CPUs (%.2f:1)" % (k.get("vcpu_overcommit") or 0),
              "wn" if (k.get("vcpu_overcommit") or 0) >= th("vcpu_overcommit_warning", 4.0) else "ok"),
             ("%.0f %%" % (100.0 * mem_run / host_mem) if host_mem else fmt_bytes(mem_run), "Host memory given to VMs",
              "wn" if host_mem and mem_run > host_mem * th("memory_overcommit_warning", 1.0) else "ok")]
    if fullest:
        items.append(("%.0f %%" % fullest["used_percent"], "Fullest pool (%s)" % fullest["name"], level_for(fullest["used_percent"], pw, pc)))
    out = [kpis(items)]

    # the virtual machines: the busiest and largest first; the comprehensive report lists every VM
    def disk(v):
        alloc = sum(d.get("allocation") or 0 for d in v.get("disks") or [])
        cap = sum(d.get("capacity") or 0 for d in v.get("disks") or [])
        return alloc, cap
    rows = []
    order = sorted(vms, key=lambda v: (v.get("state") != "running", -(v.get("cpu_percent") or 0), -disk(v)[0]))
    shown = order if full else order[:10]
    for v in shown:
        alloc, cap = disk(v)
        it = growth_item(data, "kvm.vm_disk_alloc:" + v["name"])
        cs = _cpu_stats(data, v["name"])
        row = [Cell(v["name"], "b"), _state(v), v.get("vcpus") or "-", fmt_bytes(v.get("memory") or 0), _pct(v.get("cpu_percent"))]
        if ptype != "daily":
            row += [_pct(cs["avg"]) if cs else Cell("-", "fl"), Cell("%.0f %%" % cs["max"], "up" if cs["max"] >= th("vm_cpu_warning", 90) else "b") if cs else Cell("-", "fl")]
        row += [fmt_bytes(alloc) if alloc else "-", fmt_bytes(cap) if cap else "-", fmt_delta(it[win] if it else None, cap)]
        row += ["yes" if v.get("autostart") else "no"]
        if full:
            av = _availability(data, v["name"])
            row += [Cell("%.0f %%" % av[0], "ok" if av[0] >= 99.5 else "wn") if av else Cell("-", "fl"), str(av[1]) if av else "-",
                    "%d%s" % (v.get("snapshots") or 0, (" (oldest %.0f d)" % v["oldest_snapshot_days"]) if v.get("oldest_snapshot_days") else "")]
        rows.append(row)
    heads = ["VM", "State", "vCPU", "Memory", "CPU now"] + (["CPU avg", "CPU peak"] if ptype != "daily" else []) + \
            ["Disk used", "Disk size", "Growth (%s)" % win_label, "Autostart"] + (["Running in period", "Stops", "Snapshots"] if full else [])
    align = ["", "", "r", "r", "r"] + (["r", "r"] if ptype != "daily" else []) + ["r", "r", "r", ""] + (["r", "r", ""] if full else [])
    more = "" if full or len(order) <= 10 else " The 10 busiest are shown; the comprehensive monthly report lists all %d." % len(order)
    out.append(table("Virtual machines", heads, rows, align=align,
                     legend="CPU is the share of the VM's own vCPUs. Disk used is the space the disk images take on the host; disk size is what the VM sees." + more))

    # storage pools
    if pools:
        prow = []
        for p in sorted(pools, key=lambda p: -(p.get("used_percent") or 0)):
            it = growth_item(data, "kvm.pool_alloc:" + p["name"])
            prow.append([Cell(p["name"], "b"), Cell(str(p.get("state") or "?"), "ok" if p.get("state") == "running" else "wn"),
                         Bar(p["used_percent"], pw, pc) if p.get("used_percent") is not None else Cell("-", "fl"),
                         fmt_bytes(p["allocation"]) if p.get("capacity") else "-", fmt_bytes(p["available"]) if p.get("capacity") else "-",
                         fmt_bytes(p["capacity"]) if p.get("capacity") else "-",
                         fmt_delta(it[win] if it else None, p.get("capacity"))])
        out.append(table("Storage pools", ["Pool", "State", "Used", "Allocated", "Free", "Capacity", "Growth (%s)" % win_label], prow,
                         align=["", "", "", "r", "r", "r", "r"], legend="VMs pause when a thin disk cannot grow because its pool is full."))

    if not full:
        return out

    # host against VMs
    cpu_now = data.cur.get("cpu.usage_percent")
    mem_now = data.cur.get("memory.used_percent")
    if cpu_now is None or mem_now is None:                   # no figure from this run: the average over the period
        try:
            use, _ = data.usage()
            cs, ms = stats(use["cpu"]["all"]), stats(use["mem"]["all"])
            cpu_now = cpu_now if cpu_now is not None else (cs["avg"] if cs else None)
            mem_now = mem_now if mem_now is not None else (ms["avg"] if ms else None)
        except Exception:
            pass
    vm_cpu_cores = sum((v.get("cpu_percent") or 0) * (v.get("vcpus") or 0) / 100.0 for v in running)
    out.append(table("Host and virtual machines", ["", "Host", "Virtual machines"], [
        ["CPU", "%d CPUs" % host_cpus + ((", %.0f %% busy" % cpu_now) if cpu_now is not None else ""),
         "%d vCPU given to running VMs (%.2f:1); VMs use about %.1f CPUs now" % (vcpu_run, k.get("vcpu_overcommit") or 0, vm_cpu_cores)],
        ["Memory", fmt_bytes(host_mem) + ((", %.0f %% used" % mem_now) if mem_now is not None else ""),
         "%s given to running VMs (%s of the host)" % (fmt_bytes(mem_run), ("%.0f %%" % (100.0 * mem_run / host_mem)) if host_mem else "?")],
        ["Virtual machines", "%d defined" % len(vms), "%d running, %d off, %d set to start with the host" % (
            len(running), sum(1 for v in vms if v.get("state") == "shut off"), sum(1 for v in vms if v.get("autostart")))],
    ], align=["", "", ""]))

    # every disk of every VM, with growth
    drows = []
    for v in sorted(vms, key=lambda v: v["name"]):
        for d in v.get("disks") or []:
            it = growth_item(data, "kvm.disk_alloc:%s|%s" % (v["name"], d.get("name")))
            cap, alloc = d.get("capacity") or 0, d.get("allocation") or 0
            drows.append([Cell(v["name"], "b"), d.get("name") or "-", Cell(d.get("path") or "-", "wrap"), fmt_bytes(alloc) if alloc else "-",
                          fmt_bytes(cap) if cap else "-",
                          Cell("block device", "fl") if str(d.get("path") or "").startswith("/dev/") else (Bar(100.0 * alloc / cap, 85, 95) if cap and alloc else Cell("-", "fl")),
                          fmt_delta(it["d7"] if it else None, cap), fmt_delta(it["d30"] if it else None, cap)])
    if drows:
        out.append(table("Virtual machine disks", ["VM", "Disk", "Image or device", "Used", "Size", "Filled", "Growth (7 d)", "Growth (30 d)"], drows,
                         align=["", "", "", "r", "r", "", "r", "r"],
                         legend="Used is the space the image takes on the host (thin images grow up to their size). Block devices (LVM, LUNs) are always full size."))
    # disk growth and CPU over the period
    items = [(v["name"], growth_item(data, "kvm.vm_disk_alloc:" + v["name"])) for v in vms]
    items = [(n, it) for n, it in items if it and it.get("d30") is not None and abs(it["d30"]) >= 100 * 1024 * 1024]
    items = sorted(items, key=lambda x: -abs(x[1]["d30"]))[:6]
    gc = growth_charts(items, "Disk space used by", data.since)
    series = []
    for v in sorted(running, key=lambda v: -(v.get("cpu_percent") or 0))[:6]:
        pts = [[int(t), round(val, 1)] for t, val in data.points("kvm.vm_cpu:" + v["name"]) if t >= data.since]
        if len(pts) >= 2:
            series.append({"name": v["name"], "pts": pts})
    if series:
        gc.append({"title": "CPU of the busiest VMs", "unit": " %", "color": COLORS[0], "warn": None, "crit": None, "max": 100, "min": 0,
                   "multi": True, "colors": COLORS, "series": series})
    c = charts(gc)
    if c:
        out.append(c)
    if not items:
        out.append(note("No VM disk grew by 100 MB or more in the last 30 days." if any(growth_item(data, "kvm.vm_disk_alloc:" + v["name"]) for v in vms)
                        else "Growth figures and charts for each VM build up from the next health checks (one point per check)."))
    # snapshots worth a look
    snaps = [v for v in vms if v.get("snapshots")]
    if snaps:
        out.append(table("Snapshots", ["VM", "Snapshots", "Oldest"], [[Cell(v["name"], "b"), v["snapshots"],
                   Cell("%.0f days" % v["oldest_snapshot_days"], "wn" if v["oldest_snapshot_days"] >= th("snapshot_age_warning_days", 7) else "ok")
                   if v.get("oldest_snapshot_days") is not None else "-"] for v in snaps], align=["", "r", "r"],
                   legend="Long-lived snapshots keep growing and slow the disk down; merge or delete them once they are no longer needed."))
    nets = k.get("networks") or []
    if nets:
        out.append(table("Virtual networks", ["Network", "State", "Autostart"], [[Cell(n.get("Name", ""), "b"), Cell(n.get("State", ""), "ok" if n.get("State") == "active" else "wn"),
                                                                                  n.get("Autostart", "")] for n in nets]))
    return out


def last_change_text(ts):
    return stamp(ts, "%d %b %H:%M") if ts else "-"
