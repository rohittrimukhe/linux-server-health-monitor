# Linux Server Health Monitor Enterprise
# Copyright (c) 2026 LRS Services Pvt Ltd, Mumbai. All rights reserved.
"""Top 10 programs by CPU, memory, network traffic and network connections.

Very light: reads /proc twice, one second apart (a few hundred small files), and runs one `ss` command.
Nothing is started, stopped or changed. CPU and memory are summed per program name, so a database
with many worker processes counts as one program."""
import os
import re
import time

from lshme.core.util import fmt_bytes
from lshme.reports import periodic as P
from lshme.reports.periodic import Bar, Cell, table

TOP = 10


def scan_proc(root="/proc", pagesize=4096):
    """{pid: (name, cpu ticks, rss bytes)} for every process."""
    out = {}
    try:
        names = os.listdir(root)
    except OSError:
        return out
    for pid in names:
        if not pid.isdigit():
            continue
        try:
            with open("%s/%s/stat" % (root, pid), encoding="utf-8", errors="replace") as f:
                data = f.read()
        except OSError:
            continue
        a, b = data.find("("), data.rfind(")")
        if a < 0 or b < 0:
            continue
        rest = data[b + 2:].split()
        try:
            out[pid] = (data[a + 1:b], int(rest[11]) + int(rest[12]), int(rest[21]) * pagesize, unit_of("%s/%s/cgroup" % (root, pid)))
        except (IndexError, ValueError):
            continue
    return out


UNIT_RE = re.compile(r"([^/]+\.service)")


def unit_of(path):
    """The systemd service a process belongs to (from its control group), or None."""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            found = UNIT_RE.findall(f.read())
    except OSError:
        return None
    return found[-1][:-8] if found else None


def group(first, second, seconds, clk, ncpu, mem_total, by_unit=False):
    """Per program (or per systemd service): CPU % of the whole server over the interval, memory in bytes, process count."""
    progs = {}
    for pid, vals in second.items():
        name, ticks, rss = vals[0], vals[1], vals[2]
        if by_unit:
            name = vals[3] if len(vals) > 3 else None
            if not name:
                continue
        g = progs.setdefault(name, {"name": name, "ticks": 0.0, "rss": 0, "procs": 0})
        before = first.get(pid)
        g["ticks"] += max(0, ticks - before[1]) if before and before[0] == vals[0] else 0
        g["rss"] += rss
        g["procs"] += 1
    for g in progs.values():
        g["cpu"] = 100.0 * g["ticks"] / (clk * seconds * max(1, ncpu))
        g["mem_pct"] = 100.0 * g["rss"] / mem_total if mem_total else 0.0
    return list(progs.values())


def parse_ss(text):
    """Per program: connections, established, listening, bytes sent and received (None when the kernel gives no counters)."""
    progs = {}
    lines = [l for l in (text or "").splitlines() if l.strip()]
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith(("State", "Netid")):
            i += 1
            continue
        info = ""
        if i + 1 < len(lines) and lines[i + 1][:1] in (" ", "\t"):
            info = lines[i + 1]
            i += 1
        i += 1
        parts = line.split()
        if not parts:
            continue
        state = parts[0]
        m = re.search(r'\("([^"]+)",pid=(\d+)', line)
        name = m.group(1) if m else "(not visible)"
        g = progs.setdefault(name, {"name": name, "conns": 0, "estab": 0, "listen": 0, "sent": 0, "recv": 0, "counters": False})
        if state == "LISTEN":
            g["listen"] += 1
            continue
        g["conns"] += 1
        if state == "ESTAB":
            g["estab"] += 1
        s, r = re.search(r"bytes_sent:(\d+)", info), re.search(r"bytes_received:(\d+)", info)
        if s or r:
            g["counters"] = True
            g["sent"] += int(s.group(1)) if s else 0
            g["recv"] += int(r.group(1)) if r else 0
    return list(progs.values())


def mem_total():
    try:
        with open("/proc/meminfo", encoding="utf-8") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    return int(line.split()[1]) * 1024
    except (OSError, ValueError, IndexError):
        pass
    return 0


def collect(runner, interval=1.0):
    """Take the two samples and run ss. Returns {'cpu': [...], 'net': [...], 'ncpu': n, 'seen': bool}."""
    pagesize = os.sysconf("SC_PAGE_SIZE")
    clk = os.sysconf("SC_CLK_TCK")
    ncpu = os.cpu_count() or 1
    me = str(os.getpid())          # leave out this report itself
    first = scan_proc(pagesize=pagesize)
    first.pop(me, None)
    t0 = time.time()
    time.sleep(interval)
    second = scan_proc(pagesize=pagesize)
    second.pop(me, None)
    progs = group(first, second, max(time.time() - t0, 0.1), clk, ncpu, mem_total())
    r = runner.run(["ss", "-tanpi"], timeout=20) if runner.has("ss") else None
    net = parse_ss(r.out) if r and r.out else []
    return {"procs": progs, "units": group(first, second, max(time.time() - t0, 0.1), clk, ncpu, mem_total(), True), "net": net, "ncpu": ncpu,
            "ss": bool(r and r.out)}


def blocks(cfg, runner=None):
    """The 'Top 10' tables for a report."""
    if runner is None:
        from lshme.core.runner import Runner
        runner = Runner(cfg.getint("main", "general", "command_timeout", 30))
    d = collect(runner)
    warn, crit = cfg.threshold("cpu", "warning", 85), cfg.threshold("cpu", "critical", 95)
    out = []
    user_progs = [g for g in d["procs"] if g["rss"] > 0]          # kernel threads hold no memory and are not applications
    by_cpu = [g for g in sorted(user_progs, key=lambda g: -g["cpu"]) if g["cpu"] >= 0.05][:TOP]
    out.append(table("Top 10 programs by CPU", ["#", "Program", "CPU (% of server)", "", "Processes", "Memory"],
                     [[i, Cell(g["name"], "b"), "%.1f %%" % g["cpu"], Bar(min(g["cpu"], 100.0), warn, crit), g["procs"], fmt_bytes(g["rss"])]
                      for i, g in enumerate(by_cpu, 1)], align=["", "", "r", "", "r", "r"],
                     legend="Measured over one second when this report was made. 100 % means every CPU core is fully used by that program."
                            + ("" if by_cpu else " No program used a measurable amount of CPU in that second.")))
    mw, mc = cfg.threshold("memory", "warning", 85), cfg.threshold("memory", "critical", 95)
    by_mem = sorted(user_progs, key=lambda g: -g["rss"])[:TOP]
    out.append(table("Top 10 programs by memory", ["#", "Program", "Memory used", "% of RAM", "", "Processes"],
                     [[i, Cell(g["name"], "b"), fmt_bytes(g["rss"]), "%.1f %%" % g["mem_pct"], Bar(min(g["mem_pct"], 100.0), mw, mc), g["procs"]]
                      for i, g in enumerate(by_mem, 1)], align=["", "", "r", "r", "", "r"],
                     legend="Memory that is really held in RAM by all processes of the program (shared memory is counted once per process)."))
    units = [g for g in d["units"] if g["rss"] > 0]
    if units:
        by_use = sorted(units, key=lambda g: -(g["rss"] + g["cpu"] * 1e8))[:TOP]
        out.append(table("Top 10 services by use (CPU and memory)", ["#", "Service", "CPU (% of server)", "Memory", "% of RAM", "", "Processes"],
                         [[i, Cell(g["name"], "b"), "%.1f %%" % g["cpu"], fmt_bytes(g["rss"]), "%.1f %%" % g["mem_pct"], Bar(min(g["mem_pct"], 100.0), mw, mc), g["procs"]]
                          for i, g in enumerate(by_use, 1)], align=["", "", "r", "r", "r", "", "r"],
                         legend="Systemd services ranked by memory held in RAM, with the CPU they used in the sampled second. All processes of a service are added together."))
    net = d["net"]
    if not d["ss"]:
        out.append(P.note("Network figures are not available: the 'ss' command is missing or returned nothing."))
        return out
    visible = [g for g in net if g["name"] != "(not visible)"]
    if not visible and net:
        out.append(P.note("Program names for network connections need root access. Run the report as root (the scheduled reports do)."))
    traffic = sorted([g for g in visible if g["counters"]], key=lambda g: -(g["sent"] + g["recv"]))[:TOP]
    if traffic:
        out.append(table("Top 10 programs by network traffic", ["#", "Program", "Received", "Sent", "Total", "Connections"],
                         [[i, Cell(g["name"], "b"), fmt_bytes(g["recv"]), fmt_bytes(g["sent"]), Cell(fmt_bytes(g["recv"] + g["sent"]), "b"), g["conns"]]
                          for i, g in enumerate(traffic, 1)], align=["", "", "r", "r", "r", "r"],
                         legend="Bytes carried by the TCP connections that are open right now, counted from the moment each connection started."))
    else:
        out.append(P.note("Per-program traffic totals are not available on this kernel (they need Linux 4.1 or newer). Connection counts are below."))
    conn = sorted(visible, key=lambda g: (-g["conns"], -g["listen"]))[:TOP]
    out.append(table("Top 10 services by network connections", ["#", "Service / application", "Open connections", "Established", "Listening ports"],
                     [[i, Cell(g["name"], "b"), g["conns"], g["estab"], g["listen"]] for i, g in enumerate(conn, 1)], align=["", "", "r", "r", "r"],
                     legend="TCP connections per program right now; 'listening ports' are the ports the program accepts connections on."))
    return out
