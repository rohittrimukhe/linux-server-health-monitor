# Linux Server Health Monitor Enterprise
# Copyright (c) 2026 LRS Services Pvt Ltd, Mumbai. All rights reserved.
"""Network section of the daily, weekly and monthly reports: adapters and IP addresses, data sent and received,
bandwidth and link use over time, with the same hover charts as CPU and memory."""
import json
import os

from lshme.core.util import fmt_bytes
from lshme.reports import periodic as P
from lshme.reports.periodic import Bar, Cell, bucket, chip, day_key, stamp, table

SENT, RECV, TOTAL = "#16a34a", "#1d4ed8", "#f59e0b"       # green, blue, amber
MBIT = 8 / 1e6


def read(path, default=""):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read().strip()
    except OSError:
        return default


def usage(data):
    """{iface: {'rx': [(t, B/s)], 'tx': [...], 'rxmax': [...], 'txmax': [...]}} from the watcher, or {}."""
    out = {}
    if data.days <= 1 and data.paths:
        try:
            with open(os.path.join(data.paths.data_dir, "watch_metrics.json"), encoding="utf-8") as f:
                raw = [p for p in json.load(f) if p.get("t", 0) >= data.since and p.get("net")]
            for p in raw:
                for iface, (rx, tx) in p["net"].items():
                    d = out.setdefault(iface, {"rx": [], "tx": []})
                    d["rx"].append((p["t"], float(rx)))
                    d["tx"].append((p["t"], float(tx)))
            if out:
                for d in out.values():
                    d["rxmax"], d["txmax"] = bucket(d["rx"], 288, "max"), bucket(d["tx"], 288, "max")
                    d["step"] = 60.0
                    d["rxs"], d["txs"] = d["rx"], d["tx"]
                    d["rx"], d["tx"] = bucket(d["rx"], 288, "avg"), bucket(d["tx"], 288, "avg")
                return out
        except (IOError, OSError, ValueError, KeyError, TypeError):
            pass
    if data.hist:
        try:
            rows = data.hist.hourly_net(data.host, data.since)
        except Exception:
            rows = {}
        for iface, hrs in rows.items():
            mid = lambda t: min(t + 1800, data.now)
            out[iface] = {"rx": [(mid(t), a) for t, a, _, _, _ in hrs], "tx": [(mid(t), c) for t, _, _, c, _ in hrs],
                          "rxmax": [(mid(t), m) for t, _, m, _, _ in hrs], "txmax": [(mid(t), m) for t, _, _, _, m in hrs], "step": 3600.0}
            out[iface]["rxs"], out[iface]["txs"] = out[iface]["rx"], out[iface]["tx"]
    return out


def transferred(pts, step):
    """Bytes moved: rate (B/s) times the time each sample covers."""
    total, prev = 0.0, None
    for t, v in pts:
        dt = min(t - prev, step * 2) if prev is not None else step
        total += v * dt
        prev = t
    return total


def kind_of(name, base):
    """physical | bond | bridge | vm | vlan | virtual, from /sys. Only physical adapters carry the real traffic once."""
    if os.path.isdir(base + "/bonding"):
        return "bond"
    if os.path.isdir(base + "/bridge"):
        return "bridge"
    if os.path.exists(base + "/tun_flags") or name.startswith(("vnet", "tap")):
        return "vm"
    if os.path.exists(base + "/device"):
        return "physical"
    if "." in name or os.path.exists(base + "/../" + name + "/vlan_id"):
        return "vlan"
    return "virtual"


KIND_TEXT = {"physical": "Physical", "bond": "Bond", "bridge": "Bridge", "vm": "Virtual machine", "vlan": "VLAN", "virtual": "Virtual"}


def adapters(runner):
    """Interface details from /sys, /proc/net/dev and `ip`."""
    addrs = {}
    try:
        r = runner.run(["ip", "-o", "addr", "show"], timeout=10)
        for line in r.lines():
            p = line.split()
            if len(p) >= 4 and p[2] in ("inet", "inet6"):
                addrs.setdefault(p[1].rstrip(":"), []).append(p[3])
    except Exception:
        pass
    counters = {}
    for line in read("/proc/net/dev").splitlines()[2:]:
        name, _, rest = line.partition(":")
        f = rest.split()
        if len(f) >= 9:
            counters[name.strip()] = (int(f[0]), int(f[8]))
    out = []
    try:
        names = sorted(os.listdir("/sys/class/net"))
    except OSError:
        names = []
    for n in names:
        b = "/sys/class/net/" + n
        if n == "lo" or not os.path.isdir(b):          # bonding_masters is a control file, not an adapter
            continue
        speed = read(b + "/speed")
        master = os.path.basename(os.path.realpath(b + "/master")) if os.path.islink(b + "/master") else ""
        out.append({"name": n, "state": read(b + "/operstate"), "mac": read(b + "/address"), "mtu": read(b + "/mtu"),
                    "speed": int(speed) if speed.lstrip("-").isdigit() and int(speed) > 0 else None,
                    "ips": addrs.get(n, []), "rx_total": counters.get(n, (0, 0))[0], "tx_total": counters.get(n, (0, 0))[1],
                    "physical": os.path.exists(b + "/device"), "kind": kind_of(n, b), "master": master})
    return out


def counted(ad, traffic):
    """Names whose traffic is added up. The physical adapters carry the real traffic exactly once; bonds, bridges, VLANs and VM taps
    carry the same bytes again, so adding them would count everything two or three times."""
    phys = set(a["name"] for a in ad if a["kind"] == "physical" and a["name"] in traffic)
    if phys:
        return phys
    rest = set(a["name"] for a in ad if a["kind"] not in ("vm", "bridge", "bond") and a["name"] in traffic)
    return rest or set(traffic)


def merge_total(rx_pts, tx_pts):
    tx = dict(tx_pts)
    return [(t, v + tx.get(t, 0.0)) for t, v in rx_pts]


def series(rx, tx, total):
    f = lambda pts: [[int(t), round(v * MBIT, 3)] for t, v in pts]
    return [{"name": "Sent", "pts": f(tx)}, {"name": "Received", "pts": f(rx)}, {"name": "Total", "pts": f(total)}]


def chart(title, rx, tx, wide=False):
    total = merge_total(rx, tx)
    mx = max([v for _, v in total] or [0]) * MBIT
    avg = (sum(v for _, v in total) / len(total) * MBIT) if total else 0
    return {"title": title, "unit": " Mb/s", "color": RECV, "warn": None, "crit": None, "max": None, "min": 0, "multi": True,
            "colors": [SENT, RECV, TOTAL], "series": series(rx, tx, total), "info": "average %.2f Mb/s, peak %.2f Mb/s" % (avg, mx)}


def blocks(data, runner):
    """Returns (blocks, one-sentence summary or '')."""
    ad = adapters(runner)
    use = usage(data)
    if not ad and not use:
        return [P.note("No network interfaces were found.")], ""
    traffic = {}
    for name, u in use.items():
        step = u.get("step", 60.0)
        rx_b, tx_b = transferred(u["rxs"], step), transferred(u["txs"], step)
        rxs, txs = [v for _, v in u["rxmax"]] or [0], [v for _, v in u["txmax"]] or [0]
        ravg = sum(v for _, v in u["rx"]) / len(u["rx"]) if u["rx"] else 0
        tavg = sum(v for _, v in u["tx"]) / len(u["tx"]) if u["tx"] else 0
        traffic[name] = {"rx": rx_b, "tx": tx_b, "ravg": ravg, "tavg": tavg, "rpeak": max(rxs), "tpeak": max(txs)}
    out = []
    rows = []
    for a in sorted(ad, key=lambda x: -(x["rx_total"] + x["tx_total"])):
        st = a["state"]
        kind = KIND_TEXT.get(a["kind"], "Virtual") + ((" in " + a["master"]) if a["master"] else "")
        rows.append([Cell(a["name"], "b"), kind, chip("ok") if st == "up" else Cell(st or "unknown", "fl"), ", ".join(a["ips"]) or "-", a["mac"] or "-",
                     ("%d Mb/s" % a["speed"]) if a["speed"] else "-", a["mtu"] or "-", fmt_bytes(a["rx_total"]), fmt_bytes(a["tx_total"])])
    out.append(table("Network adapters", ["Adapter", "Type", "State", "IP address", "MAC address", "Link speed", "MTU", "Received since boot", "Sent since boot"], rows,
                     align=["", "", "", "", "", "r", "r", "r", "r"],
                     legend="The 'since boot' figures are the adapter's own counters; they restart at zero when the server restarts. A bond, bridge or "
                            "VM adapter shows the same traffic as the physical adapters under it, so totals count the physical adapters only."))
    if not traffic:
        out.append(P.note("Bandwidth graphs appear after the real-time watcher has been running for a while (it records traffic every minute)."))
        return out, ""
    speeds = dict((a["name"], a["speed"]) for a in ad)
    real = counted(ad, traffic)
    rows = []
    order = sorted(traffic, key=lambda k: -(traffic[k]["rx"] + traffic[k]["tx"]))
    for k in order:
        t = traffic[k]
        sp = speeds.get(k)
        peak = max(t["rpeak"], t["tpeak"]) * 8 / 1e6
        avg = max(t["ravg"], t["tavg"]) * 8 / 1e6
        link = Bar(min(100.0, 100.0 * avg / sp), 60, 85) if sp else Cell("-", "fl")
        peak_link = Cell("%.1f %%" % (100.0 * peak / sp), "cr" if peak / sp > .85 else "wn" if peak / sp > .6 else "ok") if sp else Cell("-", "fl")
        rows.append([Cell(k, "b"), fmt_bytes(t["rx"]), fmt_bytes(t["tx"]), Cell(fmt_bytes(t["rx"] + t["tx"]), "b"),
                     "%.2f / %.2f" % (t["ravg"] * MBIT, t["tavg"] * MBIT), "%.2f / %.2f" % (t["rpeak"] * MBIT, t["tpeak"] * MBIT), link, peak_link])
    per = "24 hours" if data.days <= 1 else "%d days" % data.days
    out.append(table("Data sent and received in the last %s" % per,
                     ["Adapter", "Received", "Sent", "Total", "Average Mb/s (in / out)", "Peak Mb/s (in / out)", "Average link use", "Peak link use"], rows,
                     align=["", "r", "r", "r", "r", "r", "", "r"],
                     legend="Mb/s = megabits per second. 'in' is data received, 'out' is data sent. Link use compares the busier direction with the adapter speed; "
                            "green is under 60 %, amber up to 85 %, red above."))
    out.append({"type": "wide_charts", "items": [chart("Network traffic on %s" % k, use[k]["rx"], use[k]["tx"]) for k in order[:4]]})
    # all adapters together
    tot_rx, tot_tx = {}, {}
    for k in [x for x in order if x in real]:
        for t, v in use[k]["rx"]:
            tot_rx[t] = tot_rx.get(t, 0.0) + v
        for t, v in use[k]["tx"]:
            tot_tx[t] = tot_tx.get(t, 0.0) + v
    rx_all, tx_all = sorted(tot_rx.items()), sorted(tot_tx.items())
    out.append({"type": "wide_charts", "items": [chart("Network traffic, all physical adapters together", rx_all, tx_all)]})
    # by day
    if data.days > 1:
        per_day = {}
        step = 3600.0
        for k in [x for x in order if x in real]:
            for (t, v), (_, w) in zip(use[k]["rxs"], use[k]["txs"]):
                d = per_day.setdefault(day_key(t), {"rx": 0.0, "tx": 0.0, "peak": 0.0})
                d["rx"] += v * step
                d["tx"] += w * step
        for k in [x for x in order if x in real]:
            for t, v in use[k]["rxmax"]:
                per_day.setdefault(day_key(t), {"rx": 0.0, "tx": 0.0, "peak": 0.0})["peak"] = max(per_day[day_key(t)]["peak"], v)
            for t, v in use[k]["txmax"]:
                per_day.setdefault(day_key(t), {"rx": 0.0, "tx": 0.0, "peak": 0.0})["peak"] = max(per_day[day_key(t)]["peak"], v)
        rows = []
        for d in data.dates():
            x = per_day.get(d)
            rows.append([Cell(d, "b"), fmt_bytes(x["rx"]), fmt_bytes(x["tx"]), Cell(fmt_bytes(x["rx"] + x["tx"]), "b"), "%.2f" % (x["peak"] * MBIT)] if x
                        else [Cell(d, "b")] + [Cell("-", "fl")] * 4)
        out.append(table("Network traffic by day (physical adapters)", ["Date", "Received", "Sent", "Total", "Peak Mb/s"], rows, align=["", "r", "r", "r", "r"]))
    grand_rx, grand_tx = sum(traffic[k]["rx"] for k in real), sum(traffic[k]["tx"] for k in real)
    top = [k for k in order if k in real][0]
    sentence = "The network moved %s in the last %s: %s received and %s sent. The busiest adapter was %s." % (
        fmt_bytes(grand_rx + grand_tx), per, fmt_bytes(grand_rx), fmt_bytes(grand_tx), top)
    return out, sentence
