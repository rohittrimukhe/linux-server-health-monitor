# Linux Server Health Monitor Enterprise
# Copyright (c) 2026 LRS Services Pvt Ltd, Mumbai. All rights reserved.
"""MySQL / MariaDB adapter: availability, connections, replication, sizes, InnoDB."""
from lshme.core.util import fmt_bytes, pct, severity_for, to_float, to_int


def base_cmd(ctx, inst):
    client = "mariadb" if ctx.has("mariadb") and not ctx.has("mysql") else "mysql"
    cmd = [client]
    if inst.get("defaults_file"):
        cmd.append("--defaults-extra-file=" + inst["defaults_file"])
    for opt in ("host", "port", "socket", "user"):
        if inst.get(opt):
            cmd.append("--%s=%s" % (opt, inst[opt]))
    cmd += ["--connect-timeout=10", "-N", "-B"]
    env = {}
    pw = ctx.cfg.secret("mysql:" + inst["name"])
    if pw:
        env["MYSQL_PWD"] = pw  # not visible in the process list
    return cmd, env


def q(ctx, inst, sql, timeout=60, headers=False):
    cmd, env = base_cmd(ctx, inst)
    if headers:
        cmd = [c for c in cmd if c != "-N"]
    return ctx.run(cmd + ["-e", sql], timeout=timeout, env=env)


def replica_status(ctx, inst):
    """SHOW REPLICA/SLAVE STATUS as a dict (header row + value row, tab separated).

    -N cannot be used here: it also strips the field names, so the result would be unlabelled.
    """
    for sql in ("SHOW REPLICA STATUS", "SHOW SLAVE STATUS"):
        r = q(ctx, inst, sql, headers=True)
        if r.ok:
            lines = r.out.split("\n")
            if len(lines) >= 2 and lines[0].strip():
                return dict(zip(lines[0].split("\t"), lines[1].split("\t")))
            return {}
    return {}


def check(ctx, res, inst):
    label = "mysql:%s" % inst["name"]
    info = {"instance": inst["name"], "engine": "mysql"}
    r = q(ctx, inst, "SELECT VERSION(), @@hostname, @@port, @@read_only")
    if not r.ok:
        res.add("CRITICAL", "MYSQL_UNAVAILABLE", label, "MySQL/MariaDB %s is not reachable" % inst["name"],
                evidence=r.brief_error(), recommendation="Check mysqld status, socket/port and monitoring credentials.")
        return info
    ver, host, port, ro = (r.out.split("\t") + ["", "", "", ""])[:4]
    status = dict(l.split("\t", 1) for l in q(ctx, inst, "SHOW GLOBAL STATUS").lines() if "\t" in l)
    var = dict(l.split("\t", 1) for l in q(ctx, inst, "SHOW GLOBAL VARIABLES").lines() if "\t" in l)
    conns = to_int(status.get("Threads_connected"), 0)
    maxc = to_int(var.get("max_connections"), 0)
    cpct = pct(conns, maxc)
    uptime = to_int(status.get("Uptime"), 0)
    sizes = [l.split("\t") for l in q(ctx, inst, "SELECT table_schema, ROUND(SUM(data_length+index_length)), COUNT(*) FROM information_schema.tables "
                                                  "GROUP BY table_schema ORDER BY 2 DESC", timeout=120).lines()]
    rs = replica_status(ctx, inst)
    reads = to_float(status.get("Innodb_buffer_pool_reads"), 0)
    reqs = to_float(status.get("Innodb_buffer_pool_read_requests"), 0)
    hit = round(100.0 * (1 - reads / reqs), 2) if reqs else None
    info.update({"version": ver, "connections": conns, "max_connections": maxc, "uptime": uptime, "replication": rs})
    res.metric("mysql.%s.connections_percent" % inst["name"], cpct)
    res.metric("mysql.%s.slow_queries" % inst["name"], to_int(status.get("Slow_queries"), 0))
    total = sum(to_int(s[1], 0) for s in sizes if len(s) >= 2)
    res.metric("mysql.%s.total_bytes" % inst["name"], total)
    res.table("MySQL %s — overview" % inst["name"], ["Item", "Value"], [
        ["Version", ver], ["Host:port", "%s:%s" % (host, port)], ["Read only", ro],
        ["Uptime", "%.1f days" % (uptime / 86400.0)], ["Connections", "%s / %s (%s%%)" % (conns, maxc, cpct)],
        ["Max used connections", status.get("Max_used_connections", "")], ["Aborted connects", status.get("Aborted_connects", "")],
        ["Slow queries", status.get("Slow_queries", "")], ["InnoDB buffer pool hit", "%s%%" % hit if hit is not None else "N/A"],
        ["Total data size", fmt_bytes(total)],
        ["Replication", "IO=%s SQL=%s lag=%s" % (rs.get("Replica_IO_Running", rs.get("Slave_IO_Running", "-")),
                                                   rs.get("Replica_SQL_Running", rs.get("Slave_SQL_Running", "-")),
                                                   rs.get("Seconds_Behind_Source", rs.get("Seconds_Behind_Master", "-"))) if rs else "not a replica"],
    ], kind="summary")
    res.table("MySQL %s — schema sizes" % inst["name"], ["Schema", "Size", "Tables"],
              [[s[0], fmt_bytes(to_int(s[1], 0)), s[2]] for s in sizes if len(s) >= 3])
    sev = severity_for(cpct, ctx.t("mysql", "connections_warning", 80), ctx.t("mysql", "connections_critical", 90))
    if sev:
        res.add(sev, "MYSQL_CONNECTIONS", label, "%d of %d connections used (%.0f%%)" % (conns, maxc, cpct),
                recommendation="Look for connection leaks or raise max_connections.")
    if rs:
        io = rs.get("Replica_IO_Running", rs.get("Slave_IO_Running"))
        sql = rs.get("Replica_SQL_Running", rs.get("Slave_SQL_Running"))
        lag = to_int(rs.get("Seconds_Behind_Source", rs.get("Seconds_Behind_Master")))
        err = rs.get("Last_IO_Error") or rs.get("Last_SQL_Error") or rs.get("Last_Error") or ""
        if io != "Yes" or sql != "Yes":
            res.add("CRITICAL", "MYSQL_REPLICATION_STOPPED", label, "Replication stopped (IO=%s, SQL=%s)" % (io, sql),
                    evidence=err,
                    recommendation="Inspect the replication error before restarting the replica threads.")
        elif lag is not None:
            res.metric("mysql.%s.replication_lag" % inst["name"], lag)
            sev = severity_for(lag, ctx.t("mysql", "replication_lag_warning", 300), ctx.t("mysql", "replication_lag_critical", 1800))
            if sev:
                res.add(sev, "MYSQL_REPLICATION_LAG", label, "Replica is %d seconds behind" % lag)
    if hit is not None and hit < 95 and uptime > 86400:
        res.add("INFO", "MYSQL_BUFFER_POOL", label, "InnoDB buffer pool hit ratio %.1f%%" % hit,
                recommendation="Consider increasing innodb_buffer_pool_size.")
    if uptime < 86400:
        res.add("INFO", "MYSQL_RECENT_START", label, "Server restarted %.1f hours ago" % (uptime / 3600.0))
    return info
