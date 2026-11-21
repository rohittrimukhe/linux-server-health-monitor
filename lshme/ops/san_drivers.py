# Linux Server Health Monitor Enterprise
# Copyright (c) 2026 LRS Services Pvt Ltd, Mumbai. All rights reserved.
"""More SAN drivers (see lshme.ops.san for the common shape): Dell Unity, Dell PowerStore, HPE 3PAR / Primera / Alletra 9000,
Pure Storage FlashArray, IBM FlashSystem / Storwize / SVC, Huawei OceanStor / Dorado. Read-only calls only."""
from lshme.ops.san import SanError, _num, _pct, basic, empty, tolerant


def _st(ok, value):
    return "optimal" if ok else str(value or "unknown").lower()


def unity(a, host, http):
    """Dell Unity / Unity XT: Unisphere REST API (port 443), a local user with the Operator role."""
    base = "https://%s:%d/api/types" % (host, a["port"])
    hdr = dict(basic(a["username"], a["password"]), **{"X-EMC-REST-CLIENT": "true"})
    g = tolerant(lambda path: [e.get("content", {}) for e in (http.get(base + path, hdr, a["verify_tls"]) or {}).get("entries", [])], list)
    hv = lambda x: (x.get("health") or {}).get("value") or 0
    good = lambda x: hv(x) in (5, 7)                         # 5 OK, 7 OK but; 10+ degraded, minor, major, critical
    out = empty(a)
    s0 = (g("/system/instances?fields=name,model,serialNumber,health") or [{}])[0]
    info = (g("/basicSystemInfo/instances?fields=softwareVersion") or [{}])[0]
    out.update(name=s0.get("name") or a["name"], model=s0.get("model", ""), serial=s0.get("serialNumber", ""), firmware=info.get("softwareVersion", ""),
               status=_st(good(s0), ((s0.get("health") or {}).get("descriptions") or ["needs attention"])[0]))
    for c in g("/storageProcessor/instances?fields=name,health"):
        out["controllers"].append({"name": c.get("name", "SP"), "status": _st(good(c), "needs attention"), "detail": ""})
    for d in g("/disk/instances?fields=name,health,size,diskTechnology"):
        out["drives"].append({"slot": d.get("name", "?"), "status": _st(good(d), "failed" if hv(d) >= 20 else "degraded"), "media": str(d.get("diskTechnology", "")),
                              "capacity": _num(d.get("size")), "hot_spare": False, "predictive_failure": False, "wear_percent": None})
    for p in g("/pool/instances?fields=name,sizeTotal,sizeUsed,sizeFree,health,raidType"):
        total, used = _num(p.get("sizeTotal")), _num(p.get("sizeUsed"))
        out["pools"].append({"name": p.get("name", "?"), "raid": str(p.get("raidType", "")), "capacity": total, "used": used, "free": _num(p.get("sizeFree")),
                             "used_percent": _pct(used, total), "status": _st(good(p), "degraded")})
    for v in g("/lun/instances?fields=name,sizeTotal,health,pool"):
        out["volumes"].append({"name": v.get("name", "?"), "pool": (v.get("pool") or {}).get("id", ""), "capacity": _num(v.get("sizeTotal")),
                               "status": _st(good(v), "degraded"), "mapped": None, "hosts": None})
    for path, label in (("/fan/instances?fields=name,health", "Fan"), ("/powerSupply/instances?fields=name,health", "Power supply"),
                        ("/battery/instances?fields=name,health", "Battery")):
        try:
            for x in g(path):
                out["hardware"].append({"kind": label, "name": x.get("name", label), "status": _st(good(x), "failed")})
        except SanError:
            pass
    return out


def powerstore(a, host, http):
    """Dell PowerStore: REST API (port 443), a user with the Operator role."""
    base = "https://%s:%d/api/rest" % (host, a["port"])
    hdr = basic(a["username"], a["password"])
    g = tolerant(lambda path: http.get(base + path, hdr, a["verify_tls"]) or [], list)
    out = empty(a)
    ap = g("/appliance?select=id,name,model,service_tag")
    first = ap[0] if ap else {}
    try:
        sw = g("/software_installed?select=release_version,is_cluster&is_cluster=eq.true")
    except SanError:
        sw = []
    out.update(name=first.get("name") or a["name"], model=first.get("model", ""), serial=first.get("service_tag", ""),
               firmware=sw[0].get("release_version", "") if sw else "", status="optimal")
    for h in g("/hardware?select=name,type,lifecycle_state,extra_details"):
        life = str(h.get("lifecycle_state", ""))
        good = life.lower() in ("healthy", "empty")
        t = str(h.get("type", ""))
        if t == "Node":
            out["controllers"].append({"name": h.get("name", "Node"), "status": _st(good, life), "detail": ""})
        elif t == "Drive":
            ex = h.get("extra_details") or {}
            out["drives"].append({"slot": h.get("name", "?"), "status": _st(good, "failed" if "fail" in life.lower() else life), "media": str(ex.get("drive_type", "")),
                                  "capacity": _num(ex.get("size")), "hot_spare": False, "predictive_failure": False, "wear_percent": None})
        elif t in ("Fan", "Power_Supply", "Battery"):
            out["hardware"].append({"kind": {"Power_Supply": "Power supply"}.get(t, t), "name": h.get("name", t), "status": _st(good, life)})
    for x in ap:
        try:
            m = http.post(base + "/metrics/generate", hdr, a["verify_tls"], {"entity": "space_metrics_by_appliance", "entity_id": x.get("id"), "interval": "One_Day"})[0] or []
            last = m[-1] if m else {}
            total, used = _num(last.get("physical_total")), _num(last.get("physical_used"))
            if total:
                out["pools"].append({"name": x.get("name", "appliance"), "raid": "DRE", "capacity": total, "used": used, "free": total - used,
                                     "used_percent": _pct(used, total), "status": "optimal"})
            pm = http.post(base + "/metrics/generate", hdr, a["verify_tls"], {"entity": "performance_metrics_by_appliance", "entity_id": x.get("id"), "interval": "Five_Mins"})[0] or []
            if pm:
                q = pm[-1]
                out["perf"] = {"read_iops": _num(q.get("read_iops")), "write_iops": _num(q.get("write_iops")), "read_mbps": _num(q.get("read_bandwidth")) / 1048576.0,
                               "write_mbps": _num(q.get("write_bandwidth")) / 1048576.0, "latency_ms": _num(q.get("avg_latency")) / 1000.0}
        except SanError:
            pass
    for v in g("/volume?select=name,size,state"):
        out["volumes"].append({"name": v.get("name", "?"), "pool": "", "capacity": _num(v.get("size")), "status": _st(str(v.get("state", "Ready")) == "Ready", v.get("state")),
                               "mapped": None, "hosts": None})
    if any(c["status"] != "optimal" for c in out["controllers"]) or any(d["status"] != "optimal" for d in out["drives"]):
        out["status"] = "needs attention"
    return out


def primera(a, host, http):
    """HPE 3PAR / Primera / Alletra 9000: the WSAPI service (port 8080, or 443), a user with the browse role."""
    base = "https://%s:%d/api/v1" % (host, a["port"])
    key = (http.post(base + "/credentials", {}, a["verify_tls"], {"user": a["username"], "password": a["password"]})[0] or {}).get("key")
    if not key:
        raise SanError("the storage refused the user name or password")
    hdr = {"X-HP3PAR-WSAPI-SessionKey": key}
    g = tolerant(lambda path: http.get(base + path, hdr, a["verify_tls"]) or {}, dict)
    state = {1: "optimal", 2: "degraded", 3: "failed"}
    out = empty(a)
    try:
        s0 = g("/system")
        online, nodes = set(s0.get("onlineNodes") or []), s0.get("clusterNodes") or []
        out.update(name=s0.get("name") or a["name"], model=s0.get("model", ""), serial=s0.get("serialNumber", ""), firmware=s0.get("systemVersion", ""),
                   status="optimal" if set(nodes) <= online else "needs attention")
        for n in nodes:
            out["controllers"].append({"name": "Node %s" % n, "status": "optimal" if n in online else "offline", "detail": ""})
        try:
            cap = g("/capacity").get("allCapacity") or {}
            total, free = _num(cap.get("totalMiB")) * 1048576, _num(cap.get("freeMiB")) * 1048576
            if total:
                out["pools"].append({"name": "System capacity", "raid": "", "capacity": total, "used": total - free, "free": free,
                                     "used_percent": _pct(total - free, total), "status": "optimal"})
        except SanError:
            pass
        for c in g("/cpgs").get("members") or []:
            u = c.get("UsrUsage") or {}
            total, used = _num(u.get("totalMiB")) * 1048576, _num(u.get("usedMiB")) * 1048576
            out["pools"].append({"name": c.get("name", "?"), "raid": "CPG", "capacity": total, "used": used, "free": total - used,
                                 "used_percent": _pct(used, total), "status": state.get(c.get("state"), "unknown")})
        for v in g("/volumes").get("members") or []:
            out["volumes"].append({"name": v.get("name", "?"), "pool": v.get("userCPG", ""), "capacity": _num(v.get("sizeMiB")) * 1048576,
                                   "status": state.get(v.get("state"), "unknown"), "mapped": None, "hosts": None})
        try:
            for d in g("/disks").get("members") or []:
                out["drives"].append({"slot": "cage %s pos %s" % ((d.get("position") or {}).get("cage", "?"), (d.get("position") or {}).get("magazine", "?")),
                                      "status": state.get(d.get("state"), "unknown"), "media": str(d.get("type", "")), "capacity": _num(d.get("totalSizeMiB")) * 1048576,
                                      "hot_spare": False, "predictive_failure": False, "wear_percent": None})
        except SanError:
            pass
    finally:
        try:
            http.call("DELETE", base + "/credentials/" + key, hdr, a["verify_tls"])   # end the session: the array allows only a few at once
        except SanError:
            pass
    return out


def pure(a, host, http):
    """Pure Storage FlashArray: REST 2.x (port 443) with the API token of a Read-Only user (the token goes in the password field)."""
    root = "https://%s:%d/api" % (host, a["port"])
    vers = [v for v in ((http.get(root + "/api_version", {}, a["verify_tls"]) or {}).get("version") or []) if str(v).startswith("2.")]
    if not vers:
        raise SanError("this FlashArray does not offer REST 2.x")
    base = "%s/%s" % (root, sorted(vers, key=lambda v: [int(x) for x in v.split(".") if x.isdigit()])[-1])
    _, rh = http.post(base + "/login", {"api-token": a["password"]}, a["verify_tls"], {})
    tok = rh.get("x-auth-token")
    if not tok:
        raise SanError("the storage refused the API token")
    hdr = {"x-auth-token": tok}
    g = tolerant(lambda path: (http.get(base + path, hdr, a["verify_tls"]) or {}).get("items") or [], list)
    out = empty(a)
    ar = (g("/arrays") or [{}])[0]
    out.update(name=ar.get("name") or a["name"], firmware=("%s %s" % (ar.get("os", ""), ar.get("version", ""))).strip(), status="optimal")
    for c in g("/controllers"):
        out["model"] = out["model"] or c.get("model", "")
        out["controllers"].append({"name": c.get("name", "CT"), "status": _st(c.get("status") == "ready", c.get("status")), "detail": c.get("mode", "")})
    for d in g("/drives"):
        st = str(d.get("status", ""))
        out["drives"].append({"slot": d.get("name", "?"), "status": _st(st in ("healthy", "unused", "empty"), st), "media": str(d.get("type", "")).upper(),
                              "capacity": _num(d.get("capacity")), "hot_spare": False, "predictive_failure": False, "wear_percent": None})
    sp = (g("/arrays/space") or [{}])[0]
    total, used = _num(sp.get("capacity")), _num((sp.get("space") or {}).get("total_physical"))
    if total:
        out["pools"].append({"name": "Array", "raid": "RAID-HA", "capacity": total, "used": used, "free": total - used, "used_percent": _pct(used, total), "status": "optimal"})
    for v in g("/volumes?destroyed=false"):
        out["volumes"].append({"name": v.get("name", "?"), "pool": "", "capacity": _num(v.get("provisioned")), "status": "optimal", "mapped": None, "hosts": None})
    for h in g("/hardware"):
        st = str(h.get("status", "ok"))
        out["hardware"].append({"kind": str(h.get("type", "part")).replace("_", " "), "name": h.get("name", "?"),
                                "status": "optimal" if st in ("ok", "healthy", "not_installed") else st})
    try:
        pf = (g("/arrays/performance") or [{}])[0]
        out["perf"] = {"read_iops": _num(pf.get("reads_per_sec")), "write_iops": _num(pf.get("writes_per_sec")), "read_mbps": _num(pf.get("read_bytes_per_sec")) / 1048576.0,
                       "write_mbps": _num(pf.get("write_bytes_per_sec")) / 1048576.0,
                       "latency_ms": max(_num(pf.get("usec_per_read_op")), _num(pf.get("usec_per_write_op"))) / 1000.0}
    except SanError:
        pass
    if any(c["status"] != "optimal" for c in out["controllers"]) or any(d["status"] != "optimal" for d in out["drives"]) or \
            any(h["status"] != "optimal" for h in out["hardware"]):
        out["status"] = "needs attention"
    return out


def svc(a, host, http):
    """IBM FlashSystem / Storwize / SAN Volume Controller (Spectrum Virtualize): REST API (port 7443), a user in the Monitor group."""
    base = "https://%s:%d/rest" % (host, a["port"])
    tok = (http.post(base + "/auth", {"X-Auth-Username": a["username"], "X-Auth-Password": a["password"]}, a["verify_tls"], {})[0] or {}).get("token")
    if not tok:
        raise SanError("the storage refused the user name or password")
    hdr = {"X-Auth-Token": tok}
    g = tolerant(lambda cmd, body=None: http.post(base + "/" + cmd, hdr, a["verify_tls"], body or {})[0], list)
    out = empty(a)
    s0 = g("lssystem") or {}
    out.update(name=s0.get("name") or a["name"], model=s0.get("product_name", ""), serial=s0.get("id", ""), firmware=s0.get("code_level", ""), status="optimal")
    nodes = []
    for cmd in ("lsnodecanister", "lsnode"):
        try:
            nodes = g(cmd) or []
            break
        except SanError:
            continue
    for n in nodes:
        out["controllers"].append({"name": n.get("name", "node"), "status": _st(n.get("status") == "online", n.get("status")), "detail": n.get("panel_name", "")})
    for d in g("lsdrive", {"bytes": True}) or []:
        st = str(d.get("status", ""))
        out["drives"].append({"slot": "enclosure %s slot %s" % (d.get("enclosure_id", "?"), d.get("slot_id", "?")),
                              "status": _st(st == "online", "failed" if st == "offline" else st), "media": str(d.get("tech_type", "")), "capacity": _num(d.get("capacity")),
                              "hot_spare": d.get("use") == "spare", "predictive_failure": False, "wear_percent": None})
    for p in g("lsmdiskgrp", {"bytes": True}) or []:
        total, free = _num(p.get("capacity")), _num(p.get("free_capacity"))
        out["pools"].append({"name": p.get("name", "?"), "raid": "", "capacity": total, "used": total - free, "free": free, "used_percent": _pct(total - free, total),
                             "status": _st(p.get("status") == "online", p.get("status"))})
    for v in g("lsvdisk", {"bytes": True}) or []:
        out["volumes"].append({"name": v.get("name", "?"), "pool": v.get("mdisk_grp_name", ""), "capacity": _num(v.get("capacity")),
                               "status": _st(v.get("status") == "online", v.get("status")), "mapped": None, "hosts": None})
    for cmd, label in (("lsenclosurepsu", "Power supply"), ("lsenclosurebattery", "Battery")):
        try:
            for x in g(cmd) or []:
                out["hardware"].append({"kind": label, "name": "%s %s/%s" % (label, x.get("enclosure_id", "?"), x.get("PSU_id") or x.get("battery_id", "?")),
                                        "status": _st(x.get("status") == "online", x.get("status"))})
        except SanError:
            pass
    try:
        stats = dict((x.get("stat_name"), _num(x.get("stat_current"))) for x in (g("lssystemstats") or []))
        out["perf"] = {"read_iops": stats.get("vdisk_r_io", 0), "write_iops": stats.get("vdisk_w_io", 0), "read_mbps": stats.get("vdisk_r_mb", 0),
                       "write_mbps": stats.get("vdisk_w_mb", 0), "latency_ms": max(stats.get("vdisk_r_ms", 0), stats.get("vdisk_w_ms", 0))}
    except SanError:
        pass
    if any(c["status"] != "optimal" for c in out["controllers"]) or any(d["status"] == "failed" for d in out["drives"]):
        out["status"] = "needs attention"
    return out


def oceanstor(a, host, http):
    """Huawei OceanStor / Dorado: DeviceManager REST API (port 8088), a local user with the read-only (monitor) role."""
    base = "https://%s:%d/deviceManager/rest" % (host, a["port"])
    body, rh = http.post(base + "/xxxxx/sessions", {}, a["verify_tls"], {"username": a["username"], "password": a["password"], "scope": 0})
    body = body or {}
    data = body.get("data") or {}
    if (body.get("error") or {}).get("code", 0) != 0 or not data.get("deviceid"):
        raise SanError("the storage refused the user name or password")
    hdr = {"iBaseToken": data.get("iBaseToken", "")}
    if rh.get("set-cookie"):
        hdr["Cookie"] = rh["set-cookie"].split(";")[0]
    g = tolerant(lambda path: (http.get("%s/%s%s" % (base, data["deviceid"], path), hdr, a["verify_tls"]) or {}).get("data") or [], list)
    good = lambda x: str(x.get("HEALTHSTATUS", "")) == "1"
    sect = lambda v: _num(v) * 512.0
    out = empty(a)
    s0 = g("/system/")
    s0 = s0 if isinstance(s0, dict) else (s0[0] if s0 else {})
    out.update(name=s0.get("NAME") or a["name"], model=str(s0.get("PRODUCTMODE", "")), serial=s0.get("ID", ""), firmware=s0.get("PRODUCTVERSION", ""),
               status=_st(good(s0), "needs attention"))
    for c in g("/controller"):
        out["controllers"].append({"name": "Controller %s" % (c.get("LOCATION") or c.get("NAME", "?")), "status": _st(good(c), "needs attention"), "detail": ""})
    for d in g("/disk"):
        hs = str(d.get("HEALTHSTATUS"))
        out["drives"].append({"slot": d.get("LOCATION", "?"), "status": _st(hs == "1", "failed" if hs == "2" else "degraded"), "media": str(d.get("DISKTYPE", "")),
                              "capacity": sect(d.get("SECTORS")), "hot_spare": str(d.get("LOGICTYPE")) == "3", "predictive_failure": hs == "3", "wear_percent": None})
    for p in g("/storagepool"):
        total, free = sect(p.get("USERTOTALCAPACITY")), sect(p.get("USERFREECAPACITY"))
        out["pools"].append({"name": p.get("NAME", "?"), "raid": "", "capacity": total, "used": total - free, "free": free, "used_percent": _pct(total - free, total),
                             "status": _st(good(p), "degraded")})
    for v in g("/lun"):
        out["volumes"].append({"name": v.get("NAME", "?"), "pool": v.get("PARENTNAME", ""), "capacity": sect(v.get("CAPACITY")), "status": _st(good(v), "degraded"),
                               "mapped": str(v.get("EXPOSEDTOINITIATOR", "")).lower() == "true", "hosts": None})
    for path, label in (("/fan", "Fan"), ("/power", "Power supply"), ("/backup_power", "Battery")):
        try:
            for x in g(path):
                out["hardware"].append({"kind": label, "name": "%s %s" % (label, x.get("LOCATION", "")), "status": _st(good(x), "failed")})
        except SanError:
            pass
    return out
