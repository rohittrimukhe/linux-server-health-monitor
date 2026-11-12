# Linux Server Health Monitor Enterprise
# Copyright (c) 2026 LRS Services Pvt Ltd, Mumbai. All rights reserved.
"""Hardware inventory, sensors, ECC memory errors, IPMI and hardware RAID controllers."""
import re

from lshme.core.util import to_int

NAME = "hardware"
TITLE = "Hardware & RAID"
WEIGHT = 8


def dmi(ctx):
    base = "/sys/class/dmi/id/"
    keys = ("sys_vendor", "product_name", "product_serial", "bios_vendor", "bios_version", "bios_date",
            "board_name", "chassis_type")
    return dict((k, ctx.read(base + k, "")) for k in keys)


def sensors(ctx):
    """Temperatures from lm-sensors (sensors -u) or /sys/class/thermal."""
    temps = []
    if ctx.has("sensors"):
        r = ctx.run(["sensors", "-u"])
        chip, label = "", ""
        for line in r.out.splitlines():
            if line and not line.startswith(" ") and ":" not in line:
                chip = line.strip()
            elif line.endswith(":") and not line.startswith(" "):
                label = line[:-1].strip()
            m = re.match(r"\s+(temp\d+)_input:\s*([\d.]+)", line)
            if m:
                temps.append({"sensor": "%s %s" % (chip, label), "celsius": float(m.group(2)), "crit": None})
            m = re.match(r"\s+(temp\d+)_crit:\s*([\d.]+)", line)
            if m and temps:
                temps[-1]["crit"] = float(m.group(2))
    if not temps:
        for zone in ctx.runner.glob("/sys/class/thermal/thermal_zone*"):
            v = to_int(ctx.read(zone + "/temp", ""))
            if v is not None and v > 0:
                temps.append({"sensor": ctx.read(zone + "/type", zone), "celsius": v / 1000.0, "crit": None})
    return temps


def edac(ctx):
    out = []
    for mc in ctx.runner.glob("/sys/devices/system/edac/mc/mc*"):
        out.append({"controller": mc.rsplit("/", 1)[-1], "ce": to_int(ctx.read(mc + "/ce_count", "0"), 0),
                    "ue": to_int(ctx.read(mc + "/ue_count", "0"), 0)})
    return out


def ipmi(ctx):
    if not (ctx.has("ipmitool") and ctx.runner.exists("/dev/ipmi0")):
        return None
    r = ctx.run(["ipmitool", "sdr", "elist"], timeout=60)
    bad = []
    for line in r.lines():
        p = [x.strip() for x in line.split("|")]
        if len(p) >= 3 and p[2].lower() not in ("ok", "ns", "na", ""):
            bad.append(" | ".join(p))
    sel = ctx.run(["ipmitool", "sel", "elist", "last", "20"], timeout=60)
    return {"abnormal": bad, "sel_tail": sel.out[-4000:]}


def raid_controllers(ctx):
    """Hardware RAID status via storcli/perccli, MegaCli or HPE ssacli/hpssacli."""
    out = []
    for tool in ("storcli64", "storcli", "perccli64", "perccli"):
        if ctx.has(tool):
            r = ctx.run([tool, "/call/vall", "show"], timeout=90)
            for line in r.lines():
                m = re.match(r"^\s*(\d+/\d+)\s+(RAID\d+|Cac\d+)\s+(\S+)", line)
                if m:
                    out.append({"tool": tool, "volume": m.group(1), "level": m.group(2), "state": m.group(3),
                                "ok": m.group(3) in ("Optl", "Optimal")})
            r = ctx.run([tool, "/call/eall/sall", "show"], timeout=90)
            for line in r.lines():
                m = re.match(r"^\s*(\d+:\d+)\s+\d+\s+(\S+)\s+", line)
                if m and m.group(2) in ("Failed", "Offln", "UBad", "Rbld", "Msng"):
                    out.append({"tool": tool, "volume": "disk " + m.group(1), "level": "", "state": m.group(2), "ok": False})
            return out
    for tool in ("MegaCli64", "MegaCli", "megacli"):
        if ctx.has(tool):
            r = ctx.run([tool, "-LDInfo", "-Lall", "-aALL", "-NoLog"], timeout=90)
            vd = None
            for line in r.lines():
                m = re.match(r"Virtual Drive:\s*(\d+)", line)
                if m:
                    vd = m.group(1)
                m = re.match(r"State\s*:\s*(.+)", line)
                if m and vd is not None:
                    st = m.group(1).strip()
                    out.append({"tool": tool, "volume": "VD" + vd, "level": "", "state": st, "ok": st == "Optimal"})
            return out
    for tool in ("ssacli", "hpssacli", "hpacucli"):
        if ctx.has(tool):
            r = ctx.run([tool, "ctrl", "all", "show", "config"], timeout=90)
            for line in r.lines():
                m = re.search(r"(logicaldrive \S+|physicaldrive \S+).*?,\s*([A-Za-z ]+)\)\s*$", line.strip())
                if m:
                    st = m.group(2).strip()
                    out.append({"tool": tool, "volume": m.group(1), "level": "", "state": st, "ok": st == "OK"})
            return out
    return out


def run(ctx, res):
    info = dmi(ctx)
    temps = sensors(ctx)
    ecc = edac(ctx)
    ip = ipmi(ctx)
    raid = raid_controllers(ctx)
    mem_dimms = []
    if ctx.has("dmidecode") and ctx.runner.is_root():
        r = ctx.run(["dmidecode", "-t", "memory"])
        for block in r.out.split("\n\n"):
            if "Memory Device" in block:
                size = re.search(r"\n\s*Size:\s*(.+)", block)
                loc = re.search(r"\n\s*Locator:\s*(.+)", block)
                if size and "No Module" not in size.group(1):
                    mem_dimms.append([loc.group(1).strip() if loc else "?", size.group(1).strip()])
    res.data = {"dmi": info, "temperatures": temps, "edac": ecc, "ipmi": ip, "raid": raid, "dimms": mem_dimms}
    res.table("Hardware inventory", ["Item", "Value"], [
        ["Manufacturer", info["sys_vendor"] or "unknown"], ["Model", info["product_name"] or "unknown"],
        ["Serial number", info["product_serial"] or "restricted"], ["BIOS", ("%s %s %s" % (info["bios_vendor"], info["bios_version"], info["bios_date"])).strip() or "unknown"],
        ["Memory modules", "%d populated" % len(mem_dimms) if mem_dimms else "N/A (needs root + dmidecode)"],
    ], kind="summary")
    if mem_dimms:
        res.table("Memory modules", ["Slot", "Size"], mem_dimms)

    tw, tc = ctx.t("hardware", "temperature_warning_c", 75), ctx.t("hardware", "temperature_critical_c", 90)
    if temps:
        res.table("Temperatures", ["Sensor", "Celsius", "Critical"], [[t["sensor"], t["celsius"], t["crit"] or ""] for t in temps])
        hottest = max(t["celsius"] for t in temps)
        res.metric("hardware.max_temperature_c", hottest)
        for t in temps:
            crit = t["crit"] or tc
            if t["celsius"] >= crit:
                res.add("CRITICAL", "TEMPERATURE_CRITICAL", t["sensor"], "%s at %.0f°C" % (t["sensor"], t["celsius"]),
                        recommendation="Check fans, airflow and data-centre cooling immediately.")
            elif t["celsius"] >= tw:
                res.add("WARNING", "TEMPERATURE_HIGH", t["sensor"], "%s at %.0f°C" % (t["sensor"], t["celsius"]))
    else:
        res.skip("no temperature sensors readable (virtual machine or lm_sensors missing)")
    for e in ecc:
        if e["ue"]:
            res.add("CRITICAL", "MEMORY_UNCORRECTABLE", e["controller"], "%d uncorrectable memory errors" % e["ue"],
                    recommendation="Replace the failing DIMM; uncorrectable errors can crash the server.")
        elif e["ce"] > 100:
            res.add("WARNING", "MEMORY_CORRECTABLE", e["controller"], "%d correctable memory errors" % e["ce"],
                    recommendation="A DIMM is degrading; schedule replacement.")
    if ip:
        for line in ip["abnormal"][:20]:
            res.add("WARNING", "IPMI_SENSOR", line.split("|")[0].strip(), "IPMI sensor not OK: %s" % line)
    if raid:
        res.table("Hardware RAID", ["Tool", "Volume/Disk", "Level", "State"], [[r["tool"], r["volume"], r["level"], r["state"]] for r in raid])
        for r in raid:
            if not r["ok"]:
                sev = "WARNING" if r["state"] in ("Rbld", "Rebuilding", "Recovering") else "CRITICAL"
                res.add(sev, "HW_RAID_DEGRADED", r["volume"], "RAID %s state is %s" % (r["volume"], r["state"]),
                        recommendation="Replace failed disks and monitor the rebuild.")
    else:
        res.skip("no hardware RAID CLI (storcli/perccli/MegaCli/ssacli) found")
    res.summary = "%s %s; %d temperature sensors; %d RAID volumes" % (
        info["sys_vendor"], info["product_name"], len(temps), len(raid))
