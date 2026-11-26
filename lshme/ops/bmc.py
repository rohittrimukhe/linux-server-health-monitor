# Linux Server Health Monitor Enterprise
# Copyright (c) 2026 LRS Services Pvt Ltd, Mumbai. All rights reserved.
"""Server hardware through its management controller (BMC), read-only. Nothing is installed on the BMC and nothing is changed.

Added by the administrator (terminal: `vitals bmc add`, or the web dashboard: Servers > Hardware), never built in:
main.conf [bmc:NAME] type, address, port, username, verify_tls, enabled, server (the server in servers.conf this hardware runs,
or _local for this server: used to connect hardware faults to the operating system, its VMs and databases);
the password is kept in credentials.conf [bmc:NAME] password.

Every current management controller speaks Redfish (the DMTF standard over HTTPS), so one driver covers them all:
  idrac (Dell iDRAC 8/9/10), ilo (HPE iLO 4/5/6), xcc (Lenovo XClarity Controller), imm (Lenovo / IBM IMM2), irmc (Fujitsu iRMC),
  cimc (Cisco CIMC), supermicro, openbmc, redfish (any other).
Older controllers without Redfish are read with ipmitool over IPMI-over-LAN (type ipmi); ipmitool must be installed.

Each reading returns the same shape, so checks, alerts and reports work the same for every make:
{name, type, make, model, serial, bios, bmc_firmware, power, status, cpus, memory_gb, temps, fans, psus, power_watts,
 drives, memory_faults, cpu_faults, events, reached}"""
import os
import re
import shlex
import subprocess
import time

from lshme.core.config import as_bool
from lshme.ops.san import Http, SanError, basic, reachable

TIMEOUT = 20
TYPES = {
    "idrac": ("Dell iDRAC (8, 9, 10)", 443),
    "ilo": ("HPE iLO (4, 5, 6)", 443),
    "xcc": ("Lenovo XClarity Controller (XCC, XCC2)", 443),
    "imm": ("Lenovo / IBM IMM2", 443),
    "irmc": ("Fujitsu iRMC", 443),
    "cimc": ("Cisco UCS CIMC", 443),
    "supermicro": ("Supermicro BMC", 443),
    "openbmc": ("OpenBMC", 443),
    "redfish": ("Other make with Redfish", 443),
    "ipmi": ("Older controller: IPMI over LAN (ipmitool)", 623),
}
ALIASES = {"dell": "idrac", "hpe": "ilo", "hp": "ilo", "lenovo": "xcc", "xclarity": "xcc", "ibm": "imm", "fujitsu": "irmc", "cisco": "cimc", "ucs": "cimc"}
HOWTO = {
    "idrac": "iDRAC: iDRAC Settings > Users > add a user with the Read Only role and Redfish/IPMI over LAN enabled.",
    "ilo": "iLO: Administration > User Administration > add a user with only the Login privilege.",
    "xcc": "XClarity Controller: BMC Configuration > User/LDAP > create a user with the Read-Only role.",
    "imm": "IMM2: IMM Management > Users > add a user with Read Only authority.",
    "irmc": "iRMC: User Management > add a user with the User (read) privilege and Redfish enabled.",
    "cimc": "CIMC: Admin > User Management > add a user with the read-only role.",
    "supermicro": "BMC web: Configuration > Users > add a user with the Operator or User privilege.",
    "openbmc": "OpenBMC: Access control > User management > add a ReadOnly user.",
    "redfish": "Create a read-only user on the BMC; Redfish over HTTPS must be enabled.",
    "ipmi": "Enable IPMI over LAN on the BMC and create a User-level account. Needs ipmitool on this server (dnf install ipmitool).",
}
OK = ("ok", "enabled", "optimal", "normal", "")


class BmcError(SanError):
    pass


def kind(t):
    t = ALIASES.get((t or "").strip().lower(), (t or "").strip().lower())
    if t not in TYPES:
        raise BmcError("unknown hardware type %r (use one of: %s)" % (t, ", ".join(TYPES)))
    return t


def bmcs(cfg):
    out = []
    for sec in cfg.parsers["main"].sections() if "main" in getattr(cfg, "parsers", {}) else []:
        if not sec.startswith("bmc:"):
            continue
        s = cfg.section("main", sec)
        try:
            t = kind(s.get("type", "redfish"))
        except BmcError:
            t = s.get("type", "")
        out.append({"name": sec[4:], "type": t, "address": s.get("address", ""), "port": int(s.get("port") or TYPES.get(t, ("", 443))[1]),
                    "username": s.get("username", ""), "password": cfg.secret(sec), "verify_tls": as_bool(s.get("verify_tls", "no"), False),
                    "enabled": as_bool(s.get("enabled", "yes"), True), "server": s.get("server", "")})
    return out


def _health(st):
    """Redfish Status -> ok / warning / critical / absent / unknown."""
    st = st or {}
    if str(st.get("State", "")).lower() in ("absent", "disabled", "unavailableoffline"):
        return "absent"
    h = str(st.get("HealthRollup") or st.get("Health") or "").lower()
    return {"ok": "ok", "warning": "warning", "critical": "critical"}.get(h, "unknown")


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def empty(a):
    return {"name": a["name"], "type": a["type"], "make": TYPES.get(a["type"], (a["type"],))[0], "model": "", "serial": "", "bios": "", "bmc_firmware": "",
            "power": "unknown", "status": "unknown", "cpus": None, "memory_gb": None, "temps": [], "fans": [], "psus": [], "power_watts": None,
            "drives": [], "memory_faults": [], "cpu_faults": [], "events": [], "reached": a.get("address", ""), "server": a.get("server", ""),
            "processors": [], "dimms": [], "system": {}}


def redfish(a, http=None, detail=False):
    http = http or Http(TIMEOUT)
    base = "https://%s:%d" % (a["address"], a["port"])
    hdr = basic(a["username"], a["password"])

    def get(path):
        return http.get(base + path if path.startswith("/") else path, hdr, a["verify_tls"]) or {}

    def members(path, limit=16):
        try:
            return [get(m.get("@odata.id", "")) for m in (get(path).get("Members") or [])[:limit] if m.get("@odata.id")]
        except SanError as e:
            if "refused" in str(e):
                raise
            return []

    out = empty(a)
    root = get("/redfish/v1/")                                  # proves the address and that it speaks Redfish
    if not root.get("Systems"):
        raise BmcError("%s answers but offers no Redfish systems (is the type right?)" % a["address"])
    systems = members(root["Systems"]["@odata.id"])
    if not systems:
        raise BmcError("the BMC refused to list its systems (check the user's rights)")
    s = systems[0]
    out.update(model=(s.get("Model") or "").strip(), serial=(s.get("SerialNumber") or "").strip(), bios=s.get("BiosVersion") or "",
               power=str(s.get("PowerState") or "unknown").lower(), status=_health(s.get("Status")),
               make=(s.get("Manufacturer") or out["make"]).strip())
    ps, ms = s.get("ProcessorSummary") or {}, s.get("MemorySummary") or {}
    out["cpus"] = ps.get("Count")
    out["memory_gb"] = _num(ms.get("TotalSystemMemoryGiB"))
    for kind_, summary, sink in (("Processors", ps, "cpu_faults"), ("Memory", ms, "memory_faults")):
        if _health(summary.get("Status")) in ("warning", "critical"):
            out[sink].append({"name": kind_, "status": _health(summary.get("Status"))})
    if s.get("Memory") and out["memory_faults"]:                 # only when the summary says something is wrong: which DIMM
        for m in members(s["Memory"]["@odata.id"]):
            h = _health(m.get("Status"))
            if h in ("warning", "critical"):
                out["memory_faults"].append({"name": m.get("DeviceLocator") or m.get("Name") or m.get("Id"), "status": h})
    if detail:                                                   # inventory for the hardware page: every processor and memory module
        out["system"] = {"host_name": s.get("HostName") or "", "uuid": s.get("UUID") or "", "sku": s.get("SKU") or "", "asset_tag": s.get("AssetTag") or "",
                         "indicator_led": s.get("IndicatorLED") or "", "boot": ((s.get("Boot") or {}).get("BootSourceOverrideTarget") or "")}
        if s.get("Processors"):
            for c in members(s["Processors"]["@odata.id"], 8):
                if _health(c.get("Status")) == "absent" or (c.get("ProcessorType") or "CPU") not in ("CPU", ""):
                    continue
                out["processors"].append({"socket": c.get("Socket") or c.get("Id") or "", "model": (c.get("Model") or "").strip(), "cores": c.get("TotalCores"),
                                          "threads": c.get("TotalThreads"), "mhz": c.get("MaxSpeedMHz") or c.get("OperatingSpeedMHz"), "status": _health(c.get("Status"))})
        if s.get("Memory"):
            for m in members(s["Memory"]["@odata.id"], 64):
                h = _health(m.get("Status"))
                if h == "absent" or not (m.get("CapacityMiB") or 0):
                    continue
                out["dimms"].append({"slot": m.get("DeviceLocator") or m.get("Name") or m.get("Id"), "gb": round((m.get("CapacityMiB") or 0) / 1024.0, 1),
                                     "type": m.get("MemoryDeviceType") or "", "mhz": m.get("OperatingSpeedMhz") or (m.get("AllowedSpeedsMHz") or [None])[0],
                                     "maker": (m.get("Manufacturer") or "").strip(), "part": (m.get("PartNumber") or "").strip(), "status": h})
    if s.get("Storage"):
        for st in members(s["Storage"]["@odata.id"]):
            for d in (st.get("Drives") or [])[:32]:
                try:
                    dv = get(d.get("@odata.id", ""))
                except SanError:
                    continue
                out["drives"].append({"name": dv.get("Name") or dv.get("Id"), "model": dv.get("Model") or "", "media": dv.get("MediaType") or "",
                                      "capacity": _num(dv.get("CapacityBytes")), "status": _health(dv.get("Status")),
                                      "predicted_failure": bool(dv.get("FailurePredicted"))})
    for ch in members(root.get("Chassis", {}).get("@odata.id", "/redfish/v1/Chassis"))[:2]:
        th = {}
        if ch.get("Thermal"):
            try:
                th = get(ch["Thermal"]["@odata.id"])
            except SanError:
                th = {}
        for t in th.get("Temperatures") or []:
            v = _num(t.get("ReadingCelsius"))
            if v is None or _health(t.get("Status")) == "absent":
                continue
            out["temps"].append({"name": t.get("Name") or t.get("MemberId"), "celsius": v, "warn": _num(t.get("UpperThresholdNonCritical")),
                                 "crit": _num(t.get("UpperThresholdCritical")), "status": _health(t.get("Status"))})
        for f in th.get("Fans") or []:
            if _health(f.get("Status")) == "absent":
                continue
            out["fans"].append({"name": f.get("Name") or f.get("FanName") or f.get("MemberId"), "rpm": _num(f.get("Reading")), "status": _health(f.get("Status"))})
        pw = {}
        if ch.get("Power"):
            try:
                pw = get(ch["Power"]["@odata.id"])
            except SanError:
                pw = {}
        for p in pw.get("PowerSupplies") or []:
            if _health(p.get("Status")) == "absent":
                continue
            out["psus"].append({"name": p.get("Name") or p.get("MemberId"), "watts": _num(p.get("PowerOutputWatts") or p.get("LastPowerOutputWatts")),
                                "status": _health(p.get("Status"))})
        for c in pw.get("PowerControl") or []:
            if out["power_watts"] is None:
                out["power_watts"] = _num(c.get("PowerConsumedWatts"))
    for m in members(root.get("Managers", {}).get("@odata.id", "/redfish/v1/Managers"))[:1]:
        out["bmc_firmware"] = m.get("FirmwareVersion") or ""
        if m.get("LogServices"):                                  # the system event log: recent critical entries
            for ls in members(m["LogServices"]["@odata.id"])[:3]:
                if not ls.get("Entries"):
                    continue
                try:
                    ents = get(ls["Entries"]["@odata.id"] + "?$top=50").get("Members") or []
                except SanError:
                    continue
                for e in ents[:50]:
                    sev = str(e.get("Severity") or "").lower()
                    sev = sev if sev in ("critical", "warning") else "info"
                    out["events"].append({"time": e.get("Created") or "", "severity": sev, "text": (e.get("Message") or e.get("Name") or "")[:200]})
    out["events"] = sorted(out["events"], key=lambda e: e["time"], reverse=True)[:40]
    return out


def _ipmitool(a, args, run=None):
    if run:
        return run(a, args)
    cmd = ["ipmitool", "-I", "lanplus", "-H", a["address"], "-p", str(a["port"]), "-U", a["username"], "-E"] + args
    try:
        p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=TIMEOUT, env=dict(os.environ, IPMI_PASSWORD=a["password"] or ""))
    except FileNotFoundError:
        raise BmcError("ipmitool is not installed on this server (dnf install ipmitool)")
    except subprocess.TimeoutExpired:
        raise BmcError("%s did not answer IPMI in %d s" % (a["address"], TIMEOUT))
    if p.returncode:
        err = (p.stderr.decode("utf-8", "replace").strip().splitlines() or ["IPMI failed"])[-1]
        raise BmcError("IPMI on %s: %s" % (a["address"], err))
    return p.stdout.decode("utf-8", "replace")


def ipmi(a, run=None):
    """Older controllers: chassis status, sensors (sdr) and the event log (sel) over IPMI-over-LAN."""
    out = empty(a)
    ch = _ipmitool(a, ["chassis", "status"], run)
    m = re.search(r"System Power\s*:\s*(\w+)", ch)
    out["power"] = m.group(1).lower() if m else "unknown"
    worst = "ok"
    for line in _ipmitool(a, ["sdr", "elist", "full"], run).splitlines():
        parts = [p.strip() for p in line.split("|")]
        if len(parts) < 5:
            continue
        name, _, st, _, reading = parts[:5]
        st = st.lower()
        status = "ok" if st == "ok" else "absent" if st in ("ns", "na") else "critical" if st in ("cr", "nr") else "warning"
        if status == "absent":
            continue
        val = _num((reading.split() or [""])[0])
        if "degrees" in reading.lower():
            out["temps"].append({"name": name, "celsius": val, "warn": None, "crit": None, "status": status})
        elif "rpm" in reading.lower():
            out["fans"].append({"name": name, "rpm": val, "status": status})
        elif re.search(r"\bps\d|power supply|psu", name.lower()):
            out["psus"].append({"name": name, "watts": val if "watts" in reading.lower() else None, "status": status})
        if status == "critical" or (status == "warning" and worst == "ok"):
            worst = status
    out["status"] = worst
    try:
        for line in _ipmitool(a, ["sel", "elist", "last", "30"], run).splitlines()[-30:]:
            parts = [p.strip() for p in line.split("|")]
            if len(parts) >= 5 and re.search(r"fail|fault|critical|error|non-recoverable|asserted", line, re.I) and "Deasserted" not in line:
                out["events"].append({"time": "%s %s" % (parts[1], parts[2]), "severity": "critical" if re.search(r"fail|non-recoverable|critical", line, re.I) else "warning",
                                      "text": " ".join(parts[3:])[:200]})
    except BmcError:
        pass
    try:
        fru = _ipmitool(a, ["fru", "print", "0"], run)
        for key, field in (("Product Name", "model"), ("Product Serial", "serial"), ("Board Mfg", "make")):
            m = re.search(r"%s\s*:\s*(.+)" % key, fru)
            if m:
                out[field] = m.group(1).strip()
    except BmcError:
        pass
    out["events"] = out["events"][-20:][::-1]
    return out


def collect(a, http=None, run=None, detail=False):
    if not a["address"]:
        raise BmcError("no BMC address configured")
    t = kind(a["type"])
    out = ipmi(a, run) if t == "ipmi" else redfish(a, http, detail)
    bad = [x for x in out["psus"] + out["fans"] + out["drives"] + out["memory_faults"] + out["cpu_faults"] if x["status"] == "critical"]
    hot = [x for x in out["temps"] if x.get("crit") and x["celsius"] is not None and x["celsius"] >= x["crit"]]
    if bad or hot:
        out["status"] = "critical"
    elif out["status"] in ("ok", "unknown") and any(x["status"] == "warning" for x in out["psus"] + out["fans"] + out["drives"] + out["temps"]):
        out["status"] = "warning"
    return out


def test(a, http=None, run=None):
    t0 = time.time()
    if kind(a["type"]) != "ipmi":
        ok, why = reachable(a["address"], a["port"])
        if not ok:
            return {"ok": False, "message": "%s:%d does not answer: %s" % (a["address"], a["port"], why)}
    try:
        d = collect(a, http, run)
    except SanError as e:
        return {"ok": False, "message": str(e)}
    return {"ok": True, "message": "Connected to %s in %.1f s: %s %s, serial %s, power %s, health %s; %d temperature, %d fan, %d power supply, %d drive readings." % (
        a["address"], time.time() - t0, d["make"], d["model"] or "", d["serial"] or "-", d["power"], d["status"], len(d["temps"]), len(d["fans"]), len(d["psus"]), len(d["drives"])),
        "summary": {"model": d["model"], "serial": d["serial"], "status": d["status"], "power": d["power"]}}


def safe_name(name):
    n = "".join(ch if ch.isalnum() or ch in "-_." else "-" for ch in (name or "").strip())[:40].strip("-")
    if not n:
        raise BmcError("give the hardware a short name, e.g. kvm1-idrac")
    return n


def save(app, name, type_, address, username, password=None, port=None, verify_tls=False, enabled=True, server=""):
    name, t = safe_name(name), kind(type_)
    address = (address or "").strip()
    if not address or not all(ch.isalnum() or ch in ".-:[]" for ch in address):
        raise BmcError("give the BMC's management address (IP or name)")
    if not username:
        raise BmcError("a read-only user name is needed")
    port = int(port or TYPES[t][1])
    if not 1 <= port <= 65535:
        raise BmcError("port must be 1-65535")
    sec = "bmc:" + name
    for k, v in (("type", t), ("address", address), ("port", str(port)), ("username", username), ("verify_tls", "yes" if verify_tls else "no"),
                 ("enabled", "yes" if enabled else "no"), ("server", (server or "").strip())):
        app.cfg.set("main", sec, k, v)
    app.save_config("main")
    if password:
        app.cfg.set("credentials", sec, "password", password)
        app.save_config("credentials")
    app.reload()
    return name


def remove(app, name):
    sec = "bmc:" + safe_name(name)
    app.cfg.remove_section("main", sec)
    app.save_config("main")
    try:
        app.cfg.remove_section("credentials", sec)
        app.save_config("credentials")
    except Exception:
        pass
    app.reload()


def find(cfg, name):
    for a in bmcs(cfg):
        if a["name"] == name:
            return a
    names = [a["name"] for a in bmcs(cfg)]
    raise BmcError("no hardware named %r (%s)" % (name, ("added: " + ", ".join(names)) if names else "none added yet: vitals bmc add"))


def public(a):
    out = dict((k, v) for k, v in a.items() if k != "password")
    out.update(has_password=bool(a.get("password")), type_label=TYPES.get(a["type"], (a["type"],))[0])
    return out


def describe(a):
    return "%s (%s) at %s%s" % (a["name"], TYPES.get(a["type"], (a["type"],))[0], a["address"], (" for " + a["server"]) if a.get("server") else "")


_ = shlex  # kept for callers that quote BMC names in remote commands
