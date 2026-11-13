# Linux Server Health Monitor Enterprise
# Copyright (c) 2026 LRS Services Pvt Ltd, Mumbai. All rights reserved.
"""High availability: Pacemaker/Corosync cluster status and quorum."""
import re

NAME = "cluster"
TITLE = "High Availability Cluster"
WEIGHT = 8


def run(ctx, res):
    if not (ctx.has("crm_mon") or ctx.has("pcs")):
        res.status = "SKIPPED"
        res.summary = "no Pacemaker cluster software"
        res.skip("pacemaker not installed")
        return
    r = ctx.run(["crm_mon", "-1", "-r", "-f"], timeout=60) if ctx.has("crm_mon") else ctx.run(["pcs", "status"], timeout=60)
    if not r.ok:
        if "not running" in (r.err + r.out).lower() or "connection to cluster failed" in (r.err + r.out).lower():
            res.status = "SKIPPED"
            res.summary = "Pacemaker installed but not running"
            res.add("INFO", "CLUSTER_NOT_RUNNING", "pacemaker", "Pacemaker is installed but not running", evidence=r.brief_error())
            return
        res.add("UNKNOWN", "CLUSTER_STATUS_FAILED", "pacemaker", "Cluster status unavailable", evidence=r.brief_error())
        return
    text = r.out
    offline = re.findall(r"OFFLINE:\s*\[\s*([^\]]+)\]", text)
    online = re.findall(r"Online:\s*\[\s*([^\]]+)\]", text)
    stopped = [l.strip() for l in text.splitlines() if re.search(r"\b(Stopped|FAILED|Failed)\b", l) and "disabled" not in l]
    failed_actions = []
    if "Failed Resource Actions" in text or "Failed Actions" in text:
        part = text.split("Failed Resource Actions" if "Failed Resource Actions" in text else "Failed Actions", 1)[1]
        failed_actions = [l.strip() for l in part.splitlines()[1:] if l.strip().startswith(("*", "+"))][:20]
    quorum = None
    if ctx.has("corosync-quorumtool"):
        q = ctx.run(["corosync-quorumtool", "-s"], timeout=30)
        m = re.search(r"Quorate:\s*(\w+)", q.out)
        quorum = m.group(1) if m else None
    no_quorum = "partition WITHOUT quorum" in text or quorum == "No"
    maint = "maintenance-mode" in text.lower() and "enabled" in text.lower()
    if no_quorum:
        res.add("CRITICAL", "CLUSTER_NO_QUORUM", "corosync", "Cluster partition has no quorum",
                recommendation="Restore network connectivity between nodes; resources may be stopped.")
    for o in offline:
        res.add("CRITICAL", "CLUSTER_NODE_OFFLINE", o.strip(), "Cluster node(s) offline: %s" % o.strip())
    for s in stopped[:20]:
        res.add("WARNING", "CLUSTER_RESOURCE_STOPPED", s.split()[0] if s.split() else "resource", "Resource not running: %s" % s)
    if failed_actions:
        res.add("WARNING", "CLUSTER_FAILED_ACTIONS", "pacemaker", "%d failed resource action(s)" % len(failed_actions),
                evidence="\n".join(failed_actions), recommendation="Investigate, then 'pcs resource cleanup' once fixed.")
    if maint:
        res.add("INFO", "CLUSTER_MAINTENANCE", "pacemaker", "Cluster maintenance mode is enabled")
    res.table("Cluster status", ["Item", "Value"], [
        ["Online nodes", " ".join(online) or "-"], ["Offline nodes", " ".join(offline) or "none"],
        ["Quorate", quorum or ("no" if no_quorum else "yes")], ["Stopped/failed resources", len(stopped)],
        ["Failed actions", len(failed_actions)]], kind="summary")
    res.data = {"status": text[:20000], "quorum": quorum}
    res.summary = "online: %s; offline: %s" % (" ".join(online) or "-", " ".join(offline) or "none")
