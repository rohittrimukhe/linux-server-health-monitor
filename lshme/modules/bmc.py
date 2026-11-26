# Linux Server Health Monitor Enterprise
# Copyright (c) 2026 LRS Services Pvt Ltd, Mumbai. All rights reserved.
"""Server hardware through its management controller (main.conf [bmc:NAME]): overall health, power state, power supplies,
fans, temperatures, drives, memory and processor faults, recent critical events in the system event log, power draw.
Read-only, over Redfish (iDRAC, iLO, XClarity, IMM, iRMC, CIMC, Supermicro, OpenBMC) or IPMI over LAN (lshme.ops.bmc)."""
import calendar
import re
import time

NAME = "bmc"
TITLE = "Server Hardware (BMC)"
WEIGHT = 10


def event_time(text):
    """Epoch seconds of a BMC event time: Redfish '2026-06-17T08:20:55+00:00' (or Z) or IPMI '06/17/2026 08:20:55'. None if unknown."""
    text = (text or "").strip()
    m = re.match(r"(\d{4})-(\d\d)-(\d\d)[T ](\d\d):(\d\d):(\d\d)(?:\.\d+)?(Z|[+-]\d\d:?\d\d)?", text)
    if m:
        y, mo, d, hh, mi, ss = (int(x) for x in m.groups()[:6])
        tz = m.group(7)
        if tz:
            off = 0 if tz == "Z" else (1 if tz[0] == "+" else -1) * (int(tz[1:3]) * 3600 + int(tz[-2:]) * 60)
            return calendar.timegm((y, mo, d, hh, mi, ss, 0, 0, 0)) - off
        return time.mktime((y, mo, d, hh, mi, ss, 0, 0, -1))
    m = re.match(r"(\d\d)/(\d\d)/(\d{4})\s+(\d\d):(\d\d):(\d\d)", text)
    if m:
        mo, d, y, hh, mi, ss = (int(x) for x in m.groups())
        return time.mktime((y, mo, d, hh, mi, ss, 0, 0, -1))
    return None


def run(ctx, res):
    from lshme.ops import bmc
    items = [a for a in bmc.bmcs(ctx.cfg) if a["enabled"]]
    if not items:
        res.status = "SKIPPED"
        res.skip("no server hardware (BMC) added (vitals bmc add, or Servers > Server hardware in the web dashboard)")
        res.summary = "no server hardware added"
        return
    t_warn, t_crit = ctx.t("bmc", "temperature_warning", 0), ctx.t("bmc", "temperature_critical", 0)
    done = []
    for a in items:
        n = a["name"]
        try:
            d = bmc.collect(a)
        except bmc.BmcError as e:
            res.add("CRITICAL", "BMC_UNREACHABLE", n, "Hardware %s cannot be read: %s" % (n, e),
                    recommendation="Check the BMC's management network, the read-only user and its password (vitals bmc test %s)." % n)
            res.metric("bmc.reachable:" + n, 0)
            done.append({"name": n, "type": a["type"], "server": a.get("server", ""), "error": str(e)})
            continue
        res.metric("bmc.reachable:" + n, 1)
        on = d["server"] or n
        if d["power"] not in ("on", "unknown"):
            res.add("CRITICAL", "BMC_POWER_OFF", n, "Hardware %s (%s) is powered %s" % (n, on, d["power"]),
                    recommendation="Find out why it is off before powering it on: the event log on the BMC names the cause.")
        if d["status"] == "critical":
            res.add("CRITICAL", "BMC_HEALTH", n, "Hardware %s (%s) reports a critical fault" % (n, on),
                    recommendation="Open the BMC: the parts listed below or its event log name the failed component.")
        elif d["status"] == "warning":
            res.add("WARNING", "BMC_HEALTH", n, "Hardware %s (%s) reports a warning" % (n, on))
        for kind_, items_, code, rec in (("power supply", d["psus"], "BMC_PSU", "The server runs on its remaining power supply; replace the failed one."),
                                         ("fan", d["fans"], "BMC_FAN", "Replace the fan; the other fans run faster and parts run hotter until then."),
                                         ("drive", d["drives"], "BMC_DRIVE", "Replace the drive; until then the RAID set runs without its full protection."),
                                         ("memory", d["memory_faults"], "BMC_MEMORY", "Replace the DIMM in the next window; correctable errors often turn uncorrectable."),
                                         ("processor", d["cpu_faults"], "BMC_CPU", "Engage the hardware vendor.")):
            for x in items_:
                if x["status"] in ("critical", "warning"):
                    res.add("CRITICAL" if x["status"] == "critical" else "WARNING", code, "%s/%s" % (n, x["name"]),
                            "Hardware %s: %s %s is %s" % (n, kind_, x["name"], x["status"]), recommendation=rec)
            if kind_ == "drive":
                for x in items_:
                    if x.get("predicted_failure"):
                        res.add("WARNING", "BMC_DRIVE_PFA", "%s/%s" % (n, x["name"]), "Hardware %s: drive %s predicts it will fail soon" % (n, x["name"]),
                                recommendation="Plan a replacement before it fails.")
        for t in d["temps"]:
            v = t["celsius"]
            if v is None:
                continue
            crit = t.get("crit") or (t_crit or None)
            warn = t.get("warn") or (t_warn or None)
            if crit and v >= crit:
                res.add("CRITICAL", "BMC_TEMPERATURE", "%s/%s" % (n, t["name"]), "Hardware %s: %s is %.0f C (limit %.0f C)" % (n, t["name"], v, crit),
                        recommendation="Check cooling: room temperature, blocked air flow, failed fans.")
            elif warn and v >= warn:
                res.add("WARNING", "BMC_TEMPERATURE", "%s/%s" % (n, t["name"]), "Hardware %s: %s is %.0f C (warning %.0f C)" % (n, t["name"], v, warn))
        parts = d["psus"] + d["fans"] + d["drives"] + d["memory_faults"] + d["cpu_faults"] + d["temps"]
        healthy_now = d["status"] in ("ok", "unknown") and not any(x.get("status") in ("critical", "warning") for x in parts)
        recent = ctx.t("bmc", "event_hours", 24) * 3600          # older event-log lines are history: the live sensor readings above show the parts now
        for e in [e for e in d["events"] if e["severity"] == "critical"][:10]:
            ts = event_time(e.get("time"))
            if ts is None or time.time() - ts > recent:
                continue
            if healthy_now:                                      # the hardware reads healthy again: the problem is closed, not counted
                res.add("INFO", "BMC_EVENT_CLOSED", n, "Hardware %s event log: %s (%s) - closed, the hardware reads healthy now" % (n, e["text"], e["time"]))
                continue
            res.add("WARNING", "BMC_EVENT", n, "Hardware %s event log: %s (%s)" % (n, e["text"], e["time"]),
                    recommendation="Open the hardware page or the BMC to see which part still reports it. "
                                   "It closes by itself when the hardware reads healthy again.")
        if d["power_watts"] is not None:
            res.metric("bmc.power_watts:" + n, d["power_watts"])
        temps = [t["celsius"] for t in d["temps"] if t["celsius"] is not None]
        if temps:
            res.metric("bmc.temp_max:" + n, max(temps))
        res.metric("bmc.faults:" + n, sum(1 for x in d["psus"] + d["fans"] + d["drives"] + d["memory_faults"] + d["cpu_faults"] if x["status"] in ("critical", "warning")))
        done.append(d)
    res.data = {"bmcs": done}
    res.table("Server hardware", ["Name", "For server", "Make / model", "Serial", "Power", "Health", "Power draw", "Hottest"],
              [[d["name"], d.get("server") or "-", ("%s %s" % (d.get("make", ""), d.get("model", ""))).strip() or d.get("type", ""), d.get("serial") or "-",
                d.get("power", "-") if not d.get("error") else "unreachable", d.get("status", "-") if not d.get("error") else "unreachable",
                ("%.0f W" % d["power_watts"]) if d.get("power_watts") is not None else "-",
                ("%.0f C" % max(t["celsius"] for t in d["temps"] if t["celsius"] is not None)) if [t for t in d.get("temps", []) if t["celsius"] is not None] else "-"]
               for d in done], kind="summary")
    ok = [d for d in done if not d.get("error")]
    res.summary = "%d server hardware unit(s), %d answering" % (len(done), len(ok))
