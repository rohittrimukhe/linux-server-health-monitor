"""PostgreSQL adapter (9.4+): availability, connections, sizes, replication,
long-running queries, locks, transaction-ID wraparound and WAL archiving."""
from lshme.core.util import fmt_bytes, pct, severity_for, to_float, to_int


def base_cmd(ctx, inst):
    cmd = ["psql", "-X", "-A", "-t", "-q", "-F", "|", "-v", "ON_ERROR_STOP=0"]
    for opt, flag in (("host", "-h"), ("port", "-p"), ("user", "-U"), ("dbname", "-d")):
        if inst.get(opt):
            cmd += [flag, inst[opt]]
    if not inst.get("dbname"):
        cmd += ["-d", "postgres"]
    env = {"PGCONNECT_TIMEOUT": "10", "PGAPPNAME": "lshme"}
    pw = ctx.cfg.secret("postgresql:" + inst["name"])
    if pw:
        env["PGPASSWORD"] = pw
    if inst.get("passfile"):
        env["PGPASSFILE"] = inst["passfile"]
    user = inst.get("os_user", "")
    if user and ctx.runner.is_root() and ctx.has("runuser") and not inst.get("host"):
        envargs = ["%s=%s" % kv for kv in sorted(env.items())]
        return ["runuser", "-u", user, "--", "env"] + envargs + cmd, {}
    return cmd, env


def q(ctx, inst, sql, timeout=60):
    cmd, env = base_cmd(ctx, inst)
    r = ctx.run(cmd + ["-c", sql], timeout=timeout, env=env)
    return r, [l.split("|") for l in r.lines()]


def check(ctx, res, inst):
    label = "postgresql:%s" % inst["name"]
    info = {"instance": inst["name"], "engine": "postgresql"}
    r, rows = q(ctx, inst, "SELECT current_setting('server_version_num'), version(), pg_is_in_recovery(), "
                           "extract(epoch from now()-pg_postmaster_start_time())::bigint, current_setting('max_connections')")
    if not r.ok or not rows:
        res.add("CRITICAL", "POSTGRES_UNAVAILABLE", label, "PostgreSQL %s is not reachable" % inst["name"],
                evidence=r.brief_error(), recommendation="Check the postgresql service, pg_hba.conf and monitoring credentials.")
        return info
    vnum, version, recovery, uptime, maxc = (rows[0] + [""] * 5)[:5]
    vnum = to_int(vnum, 90400)
    in_recovery = recovery == "t"
    maxc = to_int(maxc, 0)
    _, act = q(ctx, inst, "SELECT state, count(*) FROM pg_stat_activity GROUP BY state")
    conns = sum(to_int(a[1], 0) for a in act if len(a) >= 2)
    cpct = pct(conns, maxc)
    _, dbs = q(ctx, inst, "SELECT datname, pg_database_size(datname), age(datfrozenxid), numbackends, xact_rollback, deadlocks "
                          "FROM pg_database d JOIN pg_stat_database s USING (datname) WHERE datallowconn ORDER BY 2 DESC")
    lq = ctx.t("postgresql", "long_query_minutes", 30)
    _, longq = q(ctx, inst, "SELECT pid, usename, datname, extract(epoch from now()-query_start)::bigint, state, left(regexp_replace(query, E'[\\n\\r|]+', ' ', 'g'), 120) "
                            "FROM pg_stat_activity WHERE state <> 'idle' AND pid <> pg_backend_pid() AND query_start < now() - interval '%d minutes' "
                            "ORDER BY 4 DESC LIMIT 20" % int(lq))
    _, waiting = q(ctx, inst, "SELECT count(*) FROM pg_locks WHERE NOT granted")
    idle_tx_sql = ("SELECT count(*) FROM pg_stat_activity WHERE state = 'idle in transaction' AND state_change < now() - interval '%d minutes'" % int(lq))
    _, idle_tx = q(ctx, inst, idle_tx_sql)
    repl = []
    if not in_recovery:
        lsn_fn = "pg_wal_lsn_diff(pg_current_wal_lsn(), replay_lsn)" if vnum >= 100000 else "pg_xlog_location_diff(pg_current_xlog_location(), replay_location)"
        _, repl = q(ctx, inst, "SELECT application_name, client_addr, state, sync_state, %s FROM pg_stat_replication" % lsn_fn)
    standby_delay = None
    if in_recovery:
        _, d = q(ctx, inst, "SELECT COALESCE(extract(epoch from now() - pg_last_xact_replay_timestamp())::bigint, -1)")
        standby_delay = to_int(d[0][0]) if d and d[0] else None
        rcv_view = "pg_stat_wal_receiver" if vnum >= 90600 else None
        if rcv_view:
            _, wr = q(ctx, inst, "SELECT status FROM pg_stat_wal_receiver")
            info["wal_receiver"] = wr[0][0] if wr and wr[0] else "none"
    _, slots = q(ctx, inst, "SELECT slot_name, slot_type, active FROM pg_replication_slots") if vnum >= 90400 else (None, [])
    _, arch = q(ctx, inst, "SELECT archived_count, failed_count, coalesce(last_failed_time::text,''), coalesce(last_archived_time::text,''), current_setting('archive_mode') FROM pg_stat_archiver") if vnum >= 90400 else (None, [])

    total = sum(to_int(d[1], 0) for d in dbs if len(d) >= 2)
    max_age = max([to_int(d[2], 0) for d in dbs if len(d) >= 3] or [0])
    info.update({"version": version.split(" on ")[0], "role": "standby" if in_recovery else "primary", "in_recovery": in_recovery, "connections": conns, "max_connections": maxc,
                 "databases": dbs, "replication": repl, "slots": slots})
    res.metric("postgresql.%s.connections_percent" % inst["name"], cpct)
    res.metric("postgresql.%s.total_bytes" % inst["name"], total)
    res.metric("postgresql.%s.max_xid_age" % inst["name"], max_age)
    res.table("PostgreSQL %s — overview" % inst["name"], ["Item", "Value"], [
        ["Version", version.split(",")[0]], ["Role", "standby (in recovery)" if in_recovery else "primary"],
        ["Uptime", "%.1f days" % (to_float(uptime, 0) / 86400.0)], ["Connections", "%d / %d (%s%%)" % (conns, maxc, cpct)],
        ["Total database size", fmt_bytes(total)], ["Oldest transaction ID age", "{:,}".format(max_age)],
        ["Waiting lock requests", waiting[0][0] if waiting and waiting[0] else "0"],
        ["Replicas connected", len(repl)] if not in_recovery else ["Replay delay", "%s s" % standby_delay],
        ["WAL archiving", "mode=%s archived=%s failed=%s" % (arch[0][4], arch[0][0], arch[0][1]) if arch and len(arch[0]) >= 5 else "N/A"],
    ], kind="summary")
    res.table("PostgreSQL %s — databases" % inst["name"], ["Database", "Size", "XID age", "Backends", "Rollbacks", "Deadlocks"],
              [[d[0], fmt_bytes(to_int(d[1], 0)), d[2], d[3], d[4], d[5]] for d in dbs if len(d) >= 6])
    sev = severity_for(cpct, ctx.t("postgresql", "connections_warning", 80), ctx.t("postgresql", "connections_critical", 90))
    if sev:
        res.add(sev, "POSTGRES_CONNECTIONS", label, "%d of %d connections used (%.0f%%)" % (conns, maxc, cpct),
                recommendation="Use a connection pooler (pgbouncer) or raise max_connections.")
    sev = severity_for(max_age, ctx.t("postgresql", "xid_age_warning", 1e9), ctx.t("postgresql", "xid_age_critical", 1.5e9))
    if sev:
        res.add(sev, "POSTGRES_XID_WRAPAROUND", label, "Transaction ID age %s approaching wraparound" % "{:,}".format(max_age),
                recommendation="Run VACUUM FREEZE on the oldest databases/tables; check autovacuum is keeping up.")
    if longq:
        res.table("PostgreSQL %s — long-running queries" % inst["name"], ["PID", "User", "DB", "Seconds", "State", "Query"], longq)
        res.add("WARNING", "POSTGRES_LONG_QUERIES", label, "%d query/transaction(s) running longer than %d minutes" % (len(longq), lq),
                evidence="\n".join("pid %s %ss %s" % (x[0], x[3], x[5] if len(x) > 5 else "") for x in longq[:5]))
    n_wait = to_int(waiting[0][0], 0) if waiting and waiting[0] else 0
    res.metric("postgresql.%s.lock_waits" % inst["name"], n_wait)
    if n_wait:
        _, blockers = q(ctx, inst, "SELECT a.pid, a.usename, extract(epoch from now()-a.query_start)::bigint, "
                                   "array_to_string(pg_blocking_pids(a.pid), ',') FROM pg_stat_activity a "
                                   "WHERE cardinality(pg_blocking_pids(a.pid)) > 0" if vnum >= 90600 else "SELECT 1 WHERE false")
        res.add("WARNING", "POSTGRES_LOCK_WAITS", label, "%d lock request(s) are waiting" % n_wait,
                evidence="\n".join("pid %s (%s) waiting %ss, blocked by %s" % tuple((b + ["", "", "", ""])[:4]) for b in blockers[:10]),
                recommendation="Find the blocking session (pg_blocking_pids) and coordinate before terminating it.")
    if idle_tx and idle_tx[0] and to_int(idle_tx[0][0], 0):
        res.add("WARNING", "POSTGRES_IDLE_IN_TX", label, "%s session(s) idle in transaction for more than %d minutes" % (idle_tx[0][0], lq),
                recommendation="Idle transactions hold locks and block vacuum; fix the application or set idle_in_transaction_session_timeout.")
    lw, lc = ctx.t("postgresql", "replication_lag_warning_mb", 512), ctx.t("postgresql", "replication_lag_critical_mb", 4096)
    if repl:
        res.table("PostgreSQL %s — replication" % inst["name"], ["Application", "Client", "State", "Sync", "Replay lag"],
                  [[x[0], x[1], x[2], x[3], fmt_bytes(to_float(x[4], 0))] for x in repl if len(x) >= 5])
        for x in repl:
            mb = to_float(x[4], 0) / 1048576.0 if len(x) >= 5 else 0
            sev = severity_for(mb, lw, lc)
            if sev:
                res.add(sev, "POSTGRES_REPLICATION_LAG", label, "Replica %s is %.0f MB behind" % (x[1] or x[0], mb))
            if len(x) >= 3 and x[2] != "streaming":
                res.add("WARNING", "POSTGRES_REPLICA_STATE", label, "Replica %s state is %s" % (x[1] or x[0], x[2]))
    if in_recovery and info.get("wal_receiver") not in (None, "streaming"):
        res.add("CRITICAL", "POSTGRES_WAL_RECEIVER", label, "Standby WAL receiver is %s" % info.get("wal_receiver"),
                recommendation="Check connectivity to the primary and primary_conninfo.")
    for s in slots:
        if len(s) >= 3 and s[2] == "f":
            res.add("WARNING", "POSTGRES_INACTIVE_SLOT", label, "Replication slot %s is inactive (WAL accumulates)" % s[0],
                    recommendation="Drop unused slots; inactive slots can fill the WAL disk.")
    if arch and len(arch[0]) >= 5 and arch[0][4] in ("on", "always"):
        failed, last_fail, last_ok = to_int(arch[0][1], 0), arch[0][2], arch[0][3]
        if failed and last_fail and (not last_ok or last_fail > last_ok):
            res.add("CRITICAL", "POSTGRES_ARCHIVE_FAILING", label, "WAL archiving is failing (last failure %s)" % last_fail,
                    recommendation="Fix archive_command; pg_wal will grow until the disk fills.")
    return info
