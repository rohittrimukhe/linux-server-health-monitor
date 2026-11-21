# Linux Server Health Monitor Enterprise
# Copyright (c) 2026 LRS Services Pvt Ltd, Mumbai. All rights reserved.
"""SAN storage arrays, read-only over each make's own management API. Nothing is installed on the array and nothing is changed.

Arrays are added by the administrator (terminal: `vitals san add`, or the web dashboard: Settings > SAN storage), never built in:
main.conf [san:NAME] type, controllers (A and B management addresses), port, username, verify_tls, enabled;
the password is kept in credentials.conf [san:NAME] password.

Drivers (type), each over the make's documented management API, full health: array status, controllers, drives, pools,
volumes, fans / power / batteries, critical events and I/O where the API gives them:
  santricity  Lenovo ThinkSystem DE, NetApp E-Series        msa         HPE MSA, Dell PowerVault ME4/ME5, Lenovo ThinkSystem DS
  ontap       NetApp ONTAP, Lenovo ThinkSystem DM            unity       Dell Unity / Unity XT
  powerstore  Dell PowerStore                                primera     HPE 3PAR / Primera / Alletra 9000
  pure        Pure Storage FlashArray                        svc         IBM FlashSystem / Storwize / SVC
  oceanstor   Huawei OceanStor / Dorado                      reach       a make with no supported API: management ports answer

Each driver returns the same shape, so checks, alerts and reports work the same for every make:
{type, name, model, serial, firmware, status, reached, unreachable, controllers, drives, pools, volumes, hardware, failures, events, perf}"""
import base64
import hashlib
import json
import socket
import ssl
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from lshme.core.config import as_bool, as_list

TYPES = {
    "santricity": ("Lenovo ThinkSystem DE / NetApp E-Series", 8443),
    "msa": ("HPE MSA / Dell PowerVault ME4, ME5 / Lenovo ThinkSystem DS", 443),
    "ontap": ("NetApp ONTAP (AFF, FAS, ASA) / Lenovo ThinkSystem DM", 443),
    "unity": ("Dell Unity / Unity XT", 443),
    "powerstore": ("Dell PowerStore", 443),
    "primera": ("HPE 3PAR / Primera / Alletra 9000", 8080),
    "pure": ("Pure Storage FlashArray", 443),
    "svc": ("IBM FlashSystem / Storwize / SAN Volume Controller", 7443),
    "oceanstor": ("Huawei OceanStor / Dorado", 8088),
    "reach": ("Other make: reachability only (no API)", 443),
}
# how to connect each make: shown next to the form in the terminal and the web dashboard; docs/SAN_STORAGE.md is the full guide
HOWTO = {
    "santricity": "ThinkSystem System Manager / SANtricity: Settings > Access Management > Local User Roles > select 'monitor' > Change Password. "
                  "Enter both controllers' management IPs, user monitor, port 8443.",
    "msa": "Storage Management Utility / PowerVault Manager: Settings > Users > add a user with the 'monitor' role and the WBI/HTTPS interface. "
           "Enter both controllers' management IPs, port 443.",
    "ontap": "ONTAP System Manager: Cluster > Settings > Users and Roles > add a user, application 'http', role 'readonly'. "
             "Enter the cluster management IP as A (a node management IP as B), port 443.",
    "unity": "Unisphere: Settings > Users and Groups > User Management > add a local user with the 'Operator' role. Enter the management IP, port 443.",
    "powerstore": "PowerStore Manager: Settings > Users > add a local user with the 'Operator' role. Enter the cluster management IP, port 443.",
    "primera": "Enable the WSAPI service on the array (CLI: setwsapi -https enable; startwsapi) and add a user with the 'browse' role (createuser). "
               "Enter the system management IP, port 8080 (443 on newer releases).",
    "pure": "Purity GUI: Settings > Users and Policies > create a user with the 'Read-Only' role, then 'Create API Token' for it. "
            "Leave the user empty and paste the API token as the password. Enter the array management IP (CT0 / CT1), port 443.",
    "svc": "Management GUI: Access > Users by Group > Monitor > Create User. Enter the cluster management IP as A (a node service IP as B), port 7443.",
    "oceanstor": "DeviceManager: Settings > User and Security > Users and Roles > create a local user with the read-only (monitor) role. "
                 "Enter both controllers' management IPs, port 8088.",
    "reach": "For a make without a supported API: only checks that each controller's management port answers. "
             "Set the array's own e-mail or SNMP alerts as well.",
}
ALIASES = {"lenovo-de": "santricity", "e-series": "santricity", "netapp-e": "santricity", "de": "santricity",
           "hpe-msa": "msa", "powervault": "msa", "me4": "msa", "me5": "msa", "lenovo-ds": "msa",
           "netapp": "ontap", "lenovo-dm": "ontap", "unity-xt": "unity", "3par": "primera", "alletra": "primera",
           "flasharray": "pure", "ibm": "svc", "flashsystem": "svc", "storwize": "svc", "huawei": "oceanstor", "dorado": "oceanstor",
           "other": "reach", "generic": "reach"}
TIMEOUT = 20


class SanError(ValueError):                 # shown as a plain message in the terminal and the web, never a traceback
    pass


def kind(t):
    t = (t or "").strip().lower()
    t = ALIASES.get(t, t)
    if t not in TYPES:
        raise SanError("unknown storage type %r (use one of: %s)" % (t, ", ".join(TYPES)))
    return t


def arrays(cfg):
    """The configured arrays: [{name, type, controllers, port, username, password, verify_tls, enabled}]."""
    out = []
    for sec in cfg.parsers["main"].sections() if "main" in getattr(cfg, "parsers", {}) else []:
        if not sec.startswith("san:"):
            continue
        s = cfg.section("main", sec)
        name = sec[4:]
        try:
            t = kind(s.get("type", "santricity"))
        except SanError:
            t = s.get("type", "")
        out.append({"name": name, "type": t, "controllers": as_list(s.get("controllers", "")), "port": int(s.get("port") or TYPES.get(t, ("", 443))[1]),
                    "username": s.get("username", ""), "password": cfg.secret(sec), "verify_tls": as_bool(s.get("verify_tls", "no"), False),
                    "enabled": as_bool(s.get("enabled", "yes"), True)})
    return out


# -- HTTP ----------------------------------------------------------------------------------------
def _ctx(verify):
    c = ssl.create_default_context()
    if not verify:                      # storage controllers usually carry a self-signed certificate
        c.check_hostname = False
        c.verify_mode = ssl.CERT_NONE
    return c


class Http(object):
    """GET and POST with JSON answers. post() also returns the response headers (some makes hand out the session token there)."""

    def __init__(self, timeout=TIMEOUT):
        self.timeout = timeout

    def call(self, method, url, headers=None, verify=False, body=None):
        data = None
        hdr = dict({"Accept": "application/json"}, **(headers or {}))
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            hdr.setdefault("Content-Type", "application/json")
        req = Request(url, data=data, headers=hdr, method=method)
        try:
            with urlopen(req, timeout=self.timeout, context=_ctx(verify)) as r:
                raw, rh = r.read(), dict((k.lower(), v) for k, v in r.headers.items())
        except HTTPError as e:
            if e.code in (401, 403):
                raise SanError("the storage refused the user name or password (HTTP %d)" % e.code)
            raise SanError("HTTP %d from %s" % (e.code, url.split("/")[2]))
        except (URLError, socket.timeout, ConnectionError, OSError) as e:
            raise SanError("cannot reach %s: %s" % (url.split("/")[2], getattr(e, "reason", e)))
        try:
            return json.loads(raw.decode("utf-8", "replace") or "null"), rh
        except ValueError:
            raise SanError("the answer from %s is not JSON (is the storage type right?)" % url.split("/")[2])

    def get(self, url, headers=None, verify=False):
        return self.call("GET", url, headers, verify)[0]

    def post(self, url, headers=None, verify=False, body=None):
        return self.call("POST", url, headers, verify, body if body is not None else {})


def basic(user, password):
    return {"Authorization": "Basic " + base64.b64encode(("%s:%s" % (user, password)).encode("utf-8")).decode("ascii")}


def tolerant(fn, default):
    """The first call must work (it proves the address, type and login); after that a list the firmware does not offer is skipped."""
    state = {"first": True}

    def call(*a, **kw):
        if state["first"]:
            state["first"] = False
            return fn(*a, **kw)
        try:
            return fn(*a, **kw)
        except SanError as e:
            if "refused" in str(e):
                raise
            return default() if callable(default) else default
    return call


def reachable(host, port, timeout=8):
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True, ""
    except OSError as e:
        return False, str(e)


def _num(v, default=0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _pct(used, total):
    return round(100.0 * used / total, 1) if total else None


def empty(a):
    return {"type": a["type"], "name": a["name"], "system_name": "", "model": "", "serial": "", "firmware": "", "status": "unknown", "reached": "", "unreachable": [],
            "controllers": [], "drives": [], "pools": [], "volumes": [], "hardware": [], "failures": [], "events": [], "perf": {}}


# -- drivers -------------------------------------------------------------------------------------
def santricity(a, host, http):
    base = "https://%s:%d/devmgr/v2" % (host, a["port"])
    hdr = basic(a["username"], a["password"])
    g = tolerant(lambda path: http.get(base + path, hdr, a["verify_tls"]), None)
    systems = g("/storage-systems") or []
    sid = (systems[0].get("id") if systems and isinstance(systems, list) else None) or "1"
    p = "/storage-systems/%s" % sid
    s = g(p) or {}
    out = empty(a)
    out.update(name=s.get("name") or a["name"], model=s.get("model", ""), serial=s.get("chassisSerialNumber", ""), firmware=s.get("fwVersion", ""),
               status={"optimal": "optimal", "needsattn": "needs attention", "needsattention": "needs attention"}.get(
                   str(s.get("status", "")).lower(), str(s.get("status", "unknown")).lower()))
    for c in g(p + "/controllers") or []:
        loc = (c.get("physicalLocation") or {}).get("label") or c.get("id", "")
        out["controllers"].append({"name": "Controller %s" % loc, "status": str(c.get("status", "unknown")).lower(),
                                   "detail": "cache %s MB" % int(_num(c.get("cacheMemorySize"))) if c.get("cacheMemorySize") else ""})
    for d in g(p + "/drives") or []:
        loc = d.get("physicalLocation") or {}
        wear = (d.get("ssdWearLife") or {}).get("percentEnduranceUsed")
        st = str(d.get("status", "unknown")).lower()
        out["drives"].append({"slot": "tray %s slot %s" % ((loc.get("trayRef") or "")[-2:] or "?", loc.get("slot", "?")),
                              "status": st, "media": str(d.get("driveMediaType", "")).upper(), "capacity": _num(d.get("rawCapacity")),
                              "hot_spare": bool(d.get("hotSpare")), "predictive_failure": bool(d.get("pfa")), "wear_percent": _num(wear, None) if wear is not None else None})
    for v in g(p + "/storage-pools") or []:
        total, used = _num(v.get("totalRaidedSpace")), _num(v.get("usedSpace"))
        out["pools"].append({"name": v.get("label") or v.get("name", "?"), "raid": str(v.get("raidLevel", "")).replace("raid", "RAID ").replace("raidDiskPool", "DDP"),
                             "capacity": total, "used": used, "free": _num(v.get("freeSpace")) or max(0, total - used), "used_percent": _pct(used, total),
                             "status": str(v.get("raidStatus") or v.get("state") or "unknown").lower()})
    pools = dict((v.get("volumeGroupRef") or v.get("id"), v.get("label")) for v in (g(p + "/storage-pools") or []))
    for v in g(p + "/volumes") or []:
        maps = v.get("listOfMappings") or []
        out["volumes"].append({"name": v.get("label") or v.get("name", "?"), "pool": pools.get(v.get("volumeGroupRef"), ""), "capacity": _num(v.get("capacity")),
                               "status": str(v.get("status", "unknown")).lower(), "mapped": bool(v.get("mapped") or maps),
                               "hosts": len(maps)})
    hw = g(p + "/hardware-inventory") or {}
    for key, label in (("fans", "Fan"), ("powerSupplies", "Power supply"), ("batteries", "Battery"), ("thermalSensors", "Temperature sensor")):
        for i, x in enumerate(hw.get(key) or [], 1):
            out["hardware"].append({"kind": label, "name": "%s %d" % (label, i), "status": str(x.get("status", "unknown")).lower()})
    for f in g(p + "/failures") or []:
        out["failures"].append(str(f.get("failureType") or f.get("objectType") or f))
    try:
        for e in (g(p + "/mel-events?count=50&critical=true") or [])[:20]:
            out["events"].append({"time": e.get("timeStamp", ""), "severity": str(e.get("priority", "")).lower(), "text": e.get("description", "")})
    except SanError:
        pass
    try:
        st = g(p + "/analysed-system-statistics") or {}
        st = st[0] if isinstance(st, list) and st else st
        out["perf"] = {"read_iops": _num(st.get("readIOps")), "write_iops": _num(st.get("writeIOps")), "read_mbps": _num(st.get("readThroughput")),
                       "write_mbps": _num(st.get("writeThroughput")), "latency_ms": max(_num(st.get("readResponseTime")), _num(st.get("writeResponseTime")))}
    except SanError:
        pass
    return out


def msa(a, host, http):
    """HPE MSA, Dell PowerVault ME4/ME5, Lenovo ThinkSystem DS: log in with sha256('user_password'), then 'show' commands as JSON."""
    base = "https://%s:%d/api" % (host, a["port"])
    token = hashlib.sha256(("%s_%s" % (a["username"], a["password"])).encode("utf-8")).hexdigest()
    login = http.get(base + "/login/" + token, {"datatype": "json"}, a["verify_tls"]) or {}
    st = (login.get("status") or [{}])[0]
    key = st.get("response", "")
    if st.get("return-code") not in (1, "1") or not key:
        raise SanError("the storage refused the user name or password")
    hdr = {"sessionKey": key, "datatype": "json"}
    g = tolerant(lambda cmd: http.get(base + "/show/" + cmd, hdr, a["verify_tls"]) or {}, dict)
    blocks = lambda v: _num(v) * 512.0
    out = empty(a)
    s = (g("system").get("system") or [{}])[0]
    out.update(name=s.get("system-name") or a["name"], model=s.get("product-id", ""), serial=s.get("midplane-serial-number", ""),
               status="optimal" if str(s.get("health", "")).upper() == "OK" else str(s.get("health", "unknown")).lower())
    for c in g("controllers").get("controllers") or []:
        out["firmware"] = out["firmware"] or c.get("sc-fw", "")
        out["controllers"].append({"name": "Controller %s" % c.get("controller-id", "?"), "status": "optimal" if str(c.get("health", "")).upper() == "OK" else str(c.get("health", "unknown")).lower(),
                                   "detail": c.get("status", "")})
    dk = g("disks")
    for d in dk.get("drives") or dk.get("disks") or []:
        life = d.get("ssd-life-left-numeric")
        out["drives"].append({"slot": d.get("location", "?"), "status": "optimal" if str(d.get("health", "")).upper() == "OK" else str(d.get("health", "unknown")).lower(),
                              "media": d.get("description", ""), "capacity": blocks(d.get("size-numeric")), "hot_spare": "SPARE" in str(d.get("usage", "")).upper(),
                              "predictive_failure": False, "wear_percent": (100 - _num(life)) if life not in (None, "", 255, "255") else None})
    for p in g("pools").get("pools") or []:
        total, free = blocks(p.get("total-size-numeric")), blocks(p.get("total-avail-numeric"))
        out["pools"].append({"name": p.get("name", "?"), "raid": p.get("storage-type", ""), "capacity": total, "used": total - free, "free": free,
                             "used_percent": _pct(total - free, total), "status": "optimal" if str(p.get("health", "")).upper() == "OK" else str(p.get("health", "unknown")).lower()})
    for v in g("volumes").get("volumes") or []:
        out["volumes"].append({"name": v.get("volume-name", "?"), "pool": v.get("storage-pool-name", ""), "capacity": blocks(v.get("size-numeric")),
                               "status": "optimal" if str(v.get("health", "")).upper() == "OK" else str(v.get("health", "unknown")).lower(), "mapped": None, "hosts": None})
    for cmd, key, label in (("fans", "fan", "Fan"), ("power-supplies", "power-supplies", "Power supply")):
        try:
            parts = g(cmd).get(key) or []
        except SanError:                                     # an optional list the firmware does not offer
            parts = []
        for x in parts:
            out["hardware"].append({"kind": label, "name": x.get("name") or x.get("durable-id", label), "status": "optimal" if str(x.get("health", "")).upper() == "OK" else str(x.get("health", "unknown")).lower()})
    try:
        cs = g("controller-statistics").get("controller-statistics") or []
        if cs:
            out["perf"] = {"read_iops": sum(_num(x.get("iops")) * 0.5 for x in cs), "write_iops": sum(_num(x.get("iops")) * 0.5 for x in cs),
                           "read_mbps": sum(_num(x.get("bytes-per-second-numeric")) for x in cs) / 1048576.0 * 0.5,
                           "write_mbps": sum(_num(x.get("bytes-per-second-numeric")) for x in cs) / 1048576.0 * 0.5, "latency_ms": None}
    except SanError:
        pass
    try:
        for e in (g("events/last/30").get("events") or [])[:20]:
            sev = str(e.get("severity", "")).lower()
            if sev in ("critical", "error", "warning"):
                out["events"].append({"time": e.get("time-stamp", ""), "severity": sev, "text": e.get("message", "")})
    except SanError:
        pass
    return out


def ontap(a, host, http):
    base = "https://%s:%d/api" % (host, a["port"])
    hdr = basic(a["username"], a["password"])
    g = tolerant(lambda path: http.get(base + path, hdr, a["verify_tls"]) or {}, dict)
    out = empty(a)
    c = g("/cluster?fields=name,version")
    out.update(name=c.get("name") or a["name"], firmware=(c.get("version") or {}).get("full", ""), status="optimal")
    for n in g("/cluster/nodes?fields=name,state,model,serial_number").get("records") or []:
        out["model"] = out["model"] or n.get("model", "")
        out["serial"] = out["serial"] or n.get("serial_number", "")
        out["controllers"].append({"name": n.get("name", "?"), "status": "optimal" if n.get("state") == "up" else str(n.get("state", "unknown")), "detail": n.get("model", "")})
    for d in g("/storage/disks?fields=name,state,container_type,usable_size,type").get("records") or []:
        st = str(d.get("state", "")).lower()
        out["drives"].append({"slot": d.get("name", "?"), "status": "optimal" if st in ("present", "spare") else ("failed" if st in ("broken", "failed") else st),
                              "media": str(d.get("type", "")).upper(), "capacity": _num(d.get("usable_size")), "hot_spare": d.get("container_type") == "spare",
                              "predictive_failure": False, "wear_percent": None})
    for p in g("/storage/aggregates?fields=name,state,space.block_storage.size,space.block_storage.available,block_storage.primary.raid_type").get("records") or []:
        sp = (p.get("space") or {}).get("block_storage") or {}
        total, free = _num(sp.get("size")), _num(sp.get("available"))
        out["pools"].append({"name": p.get("name", "?"), "raid": (((p.get("block_storage") or {}).get("primary") or {}).get("raid_type") or "").upper(),
                             "capacity": total, "used": total - free, "free": free, "used_percent": _pct(total - free, total),
                             "status": "optimal" if p.get("state") == "online" else str(p.get("state", "unknown"))})
    for v in g("/storage/volumes?fields=name,state,space.size,svm.name,aggregates").get("records") or []:
        out["volumes"].append({"name": v.get("name", "?"), "pool": ",".join(x.get("name", "") for x in v.get("aggregates") or []),
                               "capacity": _num((v.get("space") or {}).get("size")), "status": "optimal" if v.get("state") == "online" else str(v.get("state", "unknown")),
                               "mapped": None, "hosts": None})
    try:
        for e in (g("/support/ems/events?max_records=20&message.severity=emergency|alert|error&order_by=time%20desc&fields=time,message,log_message").get("records") or []):
            out["events"].append({"time": e.get("time", ""), "severity": ((e.get("message") or {}).get("severity") or "error"), "text": e.get("log_message", "")})
    except SanError:
        pass
    try:
        m = (g("/cluster/metrics?interval=1h&fields=iops,throughput,latency&max_records=1").get("records") or [{}])[0]
        out["perf"] = {"read_iops": _num((m.get("iops") or {}).get("read")), "write_iops": _num((m.get("iops") or {}).get("write")),
                       "read_mbps": _num((m.get("throughput") or {}).get("read")) / 1048576.0, "write_mbps": _num((m.get("throughput") or {}).get("write")) / 1048576.0,
                       "latency_ms": _num((m.get("latency") or {}).get("total")) / 1000.0}
    except SanError:
        pass
    if any(d["status"] == "failed" for d in out["drives"]) or any(x["status"] != "optimal" for x in out["controllers"]):
        out["status"] = "needs attention"
    return out


def santricity_perf(a, host, http):
    """I/O now: system totals and the busiest volumes (two light requests)."""
    base = "https://%s:%d/devmgr/v2/storage-systems/1" % (host, a["port"])
    hdr = basic(a["username"], a["password"])
    st = http.get(base + "/analysed-system-statistics", hdr, a["verify_tls"]) or {}
    st = st[0] if isinstance(st, list) and st else st
    out = {"read_iops": _num(st.get("readIOps")), "write_iops": _num(st.get("writeIOps")), "read_mbps": _num(st.get("readThroughput")),
           "write_mbps": _num(st.get("writeThroughput")), "latency_ms": max(_num(st.get("readResponseTime")), _num(st.get("writeResponseTime"))), "volumes": []}
    try:
        vols = http.get(base + "/analysed-volume-statistics", hdr, a["verify_tls"]) or []
        for v in vols:
            out["volumes"].append({"name": v.get("volumeName") or v.get("volumeId", "?"), "iops": _num(v.get("readIOps")) + _num(v.get("writeIOps")),
                                   "mbps": _num(v.get("readThroughput")) + _num(v.get("writeThroughput")),
                                   "ms": max(_num(v.get("readResponseTime")), _num(v.get("writeResponseTime")))})
        out["volumes"] = sorted(out["volumes"], key=lambda v: -v["iops"])[:5]
    except SanError:
        pass
    return out


PERF = {"santricity": santricity_perf}


def perf(a, http=None, prefer=""):
    """I/O now for the live graphs, or None when this make has no light reading (its I/O then comes with the full check)."""
    fn = PERF.get(kind(a["type"]))
    if not fn:
        return None
    hosts = ([prefer] if prefer in a["controllers"] else []) + [h for h in a["controllers"] if h != prefer]
    last = None
    for host in hosts:
        try:
            return fn(a, host, http or Http(timeout=10))
        except SanError as e:
            last = e
            if "refused" in str(e):
                break
    raise last or SanError("no controller answered")


DRIVERS = {"santricity": santricity, "msa": msa, "ontap": ontap}
from lshme.ops import san_drivers as _more  # noqa: E402  (the drivers below use the helpers above)
DRIVERS.update(unity=_more.unity, powerstore=_more.powerstore, primera=_more.primera, pure=_more.pure, svc=_more.svc, oceanstor=_more.oceanstor)


def collect(a, http=None, reach=reachable):
    """Ask controller A, then B. A controller that does not answer is reported; the array is unreachable only when none answers."""
    if not a["controllers"]:
        raise SanError("no controller address configured")
    t = kind(a["type"])
    if t == "reach":
        out = empty(a)
        for i, host in enumerate(a["controllers"]):
            ok, why = reach(host, a["port"])
            out["controllers"].append({"name": "Controller %s" % "AB"[i] if i < 2 else host, "status": "optimal" if ok else "unreachable", "detail": host if ok else "%s: %s" % (host, why)})
            if ok:
                out["reached"] = out["reached"] or host
            else:
                out["unreachable"].append("%s (%s)" % (host, why))
        if not out["reached"]:
            raise SanError("no controller answers on port %d: %s" % (a["port"], "; ".join(out["unreachable"])))
        out["status"] = "optimal" if not out["unreachable"] else "needs attention"
        return out
    errors, last_auth = [], None
    for host in a["controllers"]:
        try:
            out = DRIVERS[t](a, host, http or Http())
            out["system_name"] = out.get("name") if out.get("name") != a["name"] else ""
            out["name"] = a["name"]                              # the name you gave it is the key for history, alerts and reports
            out["reached"] = host
            for other in a["controllers"]:                       # say when the other controller's management port is down
                if other != host:
                    ok, why = reach(other, a["port"])
                    if not ok:
                        out["unreachable"].append("%s (%s)" % (other, why))
            return out
        except SanError as e:
            if "refused the user name" in str(e):
                last_auth = e
                break                                          # the other controller would refuse the same login
            errors.append("%s: %s" % (host, e))
    if last_auth:
        raise last_auth
    raise SanError("; ".join(errors))


def test(a, http=None, reach=reachable):
    """For the Test button: {ok, message, summary}."""
    t0 = time.time()
    try:
        out = collect(a, http, reach)
    except SanError as e:
        return {"ok": False, "message": str(e)}
    return {"ok": True, "message": "Connected to %s in %.1f s: %s%s, %d controller(s), %d drive(s), %d pool(s), %d volume(s), status %s" % (
        out["reached"], time.time() - t0, out["model"] or TYPES[kind(a["type"])][0], (" firmware " + out["firmware"]) if out["firmware"] else "",
        len(out["controllers"]), len(out["drives"]), len(out["pools"]), len(out["volumes"]), out["status"]),
        "summary": {"model": out["model"], "firmware": out["firmware"], "status": out["status"]}}


# -- adding and removing arrays (terminal and web) ---------------------------------------------------
def safe_name(name):
    n = "".join(ch if ch.isalnum() or ch in "-_." else "-" for ch in (name or "").strip())[:40].strip("-")
    if not n:
        raise SanError("give the storage a short name, e.g. hhk-de")
    return n


def save(app, name, type_, controllers, username, password=None, port=None, verify_tls=False, enabled=True):
    """Add or change one array. An empty password keeps the saved one."""
    name = safe_name(name)
    t = kind(type_)
    ctl = [c.strip() for c in (controllers if isinstance(controllers, (list, tuple)) else as_list(controllers)) if c and c.strip()]
    if not ctl or len(ctl) > 4:
        raise SanError("give the management address of controller A (and B)")
    for c in ctl:
        if not all(ch.isalnum() or ch in ".-:[]" for ch in c):
            raise SanError("not an address: %r" % c)
    if t not in ("reach", "pure") and not username:
        raise SanError("a read-only user name is needed (for example monitor)")
    sec = "san:" + name
    port = int(port or TYPES[t][1])
    if not 1 <= port <= 65535:
        raise SanError("port must be 1-65535")
    app.cfg.set("main", sec, "type", t)
    app.cfg.set("main", sec, "controllers", ", ".join(ctl))
    app.cfg.set("main", sec, "port", str(port))
    app.cfg.set("main", sec, "username", username or "")
    app.cfg.set("main", sec, "verify_tls", "yes" if verify_tls else "no")
    app.cfg.set("main", sec, "enabled", "yes" if enabled else "no")
    app.save_config("main")
    if password:
        app.cfg.set("credentials", sec, "password", password)
        app.save_config("credentials")
    app.reload()
    return name


def remove(app, name):
    sec = "san:" + safe_name(name)
    app.cfg.remove_section("main", sec)
    app.save_config("main")
    try:
        app.cfg.remove_section("credentials", sec)
        app.save_config("credentials")
    except Exception:
        pass
    app.reload()


def find(cfg, name):
    for a in arrays(cfg):
        if a["name"] == name:
            return a
    names = [a["name"] for a in arrays(cfg)]
    raise SanError("no SAN storage named %r (%s)" % (name, ("added: " + ", ".join(names)) if names else "none added yet: vitals san add"))


def public(a):
    """An array without its password, for the web page."""
    out = dict((k, v) for k, v in a.items() if k != "password")
    out.update(has_password=bool(a.get("password")), type_label=TYPES.get(a["type"], (a["type"],))[0])
    return out
