# Linux Server Health Monitor Enterprise
# Copyright (c) 2026 LRS Services Pvt Ltd, Mumbai. All rights reserved.
"""The raw data store: everything LRS Vitals measures, kept as it was measured, one file per server per calendar day.

    <data>/raw/<host>/<YYYY>/<MM>/<host>_<YYYY-MM-DD>_00-00-00_to_23-59-59.jsonl

Each line is one JSON record {"ts", "time", "type", "v", "data"} and lands in the file of the day it was measured
(local time, 00:00:00 to 23:59:59), whenever it is written. Two hours after a day ends (02:00) the day is sealed: the file is
gzip-compressed (.jsonl.gz, still readable with zcat, zless and zgrep) and a manifest records the period, the number of
records of each type, the minutes covered, any gaps and a SHA-256 fingerprint.

Nothing is thinned or averaged here. The history database and the reports are built from this data, so a later version can
add a report or a section and still show it for the past: `vitals raw replay` refills the history database from these files.

Record types: day (server facts, first record of a day), sample (every live-watcher check: CPU, every core, memory detail,
swap, load, every filesystem with bytes and inodes, every network interface with rates, counters, errors and drops, disk I/O,
processes, context switches, paging, open problems), top (programs by CPU and memory, every 15 minutes), connections (TCP
states, listening ports, busiest peers), services (systemd states and failed units), event (incident opened, escalated,
improved, closed), alert (alert sent), run (complete health-check result), report (report files written), email (report
mail sent), audit (administrative actions), fleet (results collected from other servers)."""
import fcntl
import gzip
import hashlib
import json
import os
import re
import socket
import time

SCHEMA = 1
SEAL_AFTER = 2 * 3600            # a day is sealed two hours after it ends (02:00)
EXTRA_EVERY = 900                # top programs, connections and services every 15 minutes
NAME = re.compile(r"^(?P<host>.+)_(?P<day>\d{4}-\d{2}-\d{2})_00-00-00_to_23-59-59\.jsonl(?P<gz>\.gz)?$")

_root = None
_host = None
_mem = {}                        # previous counters in the watcher process (rates between checks)


def configure(paths, cfg=None):
    global _root, _host
    _root = os.path.join(paths.data_dir, "raw")
    _host = socket.gethostname().split(".")[0] if not _host else _host
    if cfg is not None and not cfg.getbool("retention", "retention", "raw_enabled", True):
        _root = None
    return _root


def root():
    return _root


def host_name():
    return _host or socket.gethostname().split(".")[0]


def safe(name):
    return re.sub(r"[^A-Za-z0-9_.-]", "_", name or "host")


def day_of(ts):
    return time.strftime("%Y-%m-%d", time.localtime(ts))


def file_name(host, day):
    return "%s_%s_00-00-00_to_23-59-59.jsonl" % (safe(host), day)


def day_dir(host, day, base=None):
    return os.path.join(base or _root, safe(host), day[:4], day[5:7])


def day_path(host, day, base=None):
    return os.path.join(day_dir(host, day, base), file_name(host, day))


def day_bounds(day):
    start = time.mktime(time.strptime(day + " 00:00:00", "%Y-%m-%d %H:%M:%S"))
    t = time.localtime(start + 36 * 3600)
    nxt = time.mktime((t.tm_year, t.tm_mon, t.tm_mday, 0, 0, 0, 0, 0, -1))
    return start, nxt                                      # nxt - 1 s is 23:59:59 (23 or 25 hours on clock-change days)


# -- writing --------------------------------------------------------------------------------------------------------------------
def _append(path, lines):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    data = "".join(lines).encode("utf-8")
    for _ in range(5):
        fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o640)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            try:
                same = os.fstat(fd).st_ino == os.stat(path).st_ino
            except OSError:
                same = False
            if not same:                                   # the day was sealed while we waited: write to a fresh file
                continue
            while data:
                n = os.write(fd, data)
                data = data[n:]
            return
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)
    raise IOError("could not append to %s" % path)


def record(rtype, data, ts=None):
    ts = time.time() if ts is None else float(ts)
    return {"ts": round(ts, 3), "time": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts)), "type": rtype, "v": SCHEMA, "data": data}


def write(rtype, data, ts=None, host=None, base=None):
    """Append one record to the file of the day it belongs to. Never raises: losing the raw copy must not stop a check."""
    base = base or _root
    if not base or not os.path.isdir(os.path.dirname(base)):
        return False
    try:
        rec = record(rtype, data, ts)
        host = host or host_name()
        day = day_of(rec["ts"])
        path = day_path(host, day, base)
        lines = []
        if not os.path.exists(path) and not os.path.exists(path + ".gz") and rtype != "day":
            lines.append(json.dumps(record("day", day_facts(host), rec["ts"]), sort_keys=True, default=str) + "\n")
        lines.append(json.dumps(rec, sort_keys=True, default=str) + "\n")
        _append(path, lines)
        return True
    except Exception:
        return False


def write_many(recs, host=None, base=None):
    """[(type, data, ts)] in one go (backfill)."""
    base = base or _root
    by_day = {}
    for rtype, data, ts in recs:
        rec = record(rtype, data, ts)
        by_day.setdefault(day_of(rec["ts"]), []).append(json.dumps(rec, sort_keys=True, default=str) + "\n")
    for day, lines in by_day.items():
        _append(day_path(host or host_name(), day, base), lines)
    return sum(len(v) for v in by_day.values())


def day_facts(host):
    facts = {"host": host, "fqdn": socket.getfqdn(), "timezone": time.strftime("%Z"), "utc_offset": time.strftime("%z"),
             "period": "00:00:00 to 23:59:59 local time"}
    try:
        from lshme import VERSION
        facts["app_version"] = VERSION
        u = os.uname()
        facts.update(kernel=u.release, machine=u.machine, cpus=os.cpu_count())
        facts["memory_total_bytes"] = meminfo().get("MemTotal")
        with open("/etc/os-release", encoding="utf-8") as f:
            facts["os"] = dict(l.strip().split("=", 1) for l in f if "=" in l).get("PRETTY_NAME", "").strip('"')
    except Exception:
        pass
    return facts


# -- what is measured -----------------------------------------------------------------------------------------------------------
def _read(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read()
    except (IOError, OSError):
        return ""


def meminfo():
    out = {}
    for line in _read("/proc/meminfo").splitlines():
        p = line.split()
        if len(p) >= 2 and p[1].isdigit():
            out[p[0].rstrip(":")] = int(p[1]) * (1024 if len(p) > 2 else 1)
    return out


def net_dev():
    out = {}
    for line in _read("/proc/net/dev").splitlines()[2:]:
        if ":" not in line:
            continue
        name, rest = line.split(":", 1)
        f = rest.split()
        if len(f) >= 16:
            out[name.strip()] = [int(x) for x in (f[0], f[1], f[2], f[3], f[8], f[9], f[10], f[11])]   # rx b, p, err, drop; tx b, p, err, drop
    return out


def disk_stats():
    out = {}
    for line in _read("/proc/diskstats").splitlines():
        f = line.split()
        if len(f) >= 14 and not f[2].startswith(("loop", "ram", "sr")):
            out[f[2]] = [int(f[3]), int(f[5]), int(f[7]), int(f[9]), int(f[12])]   # reads, sectors read, writes, sectors written, io ms
    return out


def vm_counters():
    out = {}
    for line in _read("/proc/stat").splitlines():
        p = line.split()
        if p and p[0] in ("ctxt", "intr", "processes", "procs_running", "procs_blocked"):
            out[p[0]] = int(p[1])
    for line in _read("/proc/vmstat").splitlines():
        p = line.split()
        if len(p) == 2 and p[0] in ("pgfault", "pgmajfault", "pswpin", "pswpout", "oom_kill"):
            out[p[0]] = int(p[1])
    return out


def filesystems(skip_network=True):
    from lshme.core.util import NETWORK_FS, PSEUDO_FS
    out, seen = {}, set()
    for line in _read("/proc/mounts").splitlines():
        f = line.split()
        if len(f) < 4:
            continue
        mp, dev, fstype = f[1].replace("\\040", " "), f[0], f[2]
        if fstype in PSEUDO_FS or mp in seen or mp.startswith(("/proc", "/sys", "/dev", "/run/user", "/snap/")):
            continue
        seen.add(mp)
        if fstype in NETWORK_FS and skip_network:
            out[mp] = {"fstype": fstype, "device": dev, "network": True}
            continue
        try:
            st = os.statvfs(mp)
        except OSError:
            continue
        if not st.f_blocks:
            continue
        size = st.f_blocks * st.f_frsize
        free = st.f_bavail * st.f_frsize
        used = (st.f_blocks - st.f_bfree) * st.f_frsize
        out[mp] = {"fstype": fstype, "device": dev, "size": size, "used": used, "avail": free,
                   "used_pct": round(100.0 * used / (used + free) if used + free else 0, 2),
                   "inodes": st.f_files, "inodes_free": st.f_ffree,
                   "inode_pct": round(100.0 * (st.f_files - st.f_ffree) / st.f_files, 2) if st.f_files else None,
                   "readonly": "ro" in f[3].split(",")}
    return out


def _rates(name, cur, now):
    prev = _mem.get(name)
    _mem[name] = (now, cur)
    if not prev or now - prev[0] <= 0.5:
        return None
    dt = now - prev[0]
    out = {}
    for k, v in cur.items():
        p = prev[1].get(k)
        if p is not None and len(p) == len(v):
            out[k] = [max(0, round((a - b) / dt, 1)) for a, b in zip(v, p)]
    return out


def sample(watch_sample, problems, now):
    """One complete measurement, from the watcher's own figures plus everything else /proc offers cheaply."""
    s = dict(watch_sample or {})
    data = {"cpu": s.get("cpu"), "cores": s.get("cores") or [], "mem_pct": s.get("mem"), "swap_pct": s.get("swap"),
            "load_per_cpu": s.get("load")}
    la = _read("/proc/loadavg").split()
    if len(la) >= 4:
        data["load"] = [float(la[0]), float(la[1]), float(la[2])]
        data["tasks"] = la[3]
    up = _read("/proc/uptime").split()
    if up:
        data["uptime_s"] = float(up[0])
    m = meminfo()
    data["memory"] = dict((k, m.get(k)) for k in ("MemTotal", "MemFree", "MemAvailable", "Buffers", "Cached", "SwapTotal", "SwapFree",
                                                   "Dirty", "Shmem", "Slab", "SReclaimable", "Committed_AS", "HugePages_Total") if k in m)
    nd = net_dev()
    data["net"] = dict((k, {"rx_bps": r[0], "tx_bps": r[4], "rx_pps": r[1], "tx_pps": r[5], "rx_err": r[2], "rx_drop": r[3], "tx_err": r[6],
                            "tx_drop": r[7]}) for k, r in (_rates("net", nd, now) or {}).items())
    data["net_counters"] = nd
    io = _rates("disk", disk_stats(), now)
    if io:
        data["disk_io"] = dict((k, {"reads_s": r[0], "read_Bps": round(r[1] * 512), "writes_s": r[2], "write_Bps": round(r[3] * 512),
                                    "busy_pct": min(100.0, round(r[4] / 10.0, 1))}) for k, r in io.items())
    vm = vm_counters()
    data["vm"] = vm
    data["filesystems"] = filesystems()
    data["problems"] = dict((k, {"severity": p.get("severity"), "component": p.get("component"), "message": p.get("message")})
                            for k, p in (problems or {}).items())
    return data


def top_programs(now, limit=15):
    """Programs (grouped by name) by CPU over the last interval and by memory."""
    tick = os.sysconf("SC_CLK_TCK") if hasattr(os, "sysconf") else 100
    page = os.sysconf("SC_PAGE_SIZE") if hasattr(os, "sysconf") else 4096
    cur = {}
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        st = _read("/proc/%s/stat" % pid)
        if not st or ")" not in st:
            continue
        name = st[st.find("(") + 1:st.rfind(")")]
        f = st[st.rfind(")") + 2:].split()
        try:
            cur[pid] = (name, int(f[11]) + int(f[12]), int(f[21]) * page)
        except (IndexError, ValueError):
            continue
    prev = _mem.get("procs")
    _mem["procs"] = (now, cur)
    groups = {}
    for pid, (name, ticks, rss) in cur.items():
        g = groups.setdefault(name, {"name": name, "processes": 0, "rss": 0, "cpu_pct": None})
        g["processes"] += 1
        g["rss"] += rss
        if prev and pid in prev[1] and prev[1][pid][0] == name:
            dt = max(1.0, now - prev[0])
            g["cpu_pct"] = round((g["cpu_pct"] or 0) + 100.0 * (ticks - prev[1][pid][1]) / tick / dt, 2)
    by_cpu = sorted((g for g in groups.values() if g["cpu_pct"] is not None), key=lambda g: -g["cpu_pct"])[:limit]
    by_mem = sorted(groups.values(), key=lambda g: -g["rss"])[:limit]
    return {"by_cpu": by_cpu, "by_memory": by_mem, "processes": len(cur), "programs": len(groups),
            "cpu_window_s": round(now - prev[0]) if prev else None}


TCP_STATES = {"01": "ESTABLISHED", "02": "SYN_SENT", "03": "SYN_RECV", "04": "FIN_WAIT1", "05": "FIN_WAIT2", "06": "TIME_WAIT",
              "07": "CLOSE", "08": "CLOSE_WAIT", "09": "LAST_ACK", "0A": "LISTEN", "0B": "CLOSING"}


def connections(limit=10):
    states, listen, peers = {}, set(), {}
    for fn in ("/proc/net/tcp", "/proc/net/tcp6"):
        for line in _read(fn).splitlines()[1:]:
            f = line.split()
            if len(f) < 4:
                continue
            st = TCP_STATES.get(f[3], f[3])
            states[st] = states.get(st, 0) + 1
            if st == "LISTEN":
                listen.add(int(f[1].rsplit(":", 1)[1], 16))
            elif st == "ESTABLISHED":
                peer = f[2].rsplit(":", 1)[0]
                peers[peer] = peers.get(peer, 0) + 1
    udp = sum(max(0, len(_read(fn).splitlines()) - 1) for fn in ("/proc/net/udp", "/proc/net/udp6"))
    top = sorted(peers.items(), key=lambda kv: -kv[1])[:limit]
    return {"tcp_states": states, "listening_tcp_ports": sorted(listen), "udp_sockets": udp,
            "busiest_peers": [{"peer_hex": p, "established": n} for p, n in top]}


def services(runner):
    rc, out = 1, ""
    try:
        res = runner.run(["systemctl", "list-units", "--type=service", "--all", "--no-legend", "--plain", "--no-pager"], timeout=20)
        rc, out = (res.rc, res.out) if hasattr(res, "rc") else (res[0], res[1])
    except Exception:
        return {"available": False}
    counts, failed = {}, []
    for line in (out or "").splitlines():
        f = line.split(None, 4)
        if len(f) >= 4:
            counts[f[3]] = counts.get(f[3], 0) + 1
            if f[2] == "failed":
                failed.append(f[0])
    return {"available": rc == 0, "by_state": counts, "failed": failed}


def watcher_tick(app, watch_sample, problems, now, state):
    """Called by the live watcher after every check."""
    if not _root:
        return
    write("sample", sample(watch_sample, problems, now), now)
    if now - state.get("raw_extra", 0) >= EXTRA_EVERY:
        state["raw_extra"] = now
        try:
            write("top", top_programs(now), now)
            write("connections", connections(), now)
            write("services", services(app.runner), now)
        except Exception:
            pass
    if now - state.get("raw_seal_check", 0) >= 600:
        state["raw_seal_check"] = now
        try:
            seal_due(now)
        except Exception:
            pass


# -- sealing ---------------------------------------------------------------------------------------------------------------------
def iter_file(path):
    opener = gzip.open if path.endswith(".gz") else open
    with opener(path, "rt", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    yield json.loads(line)
                except ValueError:
                    continue


def summarize(paths_, day, interval=60):
    start, end = day_bounds(day)
    counts, first, last, sample_ts = {}, None, None, []
    for p in paths_:
        if not os.path.isfile(p):
            continue
        for r in iter_file(p):
            counts[r.get("type", "?")] = counts.get(r.get("type", "?"), 0) + 1
            ts = r.get("ts", 0)
            first = ts if first is None or ts < first else first
            last = ts if last is None or ts > last else last
            if r.get("type") == "sample":
                sample_ts.append(ts)
    sample_ts.sort()
    gaps, prev = [], start
    for ts in sample_ts + [end]:
        if ts - prev > 3 * interval:
            gaps.append([time.strftime("%H:%M:%S", time.localtime(prev)), time.strftime("%H:%M:%S", time.localtime(min(ts, end - 1))),
                         round((ts - prev) / 60.0, 1)])
        prev = ts
    expected = (end - start) / float(interval)
    covered = min(100.0, 100.0 * len(sample_ts) / expected) if expected else 0
    return {"day": day, "from": "%s 00:00:00" % day, "to": "%s 23:59:59" % day, "records": sum(counts.values()), "by_type": counts,
            "first": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(first)) if first else None,
            "last": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(last)) if last else None,
            "samples": len(sample_ts), "interval_s": interval, "coverage_pct": round(covered, 2),
            "complete": covered >= 99.0 and not gaps, "gaps": gaps[:200], "gap_count": len(gaps)}


def seal_day(host, day, base=None, interval=60):
    """Compress a finished day and write its manifest. Late records for a sealed day are added as another gzip member."""
    plain = day_path(host, day, base)
    gz = plain + ".gz"
    if not os.path.isfile(plain):
        return None
    fd = os.open(plain, os.O_RDWR)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)                     # no writer can append while the day is sealed
        with open(plain, "rb") as src, open(gz, "ab") as raw:
            with gzip.GzipFile(filename=os.path.basename(plain), mode="wb", fileobj=raw, compresslevel=9) as out:
                while True:
                    block = src.read(1 << 20)
                    if not block:
                        break
                    out.write(block)
        os.chmod(gz, 0o640)
        os.remove(plain)
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
    man = summarize([gz], day, interval)
    man.update(host=host, file=os.path.basename(gz), bytes=os.path.getsize(gz), sha256=sha256(gz), schema=SCHEMA,
               sealed_at=time.strftime("%Y-%m-%d %H:%M:%S"))
    mp = os.path.join(os.path.dirname(gz), file_name(host, day)[:-len(".jsonl")] + ".manifest.json")
    with open(mp + ".tmp", "w", encoding="utf-8") as f:
        json.dump(man, f, indent=1, sort_keys=True)
    os.replace(mp + ".tmp", mp)
    return man


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def seal_due(now=None, base=None, interval=60, force=False):
    """Seal every day that ended at least two hours ago (force: every day before today)."""
    now = now or time.time()
    base = base or _root
    done = []
    today = day_of(now)
    for host, day, path in plain_days(base):
        if day >= today:
            continue
        if force or now >= day_bounds(day)[1] + SEAL_AFTER:
            m = seal_day(host, day, base, interval)
            if m:
                done.append(m)
    return done


def plain_days(base=None):
    base = base or _root
    out = []
    if not base or not os.path.isdir(base):
        return out
    for dirpath, _, files in os.walk(base):
        for f in files:
            m = NAME.match(f)
            if m and not m.group("gz"):
                out.append((m.group("host"), m.group("day"), os.path.join(dirpath, f)))
    return sorted(out)


# -- reading ---------------------------------------------------------------------------------------------------------------------
def days(base=None, host=None):
    """[{host, day, files, bytes, sealed, open, manifest}] newest first."""
    base = base or _root
    found = {}
    if not base or not os.path.isdir(base):
        return []
    for dirpath, _, files in os.walk(base):
        for f in files:
            m = NAME.match(f)
            if not m or (host and m.group("host") != safe(host)):
                continue
            key = (m.group("host"), m.group("day"))
            e = found.setdefault(key, {"host": key[0], "day": key[1], "files": [], "bytes": 0, "sealed": False, "open": False, "manifest": None})
            p = os.path.join(dirpath, f)
            e["files"].append(p)
            e["bytes"] += os.path.getsize(p)
            if m.group("gz"):
                e["sealed"] = True
                mp = p[:-len(".jsonl.gz")] + ".manifest.json"
                try:
                    with open(mp, encoding="utf-8") as fh:
                        e["manifest"] = json.load(fh)
                except (OSError, ValueError):
                    pass
            else:
                e["open"] = True
    return sorted(found.values(), key=lambda e: (e["day"], e["host"]), reverse=True)


def day_files(host, day, base=None):
    p = day_path(host, day, base)
    return [x for x in (p + ".gz", p) if os.path.isfile(x)]


def read(host, day, types=None, base=None):
    """Records of one day in time order (sealed part and anything written after sealing)."""
    recs = []
    for p in day_files(host, day, base):
        for r in iter_file(p):
            if not types or r.get("type") in types:
                recs.append(r)
    recs.sort(key=lambda r: r.get("ts", 0))
    return recs


def verify(base=None):
    """[(host, day, ok, detail)] for every sealed day: fingerprint and record count against the manifest."""
    out = []
    for e in days(base):
        if not e["sealed"]:
            continue
        gz = [p for p in e["files"] if p.endswith(".gz")][0]
        m = e["manifest"]
        if not m:
            out.append((e["host"], e["day"], False, "manifest missing"))
            continue
        if sha256(gz) != m.get("sha256"):
            out.append((e["host"], e["day"], False, "fingerprint does not match (file changed or damaged)"))
            continue
        n = sum(1 for _ in iter_file(gz))
        out.append((e["host"], e["day"], n == m.get("records"), "%d records" % n))
    return out


def prune(keep_days, now=None, base=None):
    """Delete days older than keep_days. Returns [(path, bytes)]."""
    now = now or time.time()
    cutoff = day_of(now - keep_days * 86400)
    gone = []
    for e in days(base):
        if e["day"] < cutoff:
            for p in e["files"] + ([p[:-len(".jsonl.gz")] + ".manifest.json" for p in e["files"] if p.endswith(".gz")]):
                if os.path.isfile(p):
                    gone.append((p, os.path.getsize(p)))
                    os.remove(p)
    return gone
