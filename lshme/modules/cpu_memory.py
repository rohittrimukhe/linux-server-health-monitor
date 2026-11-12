# Linux Server Health Monitor Enterprise
# Copyright (c) 2026 LRS Services Pvt Ltd, Mumbai. All rights reserved.
"""CPU utilisation, load, memory, swap and top resource consumers."""
from lshme.core.util import fmt_bytes, pct, severity_for, to_float

NAME = "cpu_memory"
TITLE = "CPU & Memory"
WEIGHT = 12


def cpu_times(ctx):
    line = (ctx.read("/proc/stat").splitlines() or [""])[0].split()
    try:
        return [int(x) for x in line[1:]]
    except ValueError:
        return []


def cpu_usage(ctx, seconds):
    a = cpu_times(ctx)
    ctx.runner.sleep(seconds)
    b = cpu_times(ctx)
    if len(a) < 4 or len(b) < 4:
        return None
    delta = [y - x for x, y in zip(a, b)]
    total = float(sum(delta[:8])) if len(delta) >= 8 else float(sum(delta))
    if total <= 0:
        return None
    idle = delta[3]
    iowait = delta[4] if len(delta) > 4 else 0
    steal = delta[7] if len(delta) > 7 else 0
    return {"usage": round(100 * (total - idle - iowait) / total, 2),
            "iowait": round(100.0 * iowait / total, 2), "steal": round(100.0 * steal / total, 2),
            "user": round(100.0 * delta[0] / total, 2), "system": round(100.0 * delta[2] / total, 2)}


def meminfo(ctx):
    d = {}
    for line in ctx.read("/proc/meminfo").splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            parts = v.split()
            try:
                d[k] = int(parts[0]) * (1024 if len(parts) > 1 else 1)
            except (ValueError, IndexError):
                pass
    total = d.get("MemTotal", 0)
    avail = d.get("MemAvailable")
    if avail is None:  # very old kernels
        avail = d.get("MemFree", 0) + d.get("Buffers", 0) + d.get("Cached", 0)
    st, sf = d.get("SwapTotal", 0), d.get("SwapFree", 0)
    return {"total": total, "available": avail, "used": max(total - avail, 0),
            "used_percent": pct(total - avail, total), "buffers": d.get("Buffers", 0),
            "cached": d.get("Cached", 0), "hugepages_total": d.get("HugePages_Total", 0),
            "hugepages_free": d.get("HugePages_Free", 0), "swap_total": st, "swap_used": st - sf,
            "swap_used_percent": pct(st - sf, st) if st else 0.0}


def top_processes(ctx, sort_key):
    r = ctx.run(["ps", "-eo", "pid,user,pcpu,pmem,rss,etime,comm", "--sort=-" + sort_key])
    rows = []
    for line in r.lines()[1:11]:
        p = line.split(None, 6)
        if len(p) == 7:
            rows.append([p[0], p[1], p[2], p[3], fmt_bytes(int(p[4]) * 1024) if p[4].isdigit() else p[4], p[5], p[6]])
    return rows


def run(ctx, res):
    cores = [l for l in ctx.read("/proc/cpuinfo").splitlines() if l.startswith("processor")]
    ncpu = len(cores) or 1
    model = ""
    sockets = set()
    for line in ctx.read("/proc/cpuinfo").splitlines():
        if ":" in line:
            k, v = [x.strip() for x in line.split(":", 1)]
            if k in ("model name", "Hardware", "cpu model") and not model:
                model = v
            if k == "physical id":
                sockets.add(v)
    usage = cpu_usage(ctx, ctx.t("cpu", "sample_seconds", 1))
    load = (ctx.read("/proc/loadavg").split() + ["0", "0", "0"])[:3]
    load = [to_float(x, 0.0) for x in load]
    mem = meminfo(ctx)
    res.data = {"model": model, "logical_cpus": ncpu, "sockets": len(sockets) or None,
                "usage": usage, "load_average": load, "memory": mem}
    if usage:
        res.metric("cpu.usage_percent", usage["usage"])
        res.metric("cpu.iowait_percent", usage["iowait"])
    res.metric("cpu.load5_per_core", load[1] / ncpu)
    res.metric("memory.used_percent", mem["used_percent"])
    res.metric("swap.used_percent", mem["swap_used_percent"])
    res.summary = "CPU %s%%, load %.2f, memory %s%%, swap %s%%" % (
        usage["usage"] if usage else "?", load[1], mem["used_percent"], mem["swap_used_percent"])

    res.table("CPU and memory", ["Metric", "Value"], [
        ["CPU model", model or "unknown"], ["Logical CPUs / sockets", "%d / %s" % (ncpu, len(sockets) or "?")],
        ["CPU utilisation", "%s%%" % (usage["usage"] if usage else "N/A")],
        ["I/O wait / steal", "%s%% / %s%%" % ((usage["iowait"], usage["steal"]) if usage else ("N/A", "N/A"))],
        ["Load average (1/5/15)", "%.2f / %.2f / %.2f" % tuple(load)],
        ["Memory total", fmt_bytes(mem["total"])], ["Memory used", "%s (%s%%)" % (fmt_bytes(mem["used"]), mem["used_percent"])],
        ["Memory available", fmt_bytes(mem["available"])], ["Cache + buffers", fmt_bytes(mem["cached"] + mem["buffers"])],
        ["Swap used", "%s of %s (%s%%)" % (fmt_bytes(mem["swap_used"]), fmt_bytes(mem["swap_total"]), mem["swap_used_percent"])],
        ["HugePages total / free", "%s / %s" % (mem["hugepages_total"], mem["hugepages_free"])],
    ], kind="summary")
    hdr = ["PID", "User", "%CPU", "%MEM", "RSS", "Elapsed", "Command"]
    res.table("Top processes by CPU", hdr, top_processes(ctx, "pcpu"))
    res.table("Top processes by memory", hdr, top_processes(ctx, "rss"))

    if usage:
        sev = severity_for(usage["usage"], ctx.t("cpu", "warning", 85), ctx.t("cpu", "critical", 95))
        if sev:
            res.add(sev, "CPU_HIGH", "cpu", "CPU utilisation %.1f%%" % usage["usage"],
                    recommendation="Identify the top CPU consumers below and check for runaway processes.")
        if usage["iowait"] >= ctx.t("cpu", "iowait_warning", 20):
            res.add("WARNING", "CPU_IOWAIT", "cpu", "I/O wait %.1f%% — storage may be a bottleneck" % usage["iowait"],
                    recommendation="Review disk latency in the Storage section and SAN/multipath health.")
        if usage["steal"] >= ctx.t("cpu", "steal_warning", 10):
            res.add("WARNING", "CPU_STEAL", "cpu", "CPU steal %.1f%% — hypervisor contention" % usage["steal"],
                    recommendation="Check host capacity / vCPU overcommit on the hypervisor.")
    per_core = load[1] / ncpu
    sev = severity_for(per_core, ctx.t("load", "per_core_warning", 1.5), ctx.t("load", "per_core_critical", 3.0))
    if sev:
        res.add(sev, "LOAD_HIGH", "load", "5-minute load %.2f on %d CPUs (%.2f per CPU)" % (load[1], ncpu, per_core),
                recommendation="Check for blocked processes (state D) and CPU saturation.")
    sev = severity_for(mem["used_percent"], ctx.t("memory", "warning", 85), ctx.t("memory", "critical", 95))
    if sev:
        res.add(sev, "MEMORY_HIGH", "memory", "Memory used %.1f%% (available %s)" % (mem["used_percent"], fmt_bytes(mem["available"])),
                recommendation="Review top memory consumers; consider tuning SGA/JVM sizes or adding RAM.")
    if mem["swap_total"]:
        sev = severity_for(mem["swap_used_percent"], ctx.t("swap", "warning", 50), ctx.t("swap", "critical", 80))
        if sev:
            res.add(sev, "SWAP_HIGH", "swap", "Swap used %.1f%%" % mem["swap_used_percent"],
                    recommendation="Sustained swapping degrades performance; investigate memory pressure.")
    else:
        res.add("INFO", "NO_SWAP", "swap", "No swap space is configured")
