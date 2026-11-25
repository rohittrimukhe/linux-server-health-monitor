# Linux Server Health Monitor Enterprise
# Copyright (c) 2026 LRS Services Pvt Ltd, Mumbai. All rights reserved.
"""The dependency map of the estate and what a failure takes down with it.

Built on the central server from what the health checks already collect, nothing new is asked of the servers:
  hardware (BMC)  -> the server it runs (main.conf [bmc:NAME] server)
  SAN storage     -> pool -> volume -> the host's multipath disk (volume WWN = disk WWID without its leading 3)
                  -> the filesystem mounted on it, or a VM disk on it
  KVM host        -> its VMs -> the server inside each VM (same MAC address, else the same name) -> its databases
A failed or unreachable node puts everything below it at risk; the first failed node on a path is reported as the cause."""
import json
import os
import re

OK, WARN, CRIT, DOWN, OFF, UNKNOWN = "ok", "warn", "crit", "down", "off", "unknown"
BAD = (CRIT, DOWN)
RANK = {OK: 0, OFF: 0, UNKNOWN: 1, WARN: 2, CRIT: 3, DOWN: 4}


def _norm(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


class Graph(object):
    def __init__(self):
        self.nodes, self.children, self.parents = {}, {}, {}

    def node(self, nid, kind, name, status=UNKNOWN, detail="", href="", **extra):
        n = self.nodes.get(nid)
        if n is None:
            n = dict(id=nid, kind=kind, name=name, status=status, detail=detail, href=href, **extra)
            self.nodes[nid] = n
        else:
            if RANK.get(status, 1) > RANK.get(n["status"], 1):
                n["status"] = status
            if detail and not n["detail"]:
                n["detail"] = detail
            n.update(extra)
        return n

    def edge(self, parent, child):
        if parent == child or parent not in self.nodes or child not in self.nodes:
            return
        self.children.setdefault(parent, [])
        if child not in self.children[parent]:
            self.children[parent].append(child)
        self.parents.setdefault(child, [])
        if parent not in self.parents[child]:
            self.parents[child].append(parent)

    def below(self, nid, seen=None):
        seen = seen if seen is not None else set()
        for c in self.children.get(nid, []):
            if c not in seen:
                seen.add(c)
                self.below(c, seen)
        return seen


def _health(status):
    return {"HEALTHY": OK, "WARNING": WARN, "CRITICAL": CRIT, "UNREACHABLE": DOWN}.get((status or "").upper(), UNKNOWN)


def _runs(app):
    """(local run or None, [member runs], {member name: error})"""
    from lshme.core.model import RunResult
    from lshme.ops import dashboard
    local = None
    try:
        with open(os.path.join(app.paths.data_dir, "latest.json"), encoding="utf-8") as f:
            local = RunResult.from_dict(json.load(f))
    except (OSError, ValueError, KeyError):
        local = None
    try:
        runs, errors = dashboard.load_results(app.paths)
    except Exception:
        runs, errors = [], {}
    if local:
        runs = [r for r in runs if r.host != local.host]
    return local, runs, dict(errors or {})


def build(app):
    from lshme.ops import fleet
    g = Graph()
    local, members, errors = _runs(app)
    servers = {}                                                     # fleet name / host name -> server node id
    macs = {}                                                        # MAC -> server node id

    def add_server(run, name, is_local):
        sid = "srv:" + name
        counts = run.health.get("counts", {}) if run else {}
        g.node(sid, "server", name, _health(run.health.get("status")) if run else UNKNOWN,
               "%s critical, %s warning" % (counts.get("CRITICAL", 0), counts.get("WARNING", 0)) if run else "",
               "#/server/" + ("_local" if is_local else name), host=run.host if run else name)
        for key in {name, run.host if run else "", (run.host or "").split(".")[0] if run else ""}:
            if key:
                servers[key.lower()] = sid
        if run:
            net = run.module("network")
            for i in ((net.data if net else {}) or {}).get("interfaces", []):
                m = (i.get("mac") or "").lower()
                if m and m != "00:00:00:00:00:00":
                    macs[m] = sid
        return sid

    runs = []
    if local:
        runs.append((local, add_server(local, local.host, True)))
    for r in members:
        runs.append((r, add_server(r, r.meta.get("fleet_name") or r.host, False)))
    for name, err in errors.items():
        sid = "srv:" + name
        g.node(sid, "server", name, DOWN, "cannot be reached: %s" % str(err)[:120], "#/server/" + name, host=name)
        servers[name.lower()] = sid
    for sv in fleet.servers(app.cfg):                                # members that never answered yet
        if sv.name.lower() not in servers and sv.host.lower() not in servers:
            g.node("srv:" + sv.name, "server", sv.name, UNKNOWN, "no health check yet", "#/server/" + sv.name, host=sv.host)
            servers[sv.name.lower()] = "srv:" + sv.name

    # SAN storage -> pools -> volumes (from the central server's check)
    vol_by_wwn = {}
    sanm = local.module("san") if local else None
    for a in ((sanm.data if sanm else {}) or {}).get("arrays", []):
        aid = "san:" + a["name"]
        if a.get("error"):
            g.node(aid, "san", a["name"], DOWN, a["error"][:140], "#/san/" + a["name"])
            continue
        failed = [x for x in a.get("drives", []) if x.get("status") in ("failed", "removed", "fault", "broken")]
        st = OK if a.get("status") == "optimal" and not failed else CRIT if failed or a.get("status") not in ("optimal", "unknown") else UNKNOWN
        g.node(aid, "san", a["name"], st, ("%d failed drive(s)" % len(failed)) if failed else (a.get("model") or ""), "#/san/" + a["name"])
        for p in a.get("pools", []):
            pid = "pool:%s:%s" % (a["name"], p["name"])
            pst = OK if p.get("status") in ("optimal", "online", "") else CRIT
            if pst == OK and (p.get("used_percent") or 0) >= 90:
                pst = WARN
            g.node(pid, "pool", p["name"], pst, "%.0f%% used" % p["used_percent"] if p.get("used_percent") is not None else "", "#/san/" + a["name"])
            g.edge(aid, pid)
        for v in a.get("volumes", []):
            vid = "vol:%s:%s" % (a["name"], v["name"])
            g.node(vid, "volume", v["name"], OK if v.get("status") in ("optimal", "online", "") else CRIT, "", "#/san/" + a["name"], wwn=v.get("wwn", ""))
            pid = "pool:%s:%s" % (a["name"], v.get("pool", ""))
            g.edge(pid if pid in g.nodes else aid, vid)
            if v.get("wwn"):
                vol_by_wwn[v["wwn"].lower()] = vid

    # hardware (BMC) -> server
    bm = local.module("bmc") if local else None
    for d in ((bm.data if bm else {}) or {}).get("bmcs", []):
        bid = "bmc:" + d["name"]
        st = DOWN if d.get("error") else CRIT if d.get("power") not in ("on", "unknown", None) or d.get("status") == "critical" else WARN if d.get("status") == "warning" else OK
        g.node(bid, "hardware", d["name"], st, d.get("error") or ("%s %s, power %s" % (d.get("make", ""), d.get("model", ""), d.get("power", ""))).strip(), "")
        link = (d.get("server") or "").strip().lower()
        target = servers.get(local.host.lower()) if (link in ("_local", "local") and local) else servers.get(link)
        if target:
            g.edge(bid, target)

    # each server: SAN disks, mounts, VMs, databases
    for run, sid in runs:
        host = g.nodes[sid]["name"]
        st = run.module("storage")
        sd = (st.data if st else {}) or {}
        luns = {}
        for m in (sd.get("multipath") or {}).get("maps", []):
            lid = "lun:%s:%s" % (host, m["name"])
            lst = CRIT if m.get("paths") and not m.get("active") else WARN if m.get("failed") else OK
            g.node(lid, "disk", m["name"], lst, "%d of %d paths working" % (m.get("active", 0), m.get("paths", 0)), g.nodes[sid]["href"])
            luns[m["name"]] = lid
            wwid = (m.get("wwid") or "").lower()
            vid = vol_by_wwn.get(wwid[1:]) or vol_by_wwn.get(wwid)
            g.edge(vid if vid else sid, lid)
        mounts = {}
        for f in sd.get("filesystems", []):
            dev = f.get("device", "")
            mm = re.search(r"/dev/mapper/(mpath[a-z0-9]+?)(?:p?\d+)?$", dev) or re.search(r"/dev/mapper/([^/]+)$", dev)
            if mm and mm.group(1) in luns:
                mid = "mnt:%s:%s" % (host, f["mountpoint"])
                pct = f.get("used_percent") or 0
                g.node(mid, "mount", f["mountpoint"], CRIT if pct >= 95 else WARN if pct >= 85 else OK, "%.0f%% used" % pct, g.nodes[sid]["href"])
                g.edge(luns[mm.group(1)], mid)
                mounts[f["mountpoint"]] = mid
        kv = run.module("kvm")
        for vm in ((kv.data if kv and kv.status not in ("SKIPPED", "ERROR") else {}) or {}).get("vms", []):
            vid = "vm:%s:%s" % (host, vm["name"])
            state = vm.get("state", "")
            vst = OK if state == "running" else CRIT if state == "crashed" else WARN if state in ("paused", "blocked", "pmsuspended") or vm.get("autostart") else OFF
            g.node(vid, "vm", vm["name"], vst, state, g.nodes[sid]["href"])
            g.edge(sid, vid)
            for dk in vm.get("disks", []):
                path = dk.get("path", "")
                mm = re.search(r"/dev/mapper/([^/]+)$", path)
                if mm and mm.group(1) in luns:
                    g.edge(luns[mm.group(1)], vid)
                for mp, mid in mounts.items():
                    if path.startswith(mp.rstrip("/") + "/"):
                        g.edge(mid, vid)
            guest = None
            for m in vm.get("macs", []):
                guest = guest or macs.get(m.lower())
            if not guest:
                n = _norm(vm["name"])
                for key, s2 in servers.items():
                    if n and (_norm(key) == n or _norm(g.nodes[s2]["name"]) == n):
                        guest = s2
            if guest and guest != sid:
                g.edge(vid, guest)
        dbm = run.module("database")
        crit_db = set(f.component for f in run.findings("CRITICAL") if f.module == "database")
        warn_db = set(f.component for f in run.findings("WARNING") if f.module == "database")
        for inst in ((dbm.data if dbm and dbm.status not in ("SKIPPED",) else {}) or {}).get("instances", []):
            name = inst.get("instance") or "?"
            comp = "%s:%s" % (inst.get("engine", ""), name)
            dst = DOWN if inst.get("error") else CRIT if any(name in c for c in crit_db) else WARN if any(name in c for c in warn_db) else OK
            did = "db:%s:%s" % (host, comp)
            g.node(did, "database", "%s %s" % (inst.get("engine", "").title(), name), dst, inst.get("role") or inst.get("open_mode") or "", g.nodes[sid]["href"])
            g.edge(sid, did)
    return g


def impacts(g):
    """Every failed node that is the first failure on its path, with everything below it that depends on it."""
    out = []
    for nid, n in g.nodes.items():
        if n["status"] not in BAD:
            continue
        if any(g.nodes[p]["status"] in BAD for p in g.parents.get(nid, [])):
            continue                                             # its own parent already failed: that one is the cause
        hit = [g.nodes[c] for c in g.below(nid) if g.nodes[c]["status"] != OFF]
        if hit:
            out.append({"cause": n, "affected": sorted(hit, key=lambda x: ("hardware server vm database san pool volume disk mount".split().index(x["kind"])
                                                                            if x["kind"] in "hardware server vm database san pool volume disk mount".split() else 9, x["name"]))})
    order = {"hardware": 0, "san": 1, "pool": 2, "volume": 3, "disk": 4, "mount": 5, "server": 6, "vm": 7, "database": 8}
    return sorted(out, key=lambda i: (order.get(i["cause"]["kind"], 9), -len(i["affected"])))


def roots(g):
    return [nid for nid in g.nodes if not g.parents.get(nid)]


def tree(g, nid, depth=0, seen=None):
    seen = seen if seen is not None else set()
    n = dict(g.nodes[nid])
    if nid in seen or depth > 12:
        n["children"] = []
        n["repeat"] = True
        return n
    seen.add(nid)
    order = {"hardware": 0, "server": 1, "san": 2, "pool": 3, "volume": 4, "disk": 5, "mount": 6, "vm": 7, "database": 8}
    n["children"] = [tree(g, c, depth + 1, seen) for c in sorted(g.children.get(nid, []), key=lambda c: (order.get(g.nodes[c]["kind"], 9), g.nodes[c]["name"]))]
    return n


def snapshot(app):
    """What the dashboard and the reports show: the trees from each root and the current impacts."""
    g = build(app)
    order = {"hardware": 0, "san": 1, "server": 2}
    rs = sorted(roots(g), key=lambda r: (order.get(g.nodes[r]["kind"], 9), g.nodes[r]["name"]))
    seen = set()
    trees = [tree(g, r, 0, seen) for r in rs]
    imp = [{"cause": dict(i["cause"]), "affected": [dict(a) for a in i["affected"]]} for i in impacts(g)]
    return {"trees": trees, "impacts": imp, "counts": {k: sum(1 for n in g.nodes.values() if n["kind"] == k) for k in
                                                         ("hardware", "server", "vm", "database", "san", "pool", "volume", "disk", "mount")}}
