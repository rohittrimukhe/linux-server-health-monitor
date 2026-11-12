"""Interfaces, routing, DNS, bonding, link state, errors, reachability and firewall."""
import os
import re
import socket
import time

from lshme.core.util import pct, to_float, to_int

NAME = "network"
TITLE = "Network & Connectivity"
WEIGHT = 10


def interfaces(ctx):
    out = {}
    for name in ctx.runner.listdir("/sys/class/net"):
        base = "/sys/class/net/" + name
        st = {}
        for k in ("rx_packets", "tx_packets", "rx_errors", "tx_errors", "rx_dropped", "tx_dropped", "rx_bytes", "tx_bytes"):
            st[k] = to_int(ctx.read(base + "/statistics/" + k, "0"), 0)
        out[name] = {"name": name, "operstate": ctx.read(base + "/operstate", "unknown"),
                     "speed": to_int(ctx.read(base + "/speed", ""), None), "duplex": ctx.read(base + "/duplex", ""),
                     "mtu": to_int(ctx.read(base + "/mtu", ""), None), "mac": ctx.read(base + "/address", ""),
                     "virtual": not ctx.runner.exists(base + "/device"), "addresses": [], "stats": st,
                     "master": os.path.basename(os.path.realpath(base + "/master")) if ctx.runner.exists(base + "/master") else ""}
    r = ctx.run(["ip", "-o", "addr", "show"])
    for line in r.lines():
        p = line.split()
        if len(p) >= 4 and p[1].rstrip(":") in out and p[2] in ("inet", "inet6"):
            out[p[1].rstrip(":")]["addresses"].append(p[3])
    return out


def routes(ctx):
    r = ctx.run(["ip", "route", "show"])
    default = [l for l in r.lines() if l.startswith("default")]
    gw = None
    if default:
        m = re.search(r"via (\S+)", default[0])
        gw = m.group(1) if m else None
    if not r.ok:  # no iproute2: fall back to /proc/net/route
        for line in ctx.read("/proc/net/route").splitlines()[1:]:
            p = line.split()
            if len(p) > 2 and p[1] == "00000000":
                g = p[2]
                gw = ".".join(str(int(g[i:i + 2], 16)) for i in (6, 4, 2, 0))
                default = ["default via %s dev %s" % (gw, p[0])]
    return {"table": r.out, "default": default, "gateway": gw}


def bonds(ctx):
    out = []
    for name in ctx.runner.listdir("/proc/net/bonding"):
        text = ctx.read("/proc/net/bonding/" + name)
        mode = re.search(r"Bonding Mode:\s*(.+)", text)
        slaves = re.findall(r"Slave Interface:\s*(\S+)\s*\n\s*MII Status:\s*(\S+)", text)
        out.append({"bond": name, "mode": mode.group(1).strip() if mode else "",
                    "mii": (re.search(r"MII Status:\s*(\S+)", text) or [None, ""])[1],
                    "slaves": [{"name": s, "mii": st} for s, st in slaves]})
    for team in ctx.runner.glob("/sys/class/net/*/team"):  # teamd: report via teamdctl when present
        name = team.split("/")[4]
        r = ctx.run(["teamdctl", name, "state"])
        if r.ok:
            ports = re.findall(r"^\s+(\S+)\n(?:.*\n)*?\s+link:\s*(\w+)", r.out, re.M)
            out.append({"bond": name, "mode": "team", "mii": "up", "slaves": [{"name": p, "mii": s} for p, s in ports]})
    return out


def ping(ctx, host, count=3):
    r = ctx.run(["ping", "-c", str(count), "-W", "2", "-q", host], timeout=count * 3 + 5)
    loss = re.search(r"(\d+(?:\.\d+)?)% packet loss", r.out)
    rtt = re.search(r"= [\d.]+/([\d.]+)/", r.out)
    return {"target": host, "reachable": r.rc == 0, "loss": to_float(loss.group(1)) if loss else (None if r.missing else 100.0),
            "avg_ms": to_float(rtt.group(1)) if rtt else None, "error": "" if r.ok else r.brief_error(),
            "available": not r.missing}


def tcp_check(host, port, timeout=5):
    start = time.time()
    try:
        s = socket.create_connection((host, int(port)), timeout=timeout)
        s.close()
        return True, round((time.time() - start) * 1000, 1), ""
    except Exception as e:
        return False, None, str(e)


def dns_check(ctx, name):
    r = ctx.run(["getent", "hosts", name], timeout=10)
    return r.ok and bool(r.out.strip()), r.out.split()[0] if r.out.split() else r.brief_error()


def firewall(ctx):
    if ctx.has("firewall-cmd"):
        r = ctx.run(["firewall-cmd", "--state"])
        zones = ctx.run(["firewall-cmd", "--get-active-zones"]).out if r.ok else ""
        return {"tool": "firewalld", "active": r.out.strip() == "running", "detail": zones}
    if ctx.has("ufw"):
        r = ctx.run(["ufw", "status"])
        return {"tool": "ufw", "active": "Status: active" in r.out, "detail": r.out[:2000]}
    if ctx.has("nft"):
        r = ctx.run(["nft", "list", "ruleset"])
        if r.ok:
            return {"tool": "nftables", "active": bool(r.out.strip()), "detail": "%d lines" % len(r.out.splitlines())}
    if ctx.has("iptables"):
        r = ctx.run(["iptables", "-S"])
        rules = [l for l in r.lines() if not l.startswith("-P")]
        return {"tool": "iptables", "active": bool(rules), "detail": "%d rules" % len(rules)}
    return {"tool": "none", "active": False, "detail": "no firewall tool found"}


def run(ctx, res):
    ifs = interfaces(ctx)
    rt = routes(ctx)
    bd = bonds(ctx)
    fw = firewall(ctx)
    ctx.shared["firewall"] = fw
    resolv = ctx.read("/etc/resolv.conf")
    nameservers = re.findall(r"^nameserver\s+(\S+)", resolv, re.M)
    conf = lambda k: ctx.cfg.getlist("main", "network", k, "")
    sockstat = ctx.read("/proc/net/sockstat")
    tcp_in_use = re.search(r"TCP: inuse (\d+).*?tw (\d+)", sockstat)

    rows = []
    err_ratio_warn = ctx.t("network", "error_ratio_warning", 0.1)
    expected = set(conf("expected_interfaces"))
    for i in sorted(ifs.values(), key=lambda x: x["name"]):
        if i["name"] == "lo":
            continue
        st = i["stats"]
        pkts = st["rx_packets"] + st["tx_packets"]
        errs = st["rx_errors"] + st["tx_errors"]
        drops = st["rx_dropped"] + st["tx_dropped"]
        ratio = pct(errs, pkts) if pkts else 0.0
        rows.append([i["name"], i["operstate"], ", ".join(i["addresses"]) or "-", i["mac"],
                     "%s Mb/s" % i["speed"] if i["speed"] and i["speed"] > 0 else "-", i["duplex"] or "-",
                     i["mtu"], errs, drops, i["master"] or ""])
        if (i["name"] in expected or (i["addresses"] and not i["virtual"])) and i["operstate"] == "down":
            res.add("CRITICAL" if i["name"] in expected else "WARNING", "INTERFACE_DOWN", i["name"],
                    "Interface %s is down" % i["name"], recommendation="Check cabling/switch port and 'ip link' state.")
        if ratio and ratio >= err_ratio_warn:
            res.add("WARNING", "INTERFACE_ERRORS", i["name"], "Interface %s error ratio %.3f%% (%d errors)" % (i["name"], ratio, errs),
                    recommendation="Errors usually mean cabling, SFP or duplex problems; check the switch port.")
        if not i["virtual"] and i["duplex"] == "half":
            res.add("WARNING", "HALF_DUPLEX", i["name"], "Interface %s runs half duplex" % i["name"],
                    recommendation="Fix speed/duplex negotiation on the NIC and switch.")
    for name in expected - set(ifs):
        res.add("CRITICAL", "INTERFACE_MISSING", name, "Expected interface %s does not exist" % name)
    res.table("Network interfaces", ["Interface", "State", "Addresses", "MAC", "Speed", "Duplex", "MTU", "Errors", "Drops", "Master"],
              rows, kind="summary")

    if not rt["default"]:
        res.add("CRITICAL", "NO_DEFAULT_ROUTE", "routing", "No default route is configured",
                recommendation="Configure the default gateway; off-subnet traffic will fail.")
    res.table("Routing", ["Route"], [[l] for l in rt["table"].splitlines()])

    for b in bd:
        down = [s["name"] for s in b["slaves"] if s["mii"].lower() != "up"]
        if b["mii"].lower() != "up" or (b["slaves"] and len(down) == len(b["slaves"])):
            res.add("CRITICAL", "BOND_DOWN", b["bond"], "Bond %s is down" % b["bond"])
        elif down:
            res.add("WARNING", "BOND_SLAVE_DOWN", b["bond"], "Bond %s has down members: %s" % (b["bond"], ", ".join(down)),
                    recommendation="Redundancy is reduced; check the failed member link.")
    if bd:
        res.table("Bonding / teaming", ["Bond", "Mode", "Status", "Members"],
                  [[b["bond"], b["mode"], b["mii"], ", ".join("%s(%s)" % (s["name"], s["mii"]) for s in b["slaves"])] for b in bd])

    # reachability
    reach = []
    if rt["gateway"] and ctx.cfg.getbool("main", "network", "ping_gateway", True):
        p = ping(ctx, rt["gateway"])
        if p["available"]:
            reach.append(["gateway " + rt["gateway"], "ping", "ok" if p["reachable"] else "FAILED", p["loss"], p["avg_ms"]])
            if not p["reachable"]:
                res.add("CRITICAL", "GATEWAY_UNREACHABLE", rt["gateway"], "Default gateway %s does not answer ping" % rt["gateway"],
                        evidence=p["error"], recommendation="Some gateways block ICMP; otherwise check network connectivity.")
    lw, lc = ctx.t("network", "packet_loss_warning", 10), ctx.t("network", "packet_loss_critical", 50)
    lat = ctx.t("network", "latency_warning_ms", 100)
    for target in conf("ping_targets"):
        p = ping(ctx, target, 5)
        reach.append([target, "ping", "ok" if p["reachable"] else "FAILED", p["loss"], p["avg_ms"]])
        if p["loss"] is not None and p["loss"] >= lc:
            res.add("CRITICAL", "PACKET_LOSS", target, "%.0f%% packet loss to %s" % (p["loss"], target), evidence=p["error"])
        elif p["loss"] is not None and p["loss"] >= lw:
            res.add("WARNING", "PACKET_LOSS", target, "%.0f%% packet loss to %s" % (p["loss"], target))
        if p["avg_ms"] and p["avg_ms"] >= lat:
            res.add("WARNING", "HIGH_LATENCY", target, "Average latency to %s is %.1f ms" % (target, p["avg_ms"]))
    for spec in conf("tcp_checks"):
        if ":" not in spec:
            continue
        host, port = spec.rsplit(":", 1)
        ok, ms, err = tcp_check(host, port)
        reach.append([spec, "tcp", "ok" if ok else "FAILED", "", ms])
        if not ok:
            res.add("CRITICAL", "TCP_CHECK_FAILED", spec, "Cannot connect to %s" % spec, evidence=err,
                    recommendation="Check the remote service, firewalls and routing.")
    names = conf("dns_test_names") or []
    for name in names:
        ok, detail = dns_check(ctx, name)
        reach.append([name, "dns", "ok" if ok else "FAILED", "", detail])
        if not ok:
            res.add("WARNING", "DNS_FAILURE", name, "DNS resolution of %s failed" % name, evidence=detail,
                    recommendation="Check /etc/resolv.conf nameservers (%s)." % ", ".join(nameservers))
    if not nameservers:
        res.add("WARNING", "NO_NAMESERVER", "dns", "No nameserver configured in /etc/resolv.conf")
    if reach:
        res.table("Connectivity checks", ["Target", "Type", "Result", "Loss %", "Latency ms / detail"], reach)

    res.table("Network summary", ["Item", "Value"], [
        ["Default gateway", rt["gateway"] or "none"], ["DNS servers", ", ".join(nameservers) or "none"],
        ["Firewall", "%s (%s)" % (fw["tool"], "active" if fw["active"] else "inactive")],
        ["TCP sockets in use / time-wait", "%s / %s" % tcp_in_use.groups() if tcp_in_use else "N/A"],
    ], kind="summary")
    res.data = {"interfaces": ifs, "routes": rt, "bonds": bd, "firewall": fw, "nameservers": nameservers,
                "sockstat": sockstat}
    up = sum(1 for i in ifs.values() if i["operstate"] == "up")
    res.metric("network.interfaces_up", up)
    res.summary = "%d interfaces (%d up), gateway %s, firewall %s" % (
        len(ifs) - (1 if "lo" in ifs else 0), up, rt["gateway"] or "none", fw["tool"])
