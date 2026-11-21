# Linux Server Health Monitor Enterprise
# Copyright (c) 2026 LRS Services Pvt Ltd, Mumbai. All rights reserved.
"""SAN storage arrays added by the administrator (main.conf [san:NAME]): array health, controllers, drives, pools, volumes,
fans / power / batteries, recent critical events and performance. Read-only, over each make's management API (lshme.ops.san)."""
from lshme.core.util import fmt_bytes, severity_for

NAME = "san"
TITLE = "SAN Storage"
WEIGHT = 10


def run(ctx, res):
    from lshme.ops import san
    arrays = [a for a in san.arrays(ctx.cfg) if a["enabled"]]
    if not arrays:
        res.status = "SKIPPED"
        res.skip("no SAN storage added (vitals san add, or Settings > SAN storage in the web dashboard)")
        res.summary = "no SAN storage added"
        return
    pw, pc = ctx.t("san", "pool_warning", 80), ctx.t("san", "pool_critical", 90)
    lat_w = ctx.t("san", "latency_warning_ms", 20)
    wear_w = ctx.t("san", "ssd_wear_warning", 90)
    done = []
    for a in arrays:
        n = a["name"]
        try:
            d = san.collect(a)
        except san.SanError as e:
            res.add("CRITICAL", "SAN_UNREACHABLE", n, "SAN storage %s cannot be checked: %s" % (n, e),
                    recommendation="Check the controllers' management network, the read-only user and its password (vitals san test %s)." % n)
            res.metric("san.reachable:" + n, 0)
            done.append({"name": n, "type": a["type"], "error": str(e)})
            continue
        res.metric("san.reachable:" + n, 1)
        if d["status"] not in ("optimal", "unknown"):
            res.add("CRITICAL", "SAN_NEEDS_ATTENTION", n, "SAN storage %s needs attention (status: %s)" % (n, d["status"]),
                    recommendation="Open the storage's management console; its recovery guidance names the failed part.")
        for f in d["failures"][:10]:
            res.add("CRITICAL", "SAN_FAILURE", n, "SAN storage %s reports a failure: %s" % (n, f))
        for u in d["unreachable"]:
            res.add("WARNING", "SAN_CONTROLLER_UNREACHABLE", n, "SAN storage %s: management port of one controller does not answer: %s" % (n, u),
                    recommendation="The other controller answered; check that controller's management cable and network.")
        for c in d["controllers"]:
            if c["status"] not in ("optimal", "unreachable"):
                res.add("CRITICAL", "SAN_CONTROLLER", "%s/%s" % (n, c["name"]), "SAN storage %s: %s is %s" % (n, c["name"], c["status"]))
        failed = [x for x in d["drives"] if x["status"] in ("failed", "removed", "fault", "broken")]
        for x in failed:
            res.add("CRITICAL", "SAN_DRIVE_FAILED", "%s/%s" % (n, x["slot"]), "SAN storage %s: drive at %s has failed" % (n, x["slot"]),
                    recommendation="Replace the drive; until then the pool runs without its full protection.")
        for x in d["drives"]:
            if x not in failed and x["status"] not in ("optimal", "spare", "present") and x["status"]:
                res.add("WARNING", "SAN_DRIVE", "%s/%s" % (n, x["slot"]), "SAN storage %s: drive at %s is %s" % (n, x["slot"], x["status"]))
            if x.get("predictive_failure"):
                res.add("WARNING", "SAN_DRIVE_PFA", "%s/%s" % (n, x["slot"]), "SAN storage %s: drive at %s predicts it will fail soon" % (n, x["slot"]),
                        recommendation="Plan a replacement before it fails.")
            if x.get("wear_percent") is not None and x["wear_percent"] >= wear_w:
                res.add("WARNING", "SAN_SSD_WEAR", "%s/%s" % (n, x["slot"]), "SAN storage %s: SSD at %s has used %.0f%% of its life" % (n, x["slot"], x["wear_percent"]))
        res.metric("san.drives_failed:" + n, len(failed))
        for p in d["pools"]:
            key = "%s|%s" % (n, p["name"])
            if p["used_percent"] is not None:
                res.metric("san.pool_used_percent:" + key, p["used_percent"])
                res.metric("san.pool_used_bytes:" + key, p["used"])
                sev = severity_for(p["used_percent"], pw, pc)
                if sev:
                    res.add(sev, "SAN_POOL_FULL", "%s/%s" % (n, p["name"]), "SAN storage %s: pool %s is %.1f%% used (%s free)" % (n, p["name"], p["used_percent"], fmt_bytes(p["free"])),
                            recommendation="Add drives or free space; thin volumes stop when their pool is full.")
            if p["status"] not in ("optimal", "online", "unknown", ""):
                res.add("CRITICAL", "SAN_POOL_DEGRADED", "%s/%s" % (n, p["name"]), "SAN storage %s: pool %s is %s" % (n, p["name"], p["status"]),
                        recommendation="A degraded pool has lost protection; replace the failed drive now.")
        for v in d["volumes"]:
            if v["status"] not in ("optimal", "online", "unknown", ""):
                res.add("WARNING", "SAN_VOLUME", "%s/%s" % (n, v["name"]), "SAN storage %s: volume %s is %s" % (n, v["name"], v["status"]))
        for h in d["hardware"]:
            if h["status"] not in ("optimal", "ok", "unknown", ""):
                res.add("CRITICAL" if h["kind"] in ("Power supply", "Battery") else "WARNING", "SAN_HARDWARE", "%s/%s" % (n, h["name"]),
                        "SAN storage %s: %s is %s" % (n, h["name"], h["status"]))
        perf = d.get("perf") or {}
        for k in ("read_iops", "write_iops", "read_mbps", "write_mbps", "latency_ms"):
            if perf.get(k) is not None:
                res.metric("san.%s:%s" % (k, n), perf[k])
        if perf.get("latency_ms") and perf["latency_ms"] >= lat_w:
            res.add("WARNING", "SAN_LATENCY", n, "SAN storage %s: response time %.1f ms" % (n, perf["latency_ms"]),
                    recommendation="Slow storage makes every VM and database on it slow; look for busy volumes or a degraded pool.")
        done.append(d)
    res.data = {"arrays": done}
    ok = [d for d in done if not d.get("error")]
    res.table("SAN storage", ["Name", "Type", "Model", "Firmware", "Status", "Controllers", "Drives (failed)", "Pools"],
              [[d["name"], d["type"], d.get("model", ""), d.get("firmware", ""), d.get("error") and "unreachable" or d["status"],
                ", ".join("%s %s" % (c["name"], c["status"]) for c in d.get("controllers", [])),
                "%d (%d)" % (len(d.get("drives", [])), sum(1 for x in d.get("drives", []) if x["status"] in ("failed", "removed", "fault", "broken"))),
                len(d.get("pools", []))] for d in done], kind="summary")
    res.table("SAN pools", ["Storage", "Pool", "RAID", "Capacity", "Used", "Free", "Used%", "Status"],
              [[d["name"], p["name"], p["raid"], fmt_bytes(p["capacity"]), fmt_bytes(p["used"]), fmt_bytes(p["free"]),
                p["used_percent"] if p["used_percent"] is not None else "", p["status"]] for d in ok for p in d["pools"]])
    res.summary = "%d SAN storage system(s), %d answering" % (len(done), len(ok))
