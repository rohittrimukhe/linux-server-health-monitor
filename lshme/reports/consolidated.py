# Linux Server Health Monitor Enterprise
# Copyright (c) 2026 LRS Services Pvt Ltd, Mumbai. All rights reserved.
"""The estate report: every server (and the SAN storage) in ONE file and ONE e-mail, built on the central server.

1. Estate at a glance: status of every server and storage system, scores, open problems, the busiest and fullest, what needs attention
2. then each server's full daily / weekly / monthly report (its own sections, headed by the server's name), SAN storage included where it
   is monitored. Member servers' figures come from the central collection (fleet) that runs every few minutes."""
import json
import os
import time

from lshme.reports import periodic
from lshme.reports.periodic import Bar, Cell, chip, kpis, note, table

ORDER = {"CRITICAL": 0, "UNREACHABLE": 1, "WARNING": 2, "UNKNOWN": 3, "HEALTHY": 4}


def runs_for_estate(app):
    """This server's latest health check plus every member's latest collected result, and members that could not be reached."""
    from lshme.core.model import RunResult
    from lshme.ops import dashboard
    runs, errors = [], {}
    try:
        with open(os.path.join(app.paths.data_dir, "latest.json"), encoding="utf-8") as f:
            runs.append(RunResult.from_dict(json.load(f)))
    except (OSError, ValueError, KeyError):
        pass
    try:
        members, errors = dashboard.load_results(app.paths)
    except Exception:
        members = []
    here = set(r.host for r in runs)
    runs += [r for r in members if r.host not in here]
    return runs, errors


def _lv(status):
    return {"CRITICAL": "cr", "WARNING": "wn", "HEALTHY": "ok"}.get(status, "na")


def build(app, ptype="weekly", variant="summary", now=None):
    now = now or time.time()
    cfg, paths = app.cfg, app.paths
    runs, errors = runs_for_estate(app)
    if not runs:
        raise ValueError("no health check results yet: run a health check (and the central collection) first")
    docs = []
    for r in runs:
        try:
            docs.append((r, periodic.build(r, cfg, paths, ptype, variant, now)))
        except Exception as e:                                       # one server never stops the estate report
            app.log.exception("estate report: %s failed", r.host)
            docs.append((r, None))
    first = next((d for _, d in docs if d is not None), None)
    d = periodic.PDoc(ptype, variant)
    d.title_plain = "Estate " + d.title.replace("Server ", "").replace(" Report", " report")
    d.title = d.title_plain + " (%d server%s)" % (len(runs) + len(errors), "" if len(runs) + len(errors) == 1 else "s")
    d.generated = now
    if first:
        d.subtitle = "All servers and SAN storage - " + first.subtitle.split(" - ", 2)[-1]
        d.customer, d.company, d.customer_logo, d.tz_offset = first.customer, first.company, first.customer_logo, first.tz_offset
        d.page_size, d.orientation, d.margin_mm = first.page_size, first.orientation, first.margin_mm
        period = dict(first.details).get("Period", "")
    else:
        period = ""
    sans = []
    for r, _ in docs:
        m = r.module("san")
        if m and m.status != "SKIPPED":
            sans += [(r.host, a) for a in (m.data or {}).get("arrays", [])]
    statuses = [(doc.status if doc else "UNKNOWN") for _, doc in docs] + ["UNREACHABLE"] * len(errors)
    ok = sum(1 for s in statuses if s == "HEALTHY")
    sok = sum(1 for _, a in sans if not a.get("error") and a.get("status") == "optimal")
    crit = sum(doc.counts.get("CRITICAL", 0) for _, doc in docs if doc)
    warn = sum(doc.counts.get("WARNING", 0) for _, doc in docs if doc)
    scores = [doc.score for _, doc in docs if doc and doc.score is not None]
    d.status = "CRITICAL" if any(s in ("CRITICAL", "UNREACHABLE") for s in statuses) or any(a.get("error") or a.get("status") not in ("optimal",) for _, a in sans) \
        else ("WARNING" if any(s == "WARNING" for s in statuses) else "HEALTHY")
    d.counts = {"CRITICAL": crit, "WARNING": warn}
    d.score = int(round(sum(scores) / len(scores))) if scores else 0
    d.details = [(k, v) for k, v in (("Customer", d.customer), ("Company", d.company), ("Servers", str(len(runs) + len(errors))),
                                      ("SAN storage", str(len(sans)) if sans else ""), ("Period", period),
                                      ("Generated", time.strftime("%d %b %Y %H:%M", time.localtime(now)))) if v]
    # 1. estate at a glance
    items = [("%d / %d" % (ok, len(statuses)), "Servers healthy", "ok" if ok == len(statuses) else ("cr" if "cr" in [_lv(s) for s in statuses] or errors else "wn"))]
    if sans:
        items.append(("%d / %d" % (sok, len(sans)), "SAN storage optimal", "ok" if sok == len(sans) else "cr"))
    items += [(str(crit + warn), "Open problems", "cr" if crit else "wn" if warn else "ok"), ("%d / 100" % d.score, "Average health score", "ok" if d.score >= 90 else "wn" if d.score >= 70 else "cr")]
    pts = []
    rows = []
    for r, doc in sorted(docs, key=lambda x: (ORDER.get(x[1].status if x[1] else "UNKNOWN", 3), x[0].host)):
        mt = r.metrics()
        disk = mt.get("fs.max_used_percent")
        top = next((f["message"] for f in (doc.findings if doc else []) if f.get("severity") in ("CRITICAL", "WARNING")), "")
        st = doc.status if doc else "UNKNOWN"
        rows.append([Cell(r.meta.get("fleet_name") or r.host, "b"), ", ".join(r.meta.get("roles") or [])[:40] or "-", chip(_lv(st)),
                     doc.score if doc else "-", doc.counts.get("CRITICAL", 0) if doc else "-", doc.counts.get("WARNING", 0) if doc else "-",
                     ("%.0f %%" % mt["cpu.usage_percent"]) if mt.get("cpu.usage_percent") is not None else "-",
                     ("%.0f %%" % mt["memory.used_percent"]) if mt.get("memory.used_percent") is not None else "-",
                     Bar(disk, 80, 90) if disk is not None else Cell("-", "fl"), Cell(top[:90], "wrap") if top else Cell("nothing open", "fl")])
        if doc and st != "HEALTHY":
            pts.append(("cr" if st == "CRITICAL" else "wn", "%s: %s" % (r.meta.get("fleet_name") or r.host, top or st.lower())))
    for name, e in errors.items():
        rows.append([Cell(name, "b"), "-", Cell("UNREACHABLE", "chip-cr"), "-", "-", "-", "-", "-", Cell("-", "fl"), Cell(str(e)[:90], "wrap")])
        pts.append(("cr", "%s could not be reached: %s" % (name, str(e)[:120])))
    for host, a in sans:
        if a.get("error"):
            pts.append(("cr", "SAN storage %s cannot be checked: %s" % (a["name"], a["error"][:120])))
        elif a.get("status") != "optimal":
            pts.append(("cr", "SAN storage %s needs attention (%s)" % (a["name"], a.get("status"))))
    if not pts:
        pts.append(("ok", "Every server%s is healthy." % (" and storage system" if sans else "")))
    blocks = [kpis(items), {"type": "points", "items": pts[:12]},
              table("Servers", ["Server", "Role", "Status", "Score", "Critical", "Warning", "CPU", "Memory", "Fullest disk", "Top issue"], rows,
                    align=["", "", "", "r", "r", "r", "r", "r", "", ""])]
    if sans:
        srows = []
        for host, a in sans:
            pools = [p for p in a.get("pools", []) if p.get("used_percent") is not None]
            full = max(pools, key=lambda p: p["used_percent"]) if pools else None
            failed = sum(1 for x in a.get("drives", []) if x.get("status") in ("failed", "removed", "fault", "broken"))
            srows.append([Cell(a["name"], "b"), a.get("model") or a.get("type", ""), Cell("UNREACHABLE", "chip-cr") if a.get("error") else chip("ok" if a.get("status") == "optimal" else "cr"),
                          ", ".join("%s %s" % (c["name"].replace("Controller ", ""), "OK" if c["status"] == "optimal" else c["status"]) for c in a.get("controllers", [])) or "-",
                          "%d (%d failed)" % (len(a.get("drives", [])), failed) if a.get("drives") else "-",
                          Bar(full["used_percent"], 80, 90) if full else Cell("-", "fl"), ("%.1f ms" % a["perf"]["latency_ms"]) if (a.get("perf") or {}).get("latency_ms") else "-"])
        blocks.append(table("SAN storage", ["Storage", "Model", "Status", "Controllers", "Drives", "Fullest pool", "Response"], srows, align=["", "", "", "", "r", "", "r"]))
    blocks.append(note("The full report of each server follows, in the order above; SAN storage details are in the report of the server that monitors it."))
    d.add("Estate at a glance", blocks)
    d.attention = sum(1 for s in statuses if s != "HEALTHY") + sum(1 for _, a in sans if a.get("error") or a.get("status") != "optimal")
    # 2. every server's own report
    for r, doc in sorted(docs, key=lambda x: (ORDER.get(x[1].status if x[1] else "UNKNOWN", 3), x[0].host)):
        name = r.meta.get("fleet_name") or r.host
        if doc is None:
            d.add("%s" % name, [note("The report of this server could not be built; see the LRS Vitals log on the central server.")])
            continue
        for title, bl in doc.sections:
            d.add("%s - %s" % (name, title), bl)
    return d


def generate(app, ptype="weekly", variant="summary", formats=("html", "pdf"), now=None):
    """Write the estate report files under reports/estate/<type>/<time>. Returns (doc, outdir, files, notes)."""
    from lshme import reports
    doc = build(app, ptype, variant, now)
    outdir = os.path.join(app.paths.report_dir, "estate", ptype, time.strftime("%Y%m%d_%H%M%S", time.localtime(doc.generated)))
    os.makedirs(outdir, exist_ok=True)
    base = "estate_%s_%s" % (ptype, time.strftime("%Y%m%d_%H%M", time.localtime(doc.generated)))
    files, notes = reports.write_periodic([doc], list(formats), outdir, base, app.cfg.get("main", "general", "logo", ""))
    return doc, outdir, files, notes


def send(app, doc, files, to=None, force=False):
    """One e-mail for the whole estate: the glance in the body (same design as the report e-mails), the estate report attached."""
    from lshme.delivery import mailer
    from lshme.reports import mailbody
    ms = mailer.MailSettings(app.cfg)
    problems = ms.validate()
    if problems and not to:
        return {"ok": False, "error": "; ".join(problems)}
    subject = "%s %s - %s%s" % (ms.prefix, doc.title, doc.status, (", %d need attention" % doc.attention) if doc.attention else ", all clear")
    glance = (dict(doc.sections).get("Estate at a glance") or [{}, {}])
    lines = [doc.title, doc.subtitle, ""] + ["- %s" % t for _, t in (glance[1].get("items", []) if len(glance) > 1 else [])]
    text = chr(10).join(lines)
    import socket
    body = mailbody.build(doc, files, mailbody.dashboard_url(app.cfg, socket.getfqdn()))
    msg, rcpts, attached, skipped = mailer.build_message(ms, subject, text, body, files, doc.status, to)
    res = mailer.send(ms, msg, rcpts, app.paths.log_dir)
    res["attached"], res["skipped_attachments"] = attached, skipped
    return res
