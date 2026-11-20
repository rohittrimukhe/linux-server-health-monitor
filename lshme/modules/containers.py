# Linux Server Health Monitor Enterprise
# Copyright (c) 2026 LRS Services Pvt Ltd, Mumbai. All rights reserved.
"""Docker / Podman container health."""
NAME = "containers"
TITLE = "Containers"
WEIGHT = 4


def run(ctx, res):
    engines = [e for e in ("docker", "podman") if ctx.has(e)]
    if not engines:
        res.status = "SKIPPED"
        res.summary = "no container engine installed"
        res.skip("docker/podman not installed")
        return
    rows, data = [], {}
    for e in engines:
        r = ctx.run([e, "ps", "-a", "--format", "{{.Names}}|{{.Image}}|{{.Status}}|{{.State}}"], timeout=60)
        if not r.ok:
            data[e] = {"error": r.brief_error()}
            if "Cannot connect" in r.err or "permission denied" in r.err.lower():
                res.add("WARNING" if e == "docker" else "INFO", "CONTAINER_ENGINE_DOWN", e, "%s daemon is not reachable" % e, evidence=r.brief_error())
            continue
        items = []
        for line in r.lines():
            p = (line.split("|") + ["", "", "", ""])[:4]
            items.append(p)
            rows.append([e] + p)
            status, state = p[2].lower(), p[3].lower()
            if "unhealthy" in status:
                res.add("WARNING", "CONTAINER_UNHEALTHY", p[0], "Container %s is unhealthy" % p[0], evidence=p[2])
            elif "restarting" in status or state == "restarting":
                res.add("WARNING", "CONTAINER_RESTARTING", p[0], "Container %s is restarting" % p[0], evidence=p[2])
            elif (state == "exited" or status.startswith("exited")) and "exited (0)" not in status:
                res.add("WARNING", "CONTAINER_EXITED", p[0], "Container %s exited abnormally" % p[0], evidence=p[2])
        data[e] = {"containers": items}
        res.metric("containers.%s.running" % e, sum(1 for i in items if i[3].lower() == "running" or i[2].startswith("Up")))
    res.table("Containers", ["Engine", "Name", "Image", "Status", "State"], rows, kind="summary")
    res.data = data
    res.summary = "%d container(s) on %s" % (len(rows), ", ".join(engines))
