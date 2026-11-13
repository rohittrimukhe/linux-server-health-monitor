# Linux Server Health Monitor Enterprise
# Copyright (c) 2026 LRS Services Pvt Ltd, Mumbai. All rights reserved.
"""Disks, filesystems, mount points, inodes, LVM, multipath, RAID, NFS and disk I/O."""
import re

from lshme.core.util import (NETWORK_FS, PSEUDO_FS, fmt_bytes, parse_pairs, pct, severity_for,
                             to_float, to_int)

NAME = "storage"
TITLE = "Storage & Filesystems"
WEIGHT = 20

RO_EXPECTED_FS = {"iso9660", "squashfs", "udf"}
RO_EXPECTED_MOUNT_PREFIXES = ("/sys", "/proc", "/snap/", "/run/credentials")


def mount_table(ctx):
    mounts = {}
    for line in ctx.read("/proc/mounts").splitlines():
        p = line.split()
        if len(p) >= 4:
            mp = p[1].replace("\\040", " ")
            mounts[mp] = {"device": p[0], "fstype": p[2], "options": p[3].split(",")}
    return mounts


def filesystems(ctx, mounts):
    out = []
    # -l would skip NFS; we want them, but guard against hung mounts with a timeout
    r = ctx.run(["df", "-P", "-T", "-B1"], timeout=45)
    if not r.out:
        return out, r
    for line in r.out.splitlines()[1:]:
        p = line.split()
        if len(p) < 7:
            continue
        mp = " ".join(p[6:])
        if p[1] in PSEUDO_FS or mp.startswith(("/proc", "/sys", "/dev/shm", "/run/user")):
            continue
        size, used, free = to_int(p[2], 0), to_int(p[3], 0), to_int(p[4], 0)
        out.append({"device": p[0], "fstype": p[1], "size": size, "used": used, "free": free,
                    "used_percent": to_float(p[5], pct(used, used + free)), "mountpoint": mp,
                    "options": mounts.get(mp, {}).get("options", [])})
    return out, r


def inodes(ctx):
    out = {}
    r = ctx.run(["df", "-P", "-i"], timeout=45)
    for line in r.out.splitlines()[1:]:
        p = line.split()
        if len(p) >= 6 and p[4] not in ("-",):
            out[" ".join(p[5:])] = {"total": to_int(p[1], 0), "used": to_int(p[2], 0),
                                    "used_percent": to_float(p[4], None)}
    return out


def fstab_missing(ctx, mounts):
    missing = []
    for line in ctx.read("/etc/fstab").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        p = line.split()
        if len(p) < 4:
            continue
        mp, fstype, opts = p[1], p[2], p[3].split(",")
        if fstype in ("swap", "none") or mp in ("none", "swap") or "noauto" in opts or not mp.startswith("/"):
            continue
        if mp.rstrip("/") or mp == "/":
            norm = mp.rstrip("/") or "/"
            if norm not in mounts:
                missing.append({"mountpoint": norm, "device": p[0], "fstype": fstype})
    return missing


def lvm(ctx):
    data = {"pvs": [], "vgs": [], "lvs": [], "available": ctx.has("lvs")}
    if not data["available"]:
        return data
    common = ["--noheadings", "--units", "b", "--nosuffix", "--separator", "|"]
    r = ctx.run(["pvs"] + common + ["-o", "pv_name,vg_name,pv_size,pv_free,pv_attr"])
    for line in r.lines():
        p = [x.strip() for x in line.split("|")]
        if len(p) >= 5:
            data["pvs"].append({"pv": p[0], "vg": p[1], "size": to_int(p[2], 0), "free": to_int(p[3], 0), "attr": p[4]})
    r = ctx.run(["vgs"] + common + ["-o", "vg_name,vg_size,vg_free,pv_count,lv_count,vg_attr"])
    for line in r.lines():
        p = [x.strip() for x in line.split("|")]
        if len(p) >= 6:
            data["vgs"].append({"vg": p[0], "size": to_int(p[1], 0), "free": to_int(p[2], 0),
                                "pv_count": p[3], "lv_count": p[4], "attr": p[5]})
    r = ctx.run(["lvs"] + common + ["-o", "vg_name,lv_name,lv_size,lv_attr,data_percent,origin,devices"])
    for line in r.lines():
        p = [x.strip() for x in line.split("|")]
        if len(p) >= 5:
            data["lvs"].append({"vg": p[0], "lv": p[1], "size": to_int(p[2], 0), "attr": p[3],
                                "data_percent": to_float(p[4], None), "origin": p[5] if len(p) > 5 else "",
                                "devices": p[6] if len(p) > 6 else ""})
    return data


def multipath(ctx):
    if not ctx.has("multipath"):
        return {"available": False, "maps": []}
    r = ctx.run(["multipath", "-ll"], timeout=60)
    maps, cur = [], None
    for line in r.out.splitlines():
        m = re.match(r"^(\S+)\s+\(([^)]*)\)\s+(dm-\d+)\s*(.*)", line) or re.match(r"^(\S+)\s+(dm-\d+)\s*(.*)", line)
        if m and not line.startswith((" ", "|", "`")) and "dm-" in line:
            cur = {"name": m.group(1), "wwid": m.group(2) if m.lastindex >= 4 else "", "paths": 0,
                   "active": 0, "failed": 0, "detail": line.strip()}
            maps.append(cur)
            continue
        if cur is not None and re.search(r"\d+:\d+:\d+:\d+\s+\S+\s+\d+:\d+", line):
            cur["paths"] += 1
            low = line.lower()
            if "failed" in low or "faulty" in low or "offline" in low:
                cur["failed"] += 1
            elif "active" in low and "ready" in low:
                cur["active"] += 1
    return {"available": True, "maps": maps, "raw": r.out[:20000], "error": "" if r.ok else r.brief_error()}


def mdraid(ctx):
    text = ctx.read("/proc/mdstat")
    arrays = []
    lines = text.splitlines()
    for i, line in enumerate(lines):
        m = re.match(r"^(md\S+)\s*:\s*(\w+)\s+(\S+)?", line)
        if m:
            status = lines[i + 1] if i + 1 < len(lines) else ""
            bm = re.search(r"\[([U_]+)\]", status)
            following = " ".join(lines[i + 1:i + 3])
            arrays.append({"name": m.group(1), "state": m.group(2), "level": m.group(3) or "",
                           "members": bm.group(1) if bm else "",
                           "degraded": bool(bm and "_" in bm.group(1)) or "(F)" in line,
                           "resync": "resync" in following or "recovery" in following})
    return arrays


def network_mount_health(ctx, fs_list):
    """Probe network filesystems with a short timeout to catch hung NFS/CIFS mounts."""
    out = []
    for fs in fs_list:
        if fs["fstype"] in NETWORK_FS:
            r = ctx.run(["stat", "-f", "-c", "%T", fs["mountpoint"]], timeout=10)
            out.append({"mountpoint": fs["mountpoint"], "device": fs["device"], "fstype": fs["fstype"],
                        "responsive": r.ok, "error": "" if r.ok else r.brief_error()})
    return out


def diskstats(ctx):
    d = {}
    for line in ctx.read("/proc/diskstats").splitlines():
        p = line.split()
        if len(p) >= 14 and not re.match(r"^(loop|ram|sr|fd)", p[2]):
            d[p[2]] = [int(x) for x in p[3:14]]
    return d


def disk_io(ctx, seconds):
    """Utilisation and average latency per whole disk sampled over a short interval."""
    a = diskstats(ctx)
    ctx.runner.sleep(seconds)
    b = diskstats(ctx)
    ms = seconds * 1000.0
    out = []
    for dev, vb in b.items():
        va = a.get(dev)
        if not va or re.match(r"^(sd[a-z]+|vd[a-z]+|xvd[a-z]+|hd[a-z]+)\d+$", dev) or re.match(r"^nvme\d+n\d+p\d+$", dev):
            continue  # skip partitions
        reads, writes = vb[0] - va[0], vb[4] - va[4]
        rd_ms, wr_ms = vb[3] - va[3], vb[7] - va[7]
        io_ms = vb[9] - va[9]
        ios = reads + writes
        out.append({"device": dev, "util_percent": round(min(100.0, 100.0 * io_ms / ms), 1),
                    "await_ms": round((rd_ms + wr_ms) / float(ios), 2) if ios else 0.0,
                    "read_bps": (vb[2] - va[2]) * 512 / seconds, "write_bps": (vb[6] - va[6]) * 512 / seconds,
                    "iops": round(ios / seconds, 1)})
    return sorted(out, key=lambda x: -x["util_percent"])


def smart_health(ctx, disks):
    out = []
    if not ctx.has("smartctl") or not ctx.runner.is_root():
        return out
    for dev in disks:
        r = ctx.run(["smartctl", "-H", "/dev/" + dev], timeout=30)
        if r.missing:
            break
        m = re.search(r"(SMART overall-health self-assessment test result|SMART Health Status):\s*(.+)", r.out)
        if m:
            status = m.group(2).strip()
            out.append({"device": dev, "status": status, "ok": status.upper() in ("PASSED", "OK")})
    return out


def overrides(ctx):
    o = {}
    for mp, val in ctx.cfg.section("thresholds", "disk_overrides").items():
        parts = [to_float(x) for x in val.split(",")]
        if len(parts) == 2 and None not in parts:
            o[mp] = parts
    return o


def run(ctx, res):
    mounts = mount_table(ctx)
    fs_list, df_res = filesystems(ctx, mounts)
    if not fs_list:
        res.status = "PARTIAL"
        res.error("df failed: %s" % df_res.brief_error())
        res.add("UNKNOWN", "DF_FAILED", "filesystems", "Filesystem usage could not be read",
                evidence=df_res.brief_error(),
                recommendation="A hung network mount often blocks df; check NFS/CIFS mounts.")
    ino = inodes(ctx)
    lv = lvm(ctx)
    mp_data = multipath(ctx)
    md = mdraid(ctx)
    netfs = network_mount_health(ctx, fs_list)
    missing = fstab_missing(ctx, mounts)
    io = disk_io(ctx, ctx.t("disk_io", "sample_seconds", 1))
    block = []
    r = ctx.run(["lsblk", "-P", "-b", "-o", "NAME,KNAME,TYPE,SIZE,FSTYPE,MOUNTPOINT,MODEL,ROTA"])
    for line in r.lines():
        block.append(parse_pairs(line))
    disks = [b.get("KNAME") or b.get("NAME") for b in block if b.get("TYPE") == "disk"]
    smart = smart_health(ctx, disks)
    res.data = {"filesystems": fs_list, "inodes": ino, "lvm": lv, "multipath": mp_data,
                "mdraid": md, "network_mounts": netfs, "fstab_missing": missing, "disk_io": io,
                "block_devices": block, "smart": smart}

    ov = overrides(ctx)
    dw, dc = ctx.t("disk", "warning", 80), ctx.t("disk", "critical", 90)
    iw, ic = ctx.t("inode", "warning", 80), ctx.t("inode", "critical", 90)
    rows = []
    worst = 0.0
    for fs in fs_list:
        w, c = ov.get(fs["mountpoint"], (dw, dc))
        p = fs["used_percent"] or 0.0
        worst = max(worst, p)
        ip = ino.get(fs["mountpoint"], {}).get("used_percent")
        sev = severity_for(p, w, c)
        status = sev or "OK"
        ro = "ro" in fs["options"]
        if ro and fs["fstype"] not in RO_EXPECTED_FS and not fs["mountpoint"].startswith(RO_EXPECTED_MOUNT_PREFIXES):
            status = "READ-ONLY"
            res.add("CRITICAL", "FS_READ_ONLY", fs["mountpoint"],
                    "Filesystem %s is mounted read-only" % fs["mountpoint"],
                    evidence="%s %s %s" % (fs["device"], fs["fstype"], ",".join(fs["options"])),
                    recommendation="A filesystem remounted read-only usually indicates I/O or filesystem errors; check dmesg/journal before remounting.")
        rows.append([fs["mountpoint"], fs["device"], fs["fstype"], fmt_bytes(fs["size"]), fmt_bytes(fs["used"]),
                     fmt_bytes(fs["free"]), "%.1f%%" % p, "%s%%" % ip if ip is not None else "-", status])
        res.metric("fs.used_percent:" + fs["mountpoint"], p)
        res.metric("fs.used_bytes:" + fs["mountpoint"], fs["used"])
        res.metric("fs.size_bytes:" + fs["mountpoint"], fs["size"])
        if sev:
            res.add(sev, "DISK_USAGE", fs["mountpoint"],
                    "Filesystem %s is %.1f%% full (%s free of %s)" % (fs["mountpoint"], p, fmt_bytes(fs["free"]), fmt_bytes(fs["size"])),
                    recommendation="Remove or archive old files, or extend the logical volume/filesystem. Thresholds: warning %s%%, critical %s%%." % (w, c))
        if ip is not None:
            isev = severity_for(ip, iw, ic)
            if isev:
                res.add(isev, "INODE_USAGE", fs["mountpoint"], "Inode usage on %s is %.1f%%" % (fs["mountpoint"], ip),
                        recommendation="Many small files exhaust inodes; clean up spool/session/audit files.")
    res.metric("fs.max_used_percent", worst)
    res.table("Filesystems and mount points",
              ["Mount point", "Device", "Type", "Size", "Used", "Free", "Use%", "Inode%", "Status"], rows, kind="summary")

    for m in missing:
        res.add("CRITICAL", "FSTAB_NOT_MOUNTED", m["mountpoint"],
                "%s is defined in /etc/fstab but not mounted" % m["mountpoint"], evidence="%s %s" % (m["device"], m["fstype"]),
                recommendation="Verify the device/LUN is visible and mount it; check for SAN or NFS connectivity loss.")
    for n in netfs:
        if not n["responsive"]:
            res.add("CRITICAL", "NETFS_UNRESPONSIVE", n["mountpoint"],
                    "Network filesystem %s (%s) is not responding" % (n["mountpoint"], n["fstype"]), evidence=n["error"],
                    recommendation="Check connectivity to the NFS/CIFS server; hung mounts can block applications and backups.")

    if lv["available"]:
        res.table("LVM volume groups", ["VG", "Size", "Free", "PVs", "LVs", "Attr"],
                  [[v["vg"], fmt_bytes(v["size"]), fmt_bytes(v["free"]), v["pv_count"], v["lv_count"], v["attr"]] for v in lv["vgs"]])
        res.table("LVM logical volumes", ["VG", "LV", "Size", "Attr", "Data%", "Devices"],
                  [[x["vg"], x["lv"], fmt_bytes(x["size"]), x["attr"], x["data_percent"] if x["data_percent"] is not None else "", x["devices"]] for x in lv["lvs"]])
        res.table("LVM physical volumes", ["PV", "VG", "Size", "Free", "Attr"],
                  [[x["pv"], x["vg"], fmt_bytes(x["size"]), fmt_bytes(x["free"]), x["attr"]] for x in lv["pvs"]])
        for x in lv["lvs"]:
            if x["data_percent"] is not None and x["attr"][:1] in ("s", "S", "t", "V"):
                sev = severity_for(x["data_percent"], 80, 95)
                if sev:
                    kind = "snapshot" if x["attr"][:1] in ("s", "S") else "thin volume/pool"
                    res.add(sev, "LV_DATA_FULL", "%s/%s" % (x["vg"], x["lv"]),
                            "LVM %s %s/%s is %.1f%% full" % (kind, x["vg"], x["lv"], x["data_percent"]),
                            recommendation="Extend or remove the snapshot/thin pool before it fills and becomes invalid.")
        for p in lv["pvs"]:
            if "m" in p["attr"][2:3]:
                res.add("CRITICAL", "PV_MISSING", p["pv"], "LVM physical volume %s is missing" % p["pv"],
                        recommendation="A missing PV puts its volume group at risk; check the underlying disk/LUN.")
    else:
        res.skip("LVM tools not installed")

    if mp_data["available"]:
        res.table("Multipath devices", ["Map", "WWID", "Paths", "Active", "Failed"],
                  [[m["name"], m["wwid"], m["paths"], m["active"], m["failed"]] for m in mp_data["maps"]])
        for m in mp_data["maps"]:
            if m["failed"] and m["failed"] >= m["paths"]:
                res.add("CRITICAL", "MULTIPATH_ALL_FAILED", m["name"], "All %d paths of %s have failed" % (m["paths"], m["name"]),
                        recommendation="The LUN is unreachable; engage SAN/storage team immediately.")
            elif m["failed"]:
                res.add("WARNING", "MULTIPATH_PATH_FAILED", m["name"], "%d of %d paths of %s have failed" % (m["failed"], m["paths"], m["name"]),
                        recommendation="Check HBA/FC switch ports and SAN zoning; redundancy is reduced.")
            elif m["paths"] == 1:
                res.add("WARNING", "MULTIPATH_SINGLE_PATH", m["name"], "%s has only one path" % m["name"],
                        recommendation="Verify zoning; a single path has no redundancy.")
    else:
        res.skip("multipath not installed")

    if md:
        res.table("Software RAID (md)", ["Array", "State", "Level", "Members", "Degraded"],
                  [[a["name"], a["state"], a["level"], a["members"], "YES" if a["degraded"] else "no"] for a in md])
        for a in md:
            if a["degraded"]:
                res.add("CRITICAL", "MDRAID_DEGRADED", a["name"], "Software RAID %s is degraded [%s]" % (a["name"], a["members"]),
                        recommendation="Replace the failed member disk and re-add it to the array.")
    for s in smart:
        if not s["ok"]:
            res.add("CRITICAL", "SMART_FAILING", s["device"], "SMART health of /dev/%s: %s" % (s["device"], s["status"]),
                    recommendation="The disk predicts failure; replace it and verify backups.")
    if io:
        res.table("Disk I/O (sampled)", ["Device", "Util%", "Await ms", "IOPS", "Read/s", "Write/s"],
                  [[d["device"], d["util_percent"], d["await_ms"], d["iops"], fmt_bytes(d["read_bps"]), fmt_bytes(d["write_bps"])] for d in io[:20]])
        for d in io:
            res.metric("disk.util_percent:" + d["device"], d["util_percent"])
            if d["util_percent"] >= ctx.t("disk_io", "util_warning", 90) and d["await_ms"] >= ctx.t("disk_io", "await_warning_ms", 50):
                res.add("WARNING", "DISK_IO_SATURATED", d["device"],
                        "Disk %s is %.0f%% busy with %.1f ms average latency" % (d["device"], d["util_percent"], d["await_ms"]),
                        recommendation="Identify I/O-heavy processes (iotop) and review storage performance.")
    res.summary = "%d filesystems (fullest %.1f%%), %d multipath maps, %d md arrays" % (
        len(fs_list), worst, len(mp_data.get("maps", [])), len(md))
