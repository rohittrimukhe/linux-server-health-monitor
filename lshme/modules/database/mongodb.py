"""MongoDB adapter: availability, connections, replica set member health, sizes."""
import json

from lshme.core.util import fmt_bytes, pct, severity_for, to_int

SCRIPT = r"""
var out = {};
try { var s = db.adminCommand({serverStatus: 1}); out.version = s.version; out.uptime = s.uptime;
      out.conn = s.connections; out.mem = s.mem; } catch (e) { out.err = String(e); }
try { var r = db.adminCommand({replSetGetStatus: 1}); if (r.ok) { out.rs = {set: r.set, members: r.members.map(function (m) {
      return {name: m.name, state: m.stateStr, health: m.health, lag: (r.members.filter(function (p) { return p.stateStr == 'PRIMARY'; })[0] || {optimeDate: m.optimeDate}).optimeDate - m.optimeDate}; })}; } } catch (e) {}
try { out.dbs = db.adminCommand({listDatabases: 1}).databases.map(function (d) { return [d.name, d.sizeOnDisk]; }); } catch (e) {}
print(JSON.stringify(out));
"""


def check(ctx, res, inst):
    label = "mongodb:%s" % inst["name"]
    info = {"instance": inst["name"], "engine": "mongodb"}
    shell = "mongosh" if ctx.has("mongosh") else "mongo"
    uri = inst.get("uri", "mongodb://127.0.0.1:27017/admin")
    env = {}
    cmd = [shell, "--quiet", uri]
    pw = ctx.cfg.secret("mongodb:" + inst["name"])
    if inst.get("username"):
        cmd += ["-u", inst["username"], "--authenticationDatabase", inst.get("auth_db", "admin")]
        if pw:
            cmd += ["-p", pw]  # mongosh has no env var for passwords; prefer x509/keyfile auth in production
    r = ctx.run(cmd + ["--eval", SCRIPT], timeout=60, env=env)
    data = None
    for line in reversed(r.lines()):
        if line.startswith("{"):
            try:
                data = json.loads(line)
                break
            except ValueError:
                pass
    if not data or data.get("err"):
        res.add("CRITICAL", "MONGODB_UNAVAILABLE", label, "MongoDB %s is not reachable" % inst["name"],
                evidence=(data or {}).get("err") or r.brief_error(), recommendation="Check mongod status and credentials.")
        return info
    conn = data.get("conn", {})
    cur, avail = to_int(conn.get("current"), 0), to_int(conn.get("available"), 0)
    cpct = pct(cur, cur + avail)
    info.update(data)
    res.metric("mongodb.%s.connections_percent" % inst["name"], cpct)
    dbs = data.get("dbs", [])
    res.table("MongoDB %s — overview" % inst["name"], ["Item", "Value"], [
        ["Version", data.get("version", "")], ["Uptime", "%.1f days" % (to_int(data.get("uptime"), 0) / 86400.0)],
        ["Connections", "%d (%.1f%% of limit)" % (cur, cpct or 0)],
        ["Replica set", data.get("rs", {}).get("set", "standalone")],
        ["Total size", fmt_bytes(sum(to_int(d[1], 0) for d in dbs))],
    ], kind="summary")
    sev = severity_for(cpct, ctx.t("mongodb", "connections_warning", 80), ctx.t("mongodb", "connections_critical", 90))
    if sev:
        res.add(sev, "MONGODB_CONNECTIONS", label, "MongoDB connections at %.0f%% of limit" % cpct)
    members = data.get("rs", {}).get("members", [])
    if members:
        res.table("MongoDB %s — replica set members" % inst["name"], ["Member", "State", "Health", "Lag ms"],
                  [[m["name"], m["state"], m["health"], m.get("lag", "")] for m in members])
        for m in members:
            if m.get("health") != 1 or m.get("state") not in ("PRIMARY", "SECONDARY", "ARBITER"):
                res.add("CRITICAL", "MONGODB_MEMBER_UNHEALTHY", label, "Replica member %s is %s" % (m["name"], m["state"]))
        if not any(m.get("state") == "PRIMARY" for m in members):
            res.add("CRITICAL", "MONGODB_NO_PRIMARY", label, "Replica set has no PRIMARY")
    return info
