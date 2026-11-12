"""KVM / libvirt: hypervisor capacity, every VM's state and utilisation, snapshots,
storage pools and virtual networks. Uses read-only virsh commands only."""
import re
import time

from lshme.core.util import fmt_bytes, pct, severity_for, to_int

NAME = "kvm"
TITLE = "KVM Virtualisation"
WEIGHT = 10

STATE_NAMES = {0: "no state", 1: "running", 2: "blocked", 3: "paused", 4: "shutting down",
               5: "shut off", 6: "crashed", 7: "pmsuspended"}


def parse_domstats(text):
    doms, cur = {}, None
    for line in text.splitlines():
        m = re.match(r"^Domain:\s*'(.+)'", line)
        if m:
            cur = doms.setdefault(m.group(1), {})
            continue
        if cur is not None and "=" in line:
            k, v = line.strip().split("=", 1)
            cur[k] = v
    return doms


def parse_table(text):
    """Parse virsh tabular output (header, dashes, rows) into dicts."""
    lines = [l for l in text.splitlines() if l.strip()]
    if len(lines) < 2 or not set(lines[1].strip()) <= set("-+ "):
        return []
    header = lines[0]
    cols = [(m.start(), m.group(0)) for m in re.finditer(r"\S+(?: \S+)*", header)]
    rows = []
    for line in lines[2:]:
        row = {}
        for i, (start, name) in enumerate(cols):
            end = cols[i + 1][0] if i + 1 < len(cols) else None
            row[name.strip()] = line[start:end].strip() if end else line[start:].strip()
        rows.append(row)
    return rows


def kv(text):
    out = {}
    for line in text.splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            out[k.strip()] = v.strip()
    return out


def size_to_bytes(text):
    m = re.match(r"([\d.]+)\s*([KMGTP]i?B|B|bytes)?", text.strip())
    if not m:
        return None
    mult = {"B": 1, "bytes": 1, "KiB": 1024, "MiB": 1024 ** 2, "GiB": 1024 ** 3, "TiB": 1024 ** 4, "PiB": 1024 ** 5,
            "KB": 1000, "MB": 1000 ** 2, "GB": 1000 ** 3, "TB": 1000 ** 4}.get(m.group(2) or "B", 1)
    return float(m.group(1)) * mult


def run(ctx, res):
    if not ctx.has("virsh"):
        res.status = "SKIPPED"
        res.skip("virsh is not installed (not a KVM host)")
        res.summary = "not a KVM/libvirt host"
        return
    uri = ctx.cfg.get("main", "kvm", "uri", "qemu:///system")
    v = lambda *a, **kw: ctx.run(["virsh", "-c", uri] + list(a), **kw)
    probe = v("uri", timeout=20)
    if not probe.ok:
        res.status = "ERROR"
        res.add("CRITICAL", "LIBVIRT_UNREACHABLE", "libvirtd", "Cannot connect to libvirt at %s" % uri,
                evidence=probe.brief_error(),
                recommendation="Check 'systemctl status libvirtd' (or virtqemud) and permissions of the monitoring user.")
        return
    node = kv(v("nodeinfo").out)
    host_cpus = to_int(node.get("CPU(s)"), 0) or 1
    host_mem = (to_int((node.get("Memory size", "0").split() or ["0"])[0], 0) or 0) * 1024
    version = v("version").out

    interval = max(1.0, ctx.cfg.getfloat("main", "kvm", "sample_seconds", 2))
    s1 = parse_domstats(v("domstats", "--raw", timeout=60).out)
    t1 = time.time()
    ctx.runner.sleep(interval)
    s2 = parse_domstats(v("domstats", "--raw", timeout=60).out)
    elapsed = max(interval, time.time() - t1)

    names = [n.strip() for n in v("list", "--all", "--name").out.splitlines() if n.strip()]
    vms = []
    total_vcpu, total_mem_running = 0, 0
    snap_age = ctx.t("kvm", "snapshot_age_warning_days", 7)
    for name in names:
        a, b = s1.get(name, {}), s2.get(name, {})
        info = kv(v("dominfo", name).out)
        state_num = to_int(b.get("state.state", a.get("state.state")), None)
        state = STATE_NAMES.get(state_num, info.get("State", "unknown"))
        vcpus = to_int(b.get("vcpu.current") or info.get("CPU(s)"), 0) or 0
        mem_kib = to_int(b.get("balloon.current") or (info.get("Used memory", "0").split() or ["0"])[0], 0) or 0
        max_kib = to_int(b.get("balloon.maximum") or (info.get("Max memory", "0").split() or ["0"])[0], 0) or 0
        cpu_pct = None
        if "cpu.time" in a and "cpu.time" in b and vcpus:
            cpu_pct = round(100.0 * (int(b["cpu.time"]) - int(a["cpu.time"])) / (elapsed * 1e9 * vcpus), 1)
        mem_used_pct = None
        if b.get("balloon.available") and b.get("balloon.unused"):
            avail, unused = to_int(b["balloon.available"], 0), to_int(b["balloon.unused"], 0)
            mem_used_pct = pct(avail - unused, avail)
        rd = wr = rx = tx = 0.0
        disks, nics = [], []
        for i in range(to_int(b.get("block.count"), 0) or 0):
            p = "block.%d." % i
            disks.append({"name": b.get(p + "name", ""), "path": b.get(p + "path", ""),
                          "capacity": to_int(b.get(p + "capacity")), "allocation": to_int(b.get(p + "allocation"))})
            rd += (to_int(b.get(p + "rd.bytes"), 0) - to_int(a.get(p + "rd.bytes"), 0)) / elapsed
            wr += (to_int(b.get(p + "wr.bytes"), 0) - to_int(a.get(p + "wr.bytes"), 0)) / elapsed
        for i in range(to_int(b.get("net.count"), 0) or 0):
            p = "net.%d." % i
            nics.append(b.get(p + "name", ""))
            rx += (to_int(b.get(p + "rx.bytes"), 0) - to_int(a.get(p + "rx.bytes"), 0)) / elapsed
            tx += (to_int(b.get(p + "tx.bytes"), 0) - to_int(a.get(p + "tx.bytes"), 0)) / elapsed
        snaps = parse_table(v("snapshot-list", name).out)
        oldest = None
        for s in snaps:
            ct = s.get("Creation Time", "")
            m = re.match(r"(\d{4})-(\d{2})-(\d{2}) (\d{2}):(\d{2}):(\d{2})", ct)
            if m:
                ts = time.mktime(time.strptime(m.group(0), "%Y-%m-%d %H:%M:%S"))
                oldest = ts if oldest is None else min(oldest, ts)
        autostart = info.get("Autostart", "") == "enable"
        vm = {"name": name, "state": state, "vcpus": vcpus, "memory": mem_kib * 1024, "max_memory": max_kib * 1024,
              "cpu_percent": cpu_pct, "memory_used_percent": mem_used_pct, "autostart": autostart,
              "persistent": info.get("Persistent", ""), "disks": disks, "interfaces": nics,
              "disk_read_bps": max(rd, 0), "disk_write_bps": max(wr, 0), "net_rx_bps": max(rx, 0), "net_tx_bps": max(tx, 0),
              "snapshots": len(snaps), "oldest_snapshot_days": round((time.time() - oldest) / 86400, 1) if oldest else None,
              "uptime_hint": info.get("CPU time", "")}
        vms.append(vm)
        if state == "running":
            total_vcpu += vcpus
            total_mem_running += mem_kib * 1024
        if state == "crashed":
            res.add("CRITICAL", "VM_CRASHED", name, "VM %s has crashed" % name,
                    recommendation="Review /var/log/libvirt/qemu/%s.log before restarting the VM." % name)
        elif state in ("paused", "pmsuspended", "blocked"):
            res.add("WARNING", "VM_PAUSED", name, "VM %s is %s" % (name, state),
                    recommendation="A paused VM is often caused by a full storage pool or I/O error.")
        elif autostart and state == "shut off":
            res.add("WARNING", "VM_AUTOSTART_DOWN", name, "VM %s is marked autostart but is shut off" % name,
                    recommendation="Confirm whether the VM should be running.")
        if cpu_pct is not None and cpu_pct >= ctx.t("kvm", "vm_cpu_warning", 90):
            res.add("WARNING", "VM_CPU_HIGH", name, "VM %s CPU at %.0f%% of its %d vCPU" % (name, cpu_pct, vcpus))
        if vm["oldest_snapshot_days"] is not None and vm["oldest_snapshot_days"] >= snap_age:
            res.add("WARNING", "VM_OLD_SNAPSHOT", name, "VM %s has a snapshot %.0f days old (%d snapshots)" % (
                name, vm["oldest_snapshot_days"], len(snaps)),
                recommendation="Long-lived snapshots grow and slow I/O; merge or delete them after verification.")

    # storage pools
    pools = []
    pool_names = [x.strip() for x in v("pool-list", "--all", "--name").out.splitlines() if x.strip()]
    pw, pc = ctx.t("kvm", "pool_warning", 80), ctx.t("kvm", "pool_critical", 90)
    for p in pool_names:
        info = kv(v("pool-info", p).out)
        cap, alloc = size_to_bytes(info.get("Capacity", "0")), size_to_bytes(info.get("Allocation", "0"))
        avail = size_to_bytes(info.get("Available", "0"))
        used = pct(alloc, cap) if cap else None
        pools.append({"name": p, "state": info.get("State", ""), "autostart": info.get("Autostart", ""),
                      "capacity": cap, "allocation": alloc, "available": avail, "used_percent": used})
        if used is not None:
            res.metric("kvm.pool_used_percent:" + p, used)
        sev = severity_for(used, pw, pc)
        if sev:
            res.add(sev, "KVM_POOL_FULL", p, "Storage pool %s is %.1f%% used (%s free)" % (p, used, fmt_bytes(avail)),
                    recommendation="VMs pause when thin disks cannot grow; free space or extend the pool.")
        if info.get("State") not in ("running", None, "") and info.get("Autostart") == "yes":
            res.add("WARNING", "KVM_POOL_INACTIVE", p, "Storage pool %s is %s" % (p, info.get("State")))
    nets = parse_table(v("net-list", "--all").out)
    for n in nets:
        if n.get("State") != "active" and n.get("Autostart") == "yes":
            res.add("WARNING", "KVM_NETWORK_INACTIVE", n.get("Name", "?"), "Virtual network %s is inactive" % n.get("Name"))

    vcpu_ratio = round(total_vcpu / float(host_cpus), 2)
    mem_ratio = round(total_mem_running / float(host_mem), 2) if host_mem else None
    if vcpu_ratio >= ctx.t("kvm", "vcpu_overcommit_warning", 4.0):
        res.add("WARNING", "KVM_VCPU_OVERCOMMIT", "hypervisor", "vCPU overcommit %.1f:1 (%d vCPU on %d CPUs)" % (vcpu_ratio, total_vcpu, host_cpus),
                recommendation="High overcommit causes CPU steal inside guests; rebalance VMs.")
    if mem_ratio is not None and mem_ratio > ctx.t("kvm", "memory_overcommit_warning", 1.0):
        res.add("WARNING", "KVM_MEMORY_OVERCOMMIT", "hypervisor", "Running VMs are assigned %.0f%% of host RAM" % (mem_ratio * 100),
                recommendation="Memory overcommit risks host swapping or OOM kills of VMs.")
    running = sum(1 for x in vms if x["state"] == "running")
    res.metric("kvm.vms_total", len(vms))
    res.metric("kvm.vms_running", running)
    res.metric("kvm.vcpu_overcommit", vcpu_ratio)
    res.data = {"uri": uri, "version": version, "node": node, "vms": vms, "pools": pools, "networks": nets,
                "vcpu_overcommit": vcpu_ratio, "memory_commit_ratio": mem_ratio}
    res.table("Hypervisor capacity", ["Item", "Value"], [
        ["libvirt URI", uri], ["Host CPUs / memory", "%d / %s" % (host_cpus, fmt_bytes(host_mem))],
        ["VMs (running / total)", "%d / %d" % (running, len(vms))],
        ["vCPU assigned to running VMs", "%d (%.2f:1)" % (total_vcpu, vcpu_ratio)],
        ["Memory assigned to running VMs", "%s (%s%%)" % (fmt_bytes(total_mem_running), round(mem_ratio * 100, 1) if mem_ratio is not None else "?")],
    ], kind="summary")
    res.table("Virtual machines", ["VM", "State", "vCPU", "Memory", "CPU%", "Mem used%", "Disk R/W per s", "Net RX/TX per s", "Autostart", "Snapshots"],
              [[x["name"], x["state"], x["vcpus"], fmt_bytes(x["memory"]), x["cpu_percent"] if x["cpu_percent"] is not None else "",
                x["memory_used_percent"] if x["memory_used_percent"] is not None else "",
                "%s / %s" % (fmt_bytes(x["disk_read_bps"]), fmt_bytes(x["disk_write_bps"])),
                "%s / %s" % (fmt_bytes(x["net_rx_bps"]), fmt_bytes(x["net_tx_bps"])),
                "yes" if x["autostart"] else "no", x["snapshots"]] for x in vms], kind="summary")
    res.table("VM disks", ["VM", "Device", "Path", "Capacity", "Allocated"],
              [[x["name"], d["name"], d["path"], fmt_bytes(d["capacity"]) if d["capacity"] else "", fmt_bytes(d["allocation"]) if d["allocation"] else ""]
               for x in vms for d in x["disks"]])
    res.table("Storage pools", ["Pool", "State", "Autostart", "Capacity", "Allocated", "Available", "Used%"],
              [[p["name"], p["state"], p["autostart"], fmt_bytes(p["capacity"]), fmt_bytes(p["allocation"]), fmt_bytes(p["available"]),
                p["used_percent"] if p["used_percent"] is not None else ""] for p in pools])
    res.table("Virtual networks", ["Name", "State", "Autostart", "Persistent"],
              [[n.get("Name", ""), n.get("State", ""), n.get("Autostart", ""), n.get("Persistent", "")] for n in nets])
    res.summary = "%d VMs (%d running), %d pools, vCPU %.2f:1" % (len(vms), running, len(pools), vcpu_ratio)
