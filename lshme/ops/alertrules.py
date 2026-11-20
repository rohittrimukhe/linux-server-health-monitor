# Linux Server Health Monitor Enterprise
# Copyright (c) 2026 Rohit Santosh Trimukhe. All rights reserved.
"""Alert rules and channels for the real-time watcher: quiet hours, minimum duration, routing to different
people by area (database, storage ...), acknowledgement, a daily digest, and Slack / Microsoft Teams messages.

These are small pure functions so they can be tested without a running watcher."""
import json
import re
import time

from lshme.core.model import SEV_RANK

HOOK_HOSTS = (("hooks.slack.com", "slack"), ("webhook.office.com", "teams"), ("outlook.office.com", "teams"),
              ("logic.azure.com", "teams"), ("powerplatform.com", "teams"), ("office.com/webhook", "teams"))


# -- quiet hours --------------------------------------------------------------------------------
def parse_quiet(spec):
    """'22:00-06:00' -> (1320, 360) in minutes after midnight, or None."""
    m = re.match(r"^\s*(\d{1,2}):(\d{2})\s*-\s*(\d{1,2}):(\d{2})\s*$", spec or "")
    if not m:
        return None
    a, b = int(m.group(1)) * 60 + int(m.group(2)), int(m.group(3)) * 60 + int(m.group(4))
    return None if a > 1439 or b > 1439 or a == b else (a, b)


def in_quiet(spec, now):
    q = parse_quiet(spec)
    if not q:
        return False
    t = time.localtime(now)
    minute = t.tm_hour * 60 + t.tm_min
    a, b = q
    return a <= minute < b if a < b else (minute >= a or minute < b)


# -- which alerts may go out now ----------------------------------------------------------------
def apply_rules(alerts, now, quiet_spec, quiet_min_sev, min_minutes, min_minutes_critical):
    """Split alerts into (send, held). Held alerts are alerted again on a later check, once the rule allows it."""
    send, held = [], []
    quiet = in_quiet(quiet_spec, now)
    floor = SEV_RANK.get((quiet_min_sev or "CRITICAL").upper(), 3)
    for a in alerts:
        crit = a["severity"] == "CRITICAL"
        need = (min_minutes_critical if crit else min_minutes) * 60
        if a["kind"] == "NEW" and now - a.get("since", now) < need:
            held.append(a)
        elif quiet and SEV_RANK[a["severity"]] < floor:
            held.append(a)
        else:
            send.append(a)
    return send, held


# -- routing ------------------------------------------------------------------------------------
def route(routes, default_to, alerts, recovered, code_of):
    """[(recipients list or None, alerts, recovered)]: each area in [alert_routes] goes to its own people."""
    groups = {}
    for kind, items in (("a", alerts), ("r", recovered)):
        for item in items:
            module = code_of(item["key"])[1]
            to = tuple(x.strip() for x in (routes.get(module) or "").replace(";", ",").split(",") if x.strip()) or tuple(default_to or ())
            g = groups.setdefault(to, ([], []))
            g[0 if kind == "a" else 1].append(item)
    return [(list(to) or None, a, r) for to, (a, r) in groups.items()]


# -- acknowledgement ----------------------------------------------------------------------------
def drop_acknowledged(alerts, acks):
    """Reminders for a problem somebody acknowledged are not sent again; escalations and new problems still are."""
    return [a for a in alerts if not (a["kind"] == "STILL" and a["key"] in acks)]


# -- Slack and Teams ----------------------------------------------------------------------------
def detect_format(url, fmt="auto"):
    fmt = (fmt or "auto").lower()
    if fmt != "auto":
        return fmt
    for host, kind in HOOK_HOSTS:
        if host in (url or ""):
            return kind
    return "generic"


def payload(fmt, subject, body, status):
    color = {"CRITICAL": "#c5221f", "WARNING": "#ef8f00"}.get(status, "#1e8e3e")
    if fmt == "slack":
        icon = {"CRITICAL": ":red_circle:", "WARNING": ":large_orange_circle:"}.get(status, ":large_green_circle:")
        return {"text": "%s *%s*" % (icon, subject), "attachments": [{"color": color, "text": body[:3000]}]}
    if fmt == "teams":
        return {"type": "message", "attachments": [{"contentType": "application/vnd.microsoft.card.adaptive", "contentUrl": None, "content": {
            "$schema": "http://adaptivecards.io/schemas/adaptive-card.json", "type": "AdaptiveCard", "version": "1.4",
            "body": [{"type": "TextBlock", "text": subject, "weight": "Bolder", "size": "Medium", "wrap": True,
                      "color": "Attention" if status == "CRITICAL" else "Warning" if status == "WARNING" else "Good"},
                     {"type": "TextBlock", "text": body[:3000], "wrap": True, "fontType": "Monospace"}]}}]}
    return {"text": "%s\n%s" % (subject, body), "status": status}


def post(url, data, timeout=10):
    from urllib.request import Request, urlopen
    urlopen(Request(url, data=json.dumps(data).encode("utf-8"), headers={"Content-Type": "application/json"}), timeout=timeout).read(1024)


# -- daily digest -------------------------------------------------------------------------------
def digest(host, events, now, hours=24):
    """(subject, body, status) summarising the alerts of the last day, or None when nothing happened."""
    cutoff = now - hours * 3600
    recent = [e for e in events if e.get("ts", 0) >= cutoff and e.get("event") in ("open", "escalate", "close", "improve")]
    if not recent:
        return None
    opened = [e for e in recent if e["event"] == "open"]
    closed = [e for e in recent if e["event"] == "close"]
    crit = [e for e in recent if e.get("severity") == "CRITICAL" and e["event"] in ("open", "escalate")]
    lines = ["Alert digest for %s, the last %d hours (until %s)" % (host, hours, time.strftime("%Y-%m-%d %H:%M", time.localtime(now))), "",
             "%d problem(s) started, %d ended, %d critical." % (len(opened), len(closed), len(crit)), ""]
    names = {"open": "STARTED", "escalate": "WORSE", "close": "ENDED", "improve": "BETTER"}
    for e in sorted(recent, key=lambda x: x["ts"]):
        lines.append("%s  %-8s [%s] %s: %s" % (time.strftime("%H:%M", time.localtime(e["ts"])), names[e["event"]], e.get("severity", ""), e.get("component", ""), e.get("message", "")))
    return "[LSHME DIGEST] %s: %d alert(s) in 24 h" % (host, len(opened)), "\n".join(lines), "CRITICAL" if crit else "WARNING"


def digest_due(spec, now, last_day):
    """True once per day after the configured HH:MM."""
    m = re.match(r"^\s*(\d{1,2}):(\d{2})\s*$", spec or "")
    if not m:
        return False
    t = time.localtime(now)
    today = time.strftime("%Y-%m-%d", t)
    return last_day != today and t.tm_hour * 60 + t.tm_min >= int(m.group(1)) * 60 + int(m.group(2))
