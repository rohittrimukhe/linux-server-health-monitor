"""Database intelligence: discovers database engines on the host and runs the
read-only adapter for each configured or auto-discovered instance."""
import re

from lshme.modules.database import mongodb, mysql, oracle, postgresql, sqlserver

NAME = "database"
TITLE = "Databases"
WEIGHT = 15

ADAPTERS = {"oracle": oracle, "mysql": mysql, "postgresql": postgresql, "mongodb": mongodb, "sqlserver": sqlserver}
PROCESS_PATTERNS = {
    "oracle": r"(ora|db)_pmon_", "oracle-listener": r"tnslsnr", "mysql": r"\b(mysqld|mariadbd)\b",
    "postgresql": r"(postmaster|bin/postgres|postgres: .*checkpointer)", "mongodb": r"\bmongod\b",
    "sqlserver": r"sqlservr", "redis": r"redis-server", "db2": r"db2sysc", "sybase": r"dataserver",
}


def discover_processes(ctx):
    found = {}
    ps = ctx.run(["ps", "-eo", "user,pid,args"]).lines()[1:]
    for engine, rx in PROCESS_PATTERNS.items():
        hits = [l for l in ps if re.search(rx, l) and "lshme" not in l]
        if hits:
            found[engine] = hits[:10]
    return found


def configured(ctx):
    out = []
    for engine in ADAPTERS:
        for section in ctx.cfg.sections("database", engine + ":"):
            s = ctx.cfg.section("database", section)
            if s.get("enabled", "yes").lower() in ("no", "false", "0", "off"):
                continue
            s["name"] = section.split(":", 1)[1]
            s["engine"] = engine
            if engine == "oracle":
                s.setdefault("sid", s["name"])
            out.append(s)
    return out


def instances(ctx, procs):
    insts = configured(ctx)
    auto = lambda e: ctx.cfg.getbool("database", "discovery", e + "_autodiscover", True)
    if "oracle" in procs and auto("oracle"):
        known = set(i.get("sid") for i in insts if i["engine"] == "oracle")
        disc, _ = oracle.discover(ctx)
        for sid, d in disc.items():
            if sid not in known:
                d.update({"name": sid, "engine": "oracle", "auto": True})
                insts.append(d)
    if "postgresql" in procs and auto("postgresql") and not any(i["engine"] == "postgresql" for i in insts):
        user = ""
        for line in procs["postgresql"]:
            user = line.split()[0]
            break
        insts.append({"name": "local", "engine": "postgresql", "os_user": user if ctx.runner.is_root() else "", "auto": True})
    if "mysql" in procs and auto("mysql") and not any(i["engine"] == "mysql" for i in insts):
        insts.append({"name": "local", "engine": "mysql", "auto": True})
    if "mongodb" in procs and auto("mongodb") and not any(i["engine"] == "mongodb" for i in insts):
        insts.append({"name": "local", "engine": "mongodb", "auto": True})
    return insts


def run(ctx, res):
    procs = discover_processes(ctx)
    clients = dict((c, ctx.has(c)) for c in ("sqlplus", "mysql", "mariadb", "psql", "mongosh", "mongo", "sqlcmd", "lsnrctl"))
    insts = instances(ctx, procs)
    res.table("Database discovery", ["Engine", "Running processes", "Example"],
              [[e, len(h), h[0][:150]] for e, h in sorted(procs.items())] or [["none", 0, "no database processes found"]],
              kind="summary")
    if not procs and not insts:
        res.status = "SKIPPED"
        res.summary = "no database engines detected"
        res.skip("no database processes found and none configured")
        res.data = {"processes": procs, "clients": clients, "instances": []}
        return
    results = []
    monitored = set()
    for inst in insts:
        adapter = ADAPTERS[inst["engine"]]
        try:
            info = adapter.check(ctx, res, inst)
        except Exception as e:  # one bad adapter must not stop the others
            res.error("%s:%s adapter error: %s" % (inst["engine"], inst["name"], e))
            res.add("UNKNOWN", "DB_ADAPTER_ERROR", "%s:%s" % (inst["engine"], inst["name"]), "Check failed: %s" % e)
            info = {"instance": inst["name"], "engine": inst["engine"], "error": str(e)}
        info["auto_discovered"] = bool(inst.get("auto"))
        results.append(info)
        monitored.add(inst["engine"])
    for engine in procs:
        base = engine.split("-")[0]
        if base not in monitored and base in ("mysql", "postgresql", "mongodb", "sqlserver", "redis", "db2", "sybase"):
            res.add("INFO", "DB_NOT_MONITORED", engine, "%s is running but no adapter instance is configured" % engine,
                    recommendation="Add a [%s:NAME] section to database.conf to enable deep checks." % base)
    res.table("Monitored database instances", ["Engine", "Instance", "Version", "Role/State", "Source"],
              [[i.get("engine"), i.get("instance"), i.get("version", ""), i.get("role", i.get("open_mode", "")),
                "auto-discovered" if i.get("auto_discovered") else "configured"] for i in results], kind="summary")
    res.data = {"processes": procs, "clients": clients, "instances": results}
    res.summary = "%d instance(s) checked: %s" % (len(results), ", ".join("%s:%s" % (i.get("engine"), i.get("instance")) for i in results) or "none")
