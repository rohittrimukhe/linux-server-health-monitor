# Linux Server Health Monitor Enterprise
# Copyright (c) 2026 LRS Services Pvt Ltd, Mumbai. All rights reserved.
"""Application endpoints (HTTP/HTTPS checks) and mail queue health."""
import re
import ssl
import time

try:
    from urllib.request import Request, urlopen
    from urllib.error import HTTPError
except ImportError:  # pragma: no cover
    Request = urlopen = HTTPError = None

from lshme.core.util import severity_for, to_int

NAME = "applications"
TITLE = "Applications & Mail"
WEIGHT = 6


def http_check(url, expected, contains="", timeout=15):
    start = time.time()
    try:
        ctx = ssl.create_default_context()
        resp = urlopen(Request(url, headers={"User-Agent": "lshme-healthcheck"}), timeout=timeout, context=ctx)
        code, body = resp.getcode(), resp.read(65536).decode("utf-8", "replace")
    except HTTPError as e:
        code, body = e.code, ""
    except Exception as e:
        return {"ok": False, "code": None, "ms": None, "error": str(e)[:300]}
    ms = round((time.time() - start) * 1000)
    ok = code == expected and (not contains or contains in body)
    return {"ok": ok, "code": code, "ms": ms, "error": "" if ok else ("expected %s%s" % (expected, " containing '%s'" % contains if contains else ""))}


def run(ctx, res):
    rows = []
    for spec in ctx.cfg.getlist("main", "applications", "http_checks", ""):
        # url|expected_status|text_that_must_appear
        p = spec.split("|")
        url, expected, contains = p[0].strip(), to_int(p[1], 200) if len(p) > 1 and p[1].strip() else 200, p[2] if len(p) > 2 else ""
        r = http_check(url, expected, contains)
        rows.append([url, r["code"] or "-", "%s ms" % r["ms"] if r["ms"] is not None else "-", "OK" if r["ok"] else "FAILED: " + r["error"]])
        if not r["ok"]:
            res.add("CRITICAL", "HTTP_CHECK_FAILED", url, "Application endpoint %s failed (%s)" % (url, r["code"] or r["error"]),
                    recommendation="Check the web/application server and its upstream dependencies.")
        elif r["ms"] and r["ms"] > 5000:
            res.add("WARNING", "HTTP_SLOW", url, "Endpoint %s responded in %d ms" % (url, r["ms"]))
    if rows:
        res.table("HTTP checks", ["URL", "Status", "Response time", "Result"], rows, kind="summary")
    queue = None
    if ctx.cfg.getbool("main", "applications", "check_mail_queue", True) and ctx.has("postqueue"):
        r = ctx.run(["postqueue", "-p"], timeout=60)
        m = re.search(r"in (\d+) Requests?", r.out)
        queue = 0 if "Mail queue is empty" in r.out else (to_int(m.group(1), 0) if m else None)
        if queue is not None:
            res.metric("mail.queue", queue)
            res.table("Mail queue", ["MTA", "Queued messages"], [["postfix", queue]], kind="summary")
            sev = severity_for(queue, ctx.t("mail", "queue_warning", 100), ctx.t("mail", "queue_critical", 1000))
            if sev:
                res.add(sev, "MAIL_QUEUE", "postfix", "%d messages queued in postfix" % queue,
                        recommendation="Check relayhost connectivity and deferred reasons with 'postqueue -p'.")
    if not rows and queue is None:
        res.status = "SKIPPED"
        res.summary = "no application checks configured"
        res.skip("add http_checks in main.conf [applications]")
        return
    res.data = {"http": rows, "mail_queue": queue}
    res.summary = "%d HTTP check(s), mail queue %s" % (len(rows), queue if queue is not None else "n/a")
