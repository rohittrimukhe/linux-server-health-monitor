# Linux Server Health Monitor Enterprise
# Copyright (c) 2026 Rohit Santosh Trimukhe. All rights reserved.
"""Live system statistics for the terminal dashboard and the web dashboard.

Reads /proc and /sys directly (no commands run), so a sample costs a few milliseconds. Rates (CPU %,
network and disk throughput) come from the difference between two samples, which is why a Sampler object
is kept between calls. `root` lets tests point it at a fake /proc tree."""
import os
import pwd
import re
import time

PSEUDO_FS = ("proc", "sysfs", "devtmpfs", "tmpfs", "devpts", "cgroup", "cgroup2", "securityfs", "debugfs", "tracefs",
             "configfs", "fusectl", "mqueue", "hugetlbfs", "pstore", "bpf", "autofs", "binfmt_misc", "selinuxfs",
             "overlay", "squashfs", "ramfs", "rpc_pipefs", "nsfs", "efivarfs", "fuse.gvfsd-fuse", "fuse.portal")
DISK_RX = re.compile(r"^(sd[a-z]+|vd[a-z]+|xvd[a-z]+|hd[a-z]+|nvme\d+n\d+|mmcblk\d+|dm-\d+|md\d+)$")
HISTORY = 120  # samples kept for the sparklines


def pct(part, whole):
    return 100.0 * part / whole if whole else 0.0


def fmt_size(n):
    n = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB", "PB"):
        if abs(n) < 1024 or unit == "PB":
            return ("%.0f %s" % (n, unit)) if unit == "B" else ("%.1f %s" % (n, unit))
        n /= 1024.0


def fmt_rate(bps):
    return fmt_size(bps) + "/s"


def fmt_uptime(seconds):
    s = int(seconds)
    d, s = divmod(s, 86400)
    h, s = divmod(s, 3600)
    m = s // 60
    return ("%dd %dh %dm" % (d, h, m)) if d else ("%dh %dm" % (h, m))


class Sampler(object):
    def __init__(self, root="/", statvfs=None, clk=None, pagesize=None, clock=time.time):
        self.root = root
        self.statvfs = statvfs or os.statvfs
        self.clk = clk or os.sysconf("SC_CLK_TCK")
        self.pagesize = pagesize or os.sysconf("SC_PAGE_SIZE")
        self.clock = clock
        self.prev = {}
        self.history = {"cpu": [], "mem": [], "rx": [], "tx": [], "load": []}

    # -- helpers ------------------------------------------------------------------------
    def read(self, path):
        try:
            with open(self.root.rstrip("/") + path, encoding="utf-8", errors="replace") as f:
                return f.read()
        except OSError:
            return ""

    def _keep(self, key, value):
        h = self.history[key]
        h.append(value)
        del h[:-HISTORY]

    # -- pieces -------------------------------------------------------------------------
    def cpu(self, now):
        cur = {}
        for line in self.read("/proc/stat").splitlines():
            p = line.split()
            if p and p[0].startswith("cpu"):
                cur[p[0]] = [int(x) for x in p[1:9]] + [0] * (8 - len(p[1:9]))
        old = self.prev.get("cpu") or {}
        self.prev["cpu"] = cur

        def calc(name):
            a, b = old.get(name), cur.get(name)
            if not a or not b:
                return {"total": 0.0, "user": 0.0, "system": 0.0, "iowait": 0.0, "steal": 0.0}
            d = [y - x for x, y in zip(a, b)]
            total = sum(d[:8])
            idle = d[3] + d[4]
            return {"total": pct(total - idle, total), "user": pct(d[0] + d[1], total), "system": pct(d[2] + d[5] + d[6], total),
                    "iowait": pct(d[4], total), "steal": pct(d[7], total)}
        cores = sorted((k for k in cur if k != "cpu"), key=lambda k: int(k[3:]))
        out = calc("cpu")
        out["per_core"] = [calc(k)["total"] for k in cores]
        out["cores"] = len(cores)
        return out

    def memory(self):
        m = {}
        for line in self.read("/proc/meminfo").splitlines():
            k, _, v = line.partition(":")
            if v.split():
                m[k] = int(v.split()[0]) * 1024
        total = m.get("MemTotal", 0)
        avail = m.get("MemAvailable", m.get("MemFree", 0) + m.get("Buffers", 0) + m.get("Cached", 0))
        used = max(0, total - avail)
        swap_total, swap_free = m.get("SwapTotal", 0), m.get("SwapFree", 0)
        return {"total": total, "used": used, "available": avail, "pct": pct(used, total),
                "cache": m.get("Buffers", 0) + m.get("Cached", 0),
                "swap_total": swap_total, "swap_used": swap_total - swap_free, "swap_pct": pct(swap_total - swap_free, swap_total)}

    def mounts(self):
        out, seen = [], set()
        for line in self.read("/proc/mounts").splitlines():
            p = line.split()
            if len(p) < 4 or p[2] in PSEUDO_FS:
                continue
            mp = p[1].replace("\\040", " ")
            if mp in seen or mp.startswith(("/proc", "/sys", "/run/user", "/var/lib/containers", "/var/lib/docker", "/snap")):
                continue
            seen.add(mp)
            try:
                st = self.statvfs(mp)
            except OSError:
                out.append({"mount": mp, "device": p[0], "fstype": p[2], "total": 0, "used": 0, "free": 0, "pct": 0.0,
                            "inode_pct": 0.0, "readonly": "ro" in p[3].split(","), "error": True})
                continue
            total = st.f_blocks * st.f_frsize
            if not total:
                continue
            free_all, avail = st.f_bfree * st.f_frsize, st.f_bavail * st.f_frsize
            used = total - free_all
            out.append({"mount": mp, "device": p[0], "fstype": p[2], "total": total, "used": used, "free": avail,
                        "pct": pct(used, used + avail), "inode_pct": pct(st.f_files - st.f_ffree, st.f_files),
                        "readonly": "ro" in p[3].split(","), "error": False})
        return sorted(out, key=lambda x: x["mount"])

    def network(self, now):
        cur = {}
        for line in self.read("/proc/net/dev").splitlines()[2:]:
            name, _, rest = line.partition(":")
            f = rest.split()
            if len(f) >= 16:
                cur[name.strip()] = (int(f[0]), int(f[8]), int(f[2]) + int(f[3]), int(f[10]) + int(f[11]))
        old, then = self.prev.get("net") or {}, self.prev.get("net_t")
        self.prev["net"], self.prev["net_t"] = cur, now
        dt = (now - then) if then else 0
        out = []
        for name, (rx, tx, rxe, txe) in sorted(cur.items()):
            o = old.get(name)
            rate = (lambda a, b: max(0.0, (a - b) / dt) if o and dt > 0 else 0.0)
            state = self.read("/sys/class/net/%s/operstate" % name).strip() or ("unknown" if name != "lo" else "up")
            out.append({"iface": name, "rx_bps": rate(rx, o[0] if o else 0), "tx_bps": rate(tx, o[1] if o else 0),
                        "rx_total": rx, "tx_total": tx, "errors": rxe + txe, "state": state})
        return out

    def disks(self, now):
        cur = {}
        for line in self.read("/proc/diskstats").splitlines():
            f = line.split()
            if len(f) >= 14 and DISK_RX.match(f[2]):
                cur[f[2]] = (int(f[5]), int(f[9]), int(f[12]))  # sectors read, sectors written, ms doing I/O
        old, then = self.prev.get("disk") or {}, self.prev.get("disk_t")
        self.prev["disk"], self.prev["disk_t"] = cur, now
        dt = (now - then) if then else 0
        out = []
        for name, (rs, ws, ms) in sorted(cur.items()):
            o = old.get(name)
            label = (self.read("/sys/block/%s/dm/name" % name).strip() if name.startswith("dm-") else "") or name
            ok = bool(o) and dt > 0
            out.append({"dev": name, "name": label, "read_bps": max(0.0, (rs - o[0]) * 512 / dt) if ok else 0.0,
                        "write_bps": max(0.0, (ws - o[1]) * 512 / dt) if ok else 0.0,
                        "util": min(100.0, max(0.0, (ms - o[2]) / (dt * 10.0))) if ok else 0.0})
        return out

    def processes(self, now, top=8):
        cur, total, running = {}, 0, 0
        try:
            pids = [p for p in os.listdir(self.root.rstrip("/") + "/proc") if p.isdigit()]
        except OSError:
            pids = []
        for pid in pids:
            raw = self.read("/proc/%s/stat" % pid)
            if ")" not in raw:
                continue
            head, _, tail = raw.rpartition(")")
            f = tail.split()
            if len(f) < 22:
                continue
            total += 1
            running += f[0] == "R"
            cur[pid] = (head.partition("(")[2], int(f[11]) + int(f[12]), int(f[21]) * self.pagesize)
        old, then = self.prev.get("procs") or {}, self.prev.get("procs_t")
        self.prev["procs"], self.prev["procs_t"] = dict((p, v[1]) for p, v in cur.items()), now
        dt = (now - then) if then else 0
        rows = []
        for pid, (name, ticks, rss) in cur.items():
            cpu = max(0.0, (ticks - old[pid]) / float(self.clk) / dt * 100.0) if pid in old and dt > 0 else 0.0
            rows.append({"pid": int(pid), "name": name, "cpu": cpu, "rss": rss, "user": self._owner(pid)})
        return {"total": total, "running": running,
                "top_cpu": sorted(rows, key=lambda r: -r["cpu"])[:top], "top_mem": sorted(rows, key=lambda r: -r["rss"])[:top]}

    def _owner(self, pid):
        try:
            return pwd.getpwuid(os.stat(self.root.rstrip("/") + "/proc/" + pid).st_uid).pw_name
        except (OSError, KeyError):
            return "?"

    # -- everything ---------------------------------------------------------------------
    def sample(self, processes=True):
        now = self.clock()
        load = (self.read("/proc/loadavg").split() + ["0", "0", "0"])[:3]
        up = self.read("/proc/uptime").split()
        cpu = self.cpu(now)
        mem = self.memory()
        net = self.network(now)
        real = [n for n in net if n["iface"] != "lo"]
        self._keep("cpu", cpu["total"])
        self._keep("mem", mem["pct"])
        self._keep("rx", sum(n["rx_bps"] for n in real))
        self._keep("tx", sum(n["tx_bps"] for n in real))
        self._keep("load", float(load[0]))
        out = {"ts": now, "hostname": (self.read("/proc/sys/kernel/hostname").strip() or "?"),
               "uptime": float(up[0]) if up else 0.0, "load": [float(x) for x in load],
               "cpu": cpu, "mem": mem, "mounts": self.mounts(), "net": net, "disks": self.disks(now),
               "history": dict((k, list(v)) for k, v in self.history.items())}
        if processes:
            out["procs"] = self.processes(now)
        return out


def level(value, warn=80.0, crit=90.0):
    """'ok' | 'warn' | 'crit' for colouring a percentage."""
    return "crit" if value >= crit else "warn" if value >= warn else "ok"
