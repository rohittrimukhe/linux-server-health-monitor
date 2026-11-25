# Linux Server Health Monitor Enterprise
# Copyright (c) 2026 LRS Services Pvt Ltd, Mumbai. All rights reserved.
"""HTML body of the real-time alert e-mails, in the same design as the report e-mails: a status banner, one card per alert with
what to do, recoveries shown apart (green, quiet), and a link to the dashboard. Tables and inline styles only (Outlook-safe)."""
import time
from html import escape as E

from lshme.reports.mailbody import EDGE, FONT, HEX, SOFT, STATUS, _logo

KIND = {"NEW": "New", "STILL": "Still open", "ESCALATED": "Got worse", "IMPROVED": "Improved", "RECOVERED": "Resolved"}


def _dur(sec):
    m = int(sec or 0) // 60
    return "%d min" % m if m < 120 else "%d h %d min" % (m // 60, m % 60)


def build(host, alerts, recovered, status, link="", brand_logo="", customer_logo=""):
    images = []
    lrs = _logo(brand_logo, "lrs-logo", images, 34)
    cust = _logo(customer_logo, "customer-logo", images, 30)
    col = STATUS.get(status, "#2563eb")
    crit = sum(1 for a in alerts if a["severity"] == "CRITICAL")
    title = ("%d critical alert%s" % (crit, "" if crit == 1 else "s")) if crit else ("%d warning%s" % (len(alerts), "" if len(alerts) == 1 else "s")) if alerts else "Resolved"
    rows = ["<tr><td style='padding:14px 24px;border-bottom:1px solid #e2e8f0'><table role='presentation' width='100%%' cellpadding='0' cellspacing='0'><tr>"
            "<td align='left'>%s</td><td align='right'>%s</td></tr></table></td></tr>" % (lrs or "<b style='%s;color:#123a6b'>LRS Vitals</b>" % FONT, cust)]
    rows.append(("<tr><td bgcolor='#123a6b' style='background:#123a6b;background-image:linear-gradient(135deg,#123a6b,#0e2d57);padding:18px 24px;%s'>"
                 "<table role='presentation' width='100%%' cellpadding='0' cellspacing='0'><tr><td>"
                 "<div style='color:#c7d7f5;font-size:12px;letter-spacing:.06em;text-transform:uppercase'>Real-time alert</div>"
                 "<div style='color:#fff;font-size:20px;font-weight:700;margin-top:2px'>%s</div>"
                 "<div style='color:#c7d7f5;font-size:13px;margin-top:4px'>%s</div></td>"
                 "<td align='right' style='vertical-align:middle'><span style='display:inline-block;background:%s;color:#fff;font-weight:800;font-size:14px;"
                 "padding:8px 14px;border-radius:8px'>%s</span></td></tr></table></td></tr>") % (
        FONT, E(host), E(time.strftime("%d %b %Y, %H:%M:%S")), col, E(title)))
    cards = []
    for a in alerts:
        lv = "cr" if a["severity"] == "CRITICAL" else "wn"
        since = (" - open for " + _dur(time.time() - a["since"])) if a.get("since") else ""
        cards.append(("<tr><td style='padding:6px 0'><table role='presentation' width='100%%' cellpadding='0' cellspacing='0'><tr>"
                      "<td bgcolor='%s' style='background:%s;border-left:4px solid %s;border-radius:8px;padding:12px 14px;%s'>"
                      "<div style='font-size:11px;font-weight:700;letter-spacing:.05em;color:%s;text-transform:uppercase'>%s - %s%s</div>"
                      "<div style='font-size:15px;font-weight:700;color:#0f172a;margin-top:3px'>%s</div>"
                      "<div style='font-size:14px;color:#1e293b;margin-top:2px'>%s</div>%s</td></tr></table></td></tr>") % (
            SOFT[lv], SOFT[lv], EDGE[lv], FONT, HEX[lv], E(a["severity"]), E(KIND.get(a.get("kind"), a.get("kind", ""))), E(since), E(a.get("component", "")),
            E(a.get("message", "")),
            ("<div style='font-size:13px;color:#475569;margin-top:6px'><b>What to do:</b> %s</div>" % E(a["recommendation"])) if a.get("recommendation") else ""))
    if cards:
        rows.append("<tr><td style='padding:16px 24px 4px'><table role='presentation' width='100%%' cellpadding='0' cellspacing='0'>%s</table></td></tr>" % "".join(cards))
    if recovered:
        rec = "".join(("<tr><td style='padding:5px 0;font-size:13.5px;color:#334155;%s'><span style='display:inline-block;background:#dcfce7;color:#14532d;font-weight:700;"
                       "font-size:11px;padding:2px 8px;border-radius:6px'>%s</span>&nbsp; <b>%s</b>: %s%s</td></tr>") % (
            FONT, E(KIND.get(r["kind"], r["kind"])), E(r.get("component", "")), E(r.get("message", "")),
            (" (after %s)" % _dur(r.get("duration"))) if r.get("kind") == "RECOVERED" and r.get("duration") else
            (" (was %s, now %s)" % (E(r.get("previous", "")), E(r.get("severity", "")))) if r.get("kind") == "IMPROVED" else "") for r in recovered)
        rows.append("<tr><td style='padding:12px 24px 4px;%s'><div style='font-size:13px;font-weight:700;color:#0f172a;margin-bottom:4px'>Resolved or improved</div>"
                    "<table role='presentation' width='100%%' cellpadding='0' cellspacing='0'>%s</table></td></tr>" % (FONT, rec))
    if link:
        rows.append("<tr><td style='padding:16px 24px 0'><table role='presentation' cellpadding='0' cellspacing='0'><tr><td bgcolor='#2563eb' style='background:#2563eb;border-radius:10px'>"
                    "<a href='%s' style='display:inline-block;padding:10px 20px;color:#fff;font-size:14px;font-weight:700;text-decoration:none;%s'>Open the dashboard &rarr;</a></td></tr></table></td></tr>" % (E(link), FONT))
    rows.append("<tr><td style='padding:16px 24px 20px;%s'><div style='border-top:1px solid #e2e8f0;padding-top:10px;font-size:11.5px;color:#94a3b8'>"
                "Reminders for a problem that stays open come every few hours; mute one from the dashboard (Alerts &gt; Actions). &copy; LRS Services Pvt Ltd, Mumbai.</div></td></tr>" % FONT)
    html = ("<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><meta name='color-scheme' content='light'>"
            "<title>%s</title></head><body style='margin:0;padding:0;background:#eef2f7;%s'>"
            "<div style='display:none;max-height:0;overflow:hidden'>%s: %s</div>"
            "<table role='presentation' width='100%%' cellpadding='0' cellspacing='0' bgcolor='#eef2f7' style='background:#eef2f7'><tr><td align='center' style='padding:18px 10px'>"
            "<table role='presentation' width='640' cellpadding='0' cellspacing='0' bgcolor='#ffffff' style='width:100%%;max-width:640px;background:#ffffff;border:1px solid #e2e8f0;border-radius:14px;overflow:hidden'>"
            "%s</table></td></tr></table></body></html>") % (E(host), FONT, E(host), E(title), "".join(rows))
    return html, images
