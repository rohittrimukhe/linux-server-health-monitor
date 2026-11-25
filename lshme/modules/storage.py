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
            dm = re.search(r"\b(dm-\d+)\b", line)
            cur = {"name": m.group(1), "wwid": m.group(2) if m.lastindex >= 4 else "", "paths": 0,
                   "active": 0, "failed": 0, "detail": line.strip(), "dm": dm.group(1) if dm else "", "devs": []}
            maps.append(cur)
            continue
        if cur is not None and re.search(r"\d+:\d+:\d+:\d+\s+\S+\s+\d+:\d+", line):
            cur["paths"] += 1
            dv = re.search(r"\d+:\d+:\d+:\d+\s+(\S+)\s+\d+:\d+", line)
            if dv:
                cur["devs"].append(dv.group(1))
            low = line.lower()
            if "failed" in low or "faulty" in low or "offline" in low:
                cur["failed"] += 1
            elif "active" in low and "ready" in low:
                cur["active"] += 1
    return {"available": True, "maps": maps, "raw": r.out[:20000], "error": "" if r.ok else r.brief_error()}


def san_links(ctx):
    """How this server is connected to SAN storage: iSCSI sessions (one per portal) and Fibre Channel ports."""
    out = {"iscsi": [], "fc": []}
    if ctx.has("iscsiadm"):
        r = ctx.run(["iscsiadm", "-m", "session", "-P", "1"], timeout=30)
        target, cur = "", None
        for line in r.out.splitlines():
            m = re.match(r"\s*Target:\s*(\S+)", line)
            if m:
                target, cur = m.group(1), None
                continue
            m = re.match(r"\s*Current Portal:\s*(\S+)", line)
            if m:
                cur = {"target": target, "portal": m.group(1).split(",")[0], "state": ""}
                out["iscsi"].append(cur)
                continue
            m = re.match(r"\s*iSCSI Session State:\s*(\S+)", line)
            if m and cur is not None and not cur["state"]:
                cur["state"] = m.group(1)
    for d in sorted(x.rstrip("/") for x in (ctx.runner.glob("/sys/class/fc_host/host*") or []) if re.match(r"^/sys/class/fc_host/host\d+/?$", x)):
        port = {"host": d.rstrip("/").rsplit("/", 1)[-1], "state": ctx.read(d + "/port_state", "").strip(),
                "wwpn": ctx.read(d + "/port_name", "").strip(), "speed": ctx.read(d + "/speed", "").strip()}
        for k in ("link_failure_count", "loss_of_sync_count", "loss_of_signal_count", "invalid_crc_count"):
            v = ctx.read(d + "/statistics/" + k, "").strip()
            try:
                port[k] = int(v, 16) if v.startswith("0x") else int(v)
            except ValueError:
                pass
        out["fc"].append(port)
    out["nvme"] = []
    if ctx.has("nvme"):                                          # NVMe over Fabrics: each controller path and its state
        r = ctx.run(["nvme", "list-subsys"], timeout=30)
        sub = ""
        for line in r.out.splitlines():
            m = re.match(r"^(nvme-subsys\d+)\s*-\s*NQN=(\S+)", line.strip())
            if m:
                sub = m.group(2)
                continue
            m = re.search(r"\+-\s*(nvme\d+)\s+(\w+)\s+(\S+)\s+(\w+)", line)
            if m and m.group(2).lower() in ("tcp", "rdma", "fc"):
                out["nvme"].append({"subsystem": sub, "controller": m.group(1), "transport": m.group(2), "address": m.group(3), "state": m.group(4).lower()})
    out["scsi_bad"] = []
    for d in ctx.runner.glob("/sys/block/sd*") or []:
        if not re.match(r"^/sys/block/sd[a-z]+$", d.rstrip("/")):
            continue
        st = ctx.read(d.rstrip("/") + "/device/state", "").strip().lower()
        if st and st != "running":
            out["scsi_bad"].append({"device": d.rstrip("/").rsplit("/", 1)[-1], "state": st})
    out["dm_suspended"] = []
    if ctx.has("dmsetup"):
        r = ctx.run(["dmsetup", "info", "-c", "--noheadings", "-o", "name,suspended", "--separator", "|"], timeout=30)
        for line in r.out.splitlines():
            p = [x.strip() for x in line.split("|")]
            if len(p) == 2 and p[1].lower() == "suspended":
                out["dm_suspended"].append(p[0])
    out["multipathd"] = ""
    if ctx.has("systemctl") and ctx.has("multipath"):
        out["multipathd"] = ctx.run(["systemctl", "is-active", "multipathd"], timeout=15).out.strip()
    return out


def map_users(ctx, m, fs_list, lv):
    """What on this server sits on a multipath map: mounted filesystems, LVM physical volumes, anything holding its dm device."""
    rx = re.compile(r"^/dev/mapper/%s(p?\d+)?$" % re.escape(m["name"]))
    users = ["%s (mounted)" % f["mountpoint"] for f in fs_list if rx.match(f.get("device", ""))]
    users += [("LVM volume group %s" % p["vg"]) if p.get("vg") else ("LVM physical volume %s" % p["pv"]) for p in lv.get("pvs", []) if rx.match(p.get("pv", ""))]
    if not users and m.get("dm"):
        held = [h for h in (ctx.runner.listdir("/sys/block/%s/holders" % m["dm"]) or []) if h]
        if held:
            users.append("held by %s" % ", ".join(held))
    return users


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
        for x in lv["lvs"]:
            if len(x.get("attr", "")) > 8 and x["attr"][8] == "p":
                res.add("CRITICAL", "LV_PARTIAL", "%s/%s" % (x["vg"], x["lv"]), "LVM logical volume %s/%s is partial: part of it is on a missing disk" % (x["vg"], x["lv"]),
                        recommendation="Data on the missing part cannot be read; restore the disk or LUN.")
        for v in lv["vgs"]:
            if len(v.get("attr", "")) > 3 and v["attr"][3] == "p":
                res.add("CRITICAL", "VG_PARTIAL", v["vg"], "LVM volume group %s is partial: one of its disks is missing" % v["vg"],
                        recommendation="Logical volumes on the missing disk cannot be used; restore the disk or LUN before changing the volume group.")
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
                users = map_users(ctx, m, fs_list, lv)
                m["users"] = users
                if users:
                    res.add("CRITICAL", "MULTIPATH_ALL_FAILED", m["name"], "All %d paths of %s have failed - in use: %s" % (m["paths"], m["name"], "; ".join(users)),
                            recommendation="Data on it cannot be read or written. Restore the SAN connection or mapping for this server; "
                                           "if the LUN was removed on purpose, move or unmount what uses it first.")
                else:
                    res.add("WARNING", "MULTIPATH_STALE", m["name"], "All %d paths of %s have failed and nothing on this server uses it - most likely a LUN unmapped or deleted on the SAN"
                            % (m["paths"], m["name"]),
                            recommendation="Ask the storage team whether this LUN is still meant for this server. Until the stale entry is cleared on the host it adds I/O errors to the logs. Paths: %s." % (", ".join(m.get("devs") or []) or "-"))
            elif m["failed"]:
                res.add("WARNING", "MULTIPATH_PATH_FAILED", m["name"], "%d of %d paths of %s have failed" % (m["failed"], m["paths"], m["name"]),
                        recommendation="Check HBA/FC switch ports and SAN zoning; redundancy is reduced.")
            elif m["paths"] == 1:
                res.add("WARNING", "MULTIPATH_SINGLE_PATH", m["name"], "%s has only one path" % m["name"],
                        recommendation="Verify zoning; a single path has no redundancy.")
    else:
        res.skip("multipath not installed")

    links = san_links(ctx)
    if links["iscsi"]:
        res.table("iSCSI sessions", ["Target", "Portal", "State"], [[x["target"], x["portal"], x["state"]] for x in links["iscsi"]])
        for x in links["iscsi"]:
            if x["state"] and x["state"].upper() != "LOGGED_IN":
                res.add("WARNING", "ISCSI_SESSION_DOWN", "%s@%s" % (x["target"].split(":")[-1], x["portal"]), "iSCSI session to %s via %s is %s" % (x["target"], x["portal"], x["state"]),
                        recommendation="Check the network path to the storage port; the remaining paths carry the I/O until it is back.")
    if links["fc"]:
        res.table("Fibre Channel ports", ["Port", "State", "WWPN", "Speed"], [[x["host"], x["state"], x["wwpn"], x["speed"]] for x in links["fc"]])
        online = [x for x in links["fc"] if x["state"].lower() == "online"]
        if mp_data.get("maps") and not online:
            res.add("CRITICAL", "FC_ALL_DOWN", "fibre channel", "No Fibre Channel port is online (%s)" % ", ".join("%s %s" % (x["host"], x["state"]) for x in links["fc"]),
                    recommendation="The server has lost its SAN connection: check cables, the FC switch and the HBA.")
        elif online:
            for x in links["fc"]:
                if x["state"] and x["state"].lower() != "online":
                    res.add("INFO", "FC_PORT_DOWN", x["host"], "Fibre Channel port %s is %s (another port is online)" % (x["host"], x["state"]),
                            recommendation="If this port should be connected, check its cable and switch port; an unused port can be ignored.")
    for x in links["fc"]:
        for k in ("link_failure_count", "loss_of_sync_count", "loss_of_signal_count", "invalid_crc_count"):
            if k in x:
                res.metric("fc.%s:%s" % (k, x["host"]), x[k])
    if links.get("nvme"):
        res.table("NVMe over Fabrics paths", ["Subsystem", "Controller", "Transport", "Address", "State"],
                  [[x["subsystem"], x["controller"], x["transport"], x["address"], x["state"]] for x in links["nvme"]])
        for sub in sorted(set(x["subsystem"] for x in links["nvme"])):
            paths = [x for x in links["nvme"] if x["subsystem"] == sub]
            live = [x for x in paths if x["state"] == "live"]
            if not live:
                res.add("CRITICAL", "NVME_ALL_PATHS_DOWN", sub, "No NVMe over Fabrics path to %s is live (%s)" % (sub, ", ".join("%s %s" % (x["controller"], x["state"]) for x in paths)),
                        recommendation="The namespaces of this subsystem cannot be reached; check the storage network and the target.")
            elif len(live) < len(paths):
                res.add("WARNING", "NVME_PATH_DOWN", sub, "%d of %d NVMe over Fabrics paths to %s are not live" % (len(paths) - len(live), len(paths), sub),
                        recommendation="Redundancy is reduced; check the storage network port of the paths that are down.")
    for x in links.get("scsi_bad", []):
        res.add("WARNING", "SCSI_DEVICE_OFFLINE", x["device"], "Disk path %s is %s at the kernel level" % (x["device"], x["state"]),
                recommendation="The kernel stopped using this path; check the cable, HBA or storage port it goes through.")
    for name in links.get("dm_suspended", []):
        res.add("CRITICAL", "DM_SUSPENDED", name, "Device-mapper device %s is suspended: every read and write to it waits" % name,
                recommendation="A suspended device usually means an interrupted LVM or multipath change; find out who suspended it before resuming it.")
    if mp_data.get("maps") and links.get("multipathd") and links["multipathd"] != "active":
        res.add("CRITICAL", "MULTIPATHD_DOWN", "multipathd", "multipathd is %s while %d multipath disks are in use" % (links["multipathd"], len(mp_data["maps"])),
                recommendation="Without multipathd a failed path is not replaced by a working one; start it (systemctl start multipathd).")
    res.data["san_links"] = links
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
