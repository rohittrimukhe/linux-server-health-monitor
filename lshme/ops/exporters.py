# Linux Server Health Monitor Enterprise
# Copyright (c) 2026 LRS Services Pvt Ltd, Mumbai. All rights reserved.
"""Integration outputs: Nagios/Icinga plugin, Prometheus textfile (Grafana)."""
import os
import re

EXIT = {"HEALTHY": 0, "WARNING": 1, "CRITICAL": 2, "UNKNOWN": 3}


def nagios(run):
    h = run.health
    c = h.get("counts", {})
    status = h.get("status", "UNKNOWN")
    label = {"HEALTHY": "OK"}.get(status, status)
    top = run.findings("WARNING")[:3]
    msg = "; ".join("%s: %s" % (f.component, f.message[:80]) for f in top)
    text = "LSHME %s - score %s/100%s | score=%s;89;69;0;100 critical=%d warning=%d unknown=%d" % (
        label, h.get("score"), (" - " + msg) if msg else "", h.get("score"), c.get("CRITICAL", 0),
        c.get("WARNING", 0), c.get("UNKNOWN", 0))
    return EXIT.get(status, 3), text


def _label(v):
    return str(v).replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")


def prometheus(run):
    host = _label(run.host)
    L = ["# HELP lshme_health_score Overall health score (0-100)", "# TYPE lshme_health_score gauge",
         'lshme_health_score{host="%s"} %s' % (host, run.health.get("score", 0)),
         "# HELP lshme_health_status 0=healthy 1=warning 2=critical", "# TYPE lshme_health_status gauge",
         'lshme_health_status{host="%s"} %d' % (host, {"HEALTHY": 0, "WARNING": 1, "CRITICAL": 2}.get(run.health.get("status"), 3)),
         "# HELP lshme_findings Findings by severity", "# TYPE lshme_findings gauge"]
    for sev, n in sorted(run.health.get("counts", {}).items()):
        L.append('lshme_findings{host="%s",severity="%s"} %d' % (host, sev.lower(), n))
    L += ["# HELP lshme_module_score Module health score", "# TYPE lshme_module_score gauge"]
    for m in run.health.get("modules", []):
        if m["score"] is not None:
            L.append('lshme_module_score{host="%s",module="%s"} %s' % (host, m["module"], m["score"]))
    L += ["# HELP lshme_metric Raw metric collected by LSHME", "# TYPE lshme_metric gauge"]
    for k, v in sorted(run.metrics().items()):
        name, _, inst = k.partition(":")
        L.append('lshme_metric{host="%s",metric="%s",instance="%s"} %s' % (host, _label(re.sub(r"[^A-Za-z0-9_.]", "_", name)), _label(inst), v))
    L.append('lshme_last_run_timestamp_seconds{host="%s"} %d' % (host, int(run.collected_at)))
    return "\n".join(L) + "\n"


def write_prometheus(run, path):
    tmp = path + ".tmp"
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(prometheus(run))
    os.replace(tmp, path)  # atomic for node_exporter's textfile collector
    return path
