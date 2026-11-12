# Linux Server Health Monitor Enterprise
# Copyright (c) 2026 LRS Services Pvt Ltd, Mumbai. All rights reserved.
"""Microsoft SQL Server on Linux adapter: availability, database states,
backup freshness (msdb) and failed SQL Agent jobs."""
from lshme.core.util import fmt_bytes, to_float, to_int

QUERIES = {
    "ver": "SET NOCOUNT ON; SELECT CAST(SERVERPROPERTY('ProductVersion') AS varchar(50)) + '|' + CAST(SERVERPROPERTY('Edition') AS varchar(100)) + '|' + CAST(DATEDIFF(second, sqlserver_start_time, GETDATE()) AS varchar(20)) FROM sys.dm_os_sys_info",
    "dbs": "SET NOCOUNT ON; SELECT d.name + '|' + d.state_desc + '|' + d.recovery_model_desc + '|' + CAST(SUM(CAST(f.size AS bigint))*8192 AS varchar(30)) FROM sys.databases d JOIN sys.master_files f ON d.database_id=f.database_id GROUP BY d.name, d.state_desc, d.recovery_model_desc",
    "bak": "SET NOCOUNT ON; SELECT d.name + '|' + ISNULL(CAST(DATEDIFF(minute, MAX(b.backup_finish_date), GETDATE())/60.0 AS varchar(20)),'never') FROM sys.databases d LEFT JOIN msdb.dbo.backupset b ON b.database_name=d.name AND b.type IN ('D','I') WHERE d.name <> 'tempdb' GROUP BY d.name",
    "jobs": "SET NOCOUNT ON; SELECT TOP 50 j.name + '|' + CAST(h.run_date AS varchar(10)) + '|' + REPLACE(LEFT(h.message,150),'|','/') FROM msdb.dbo.sysjobhistory h JOIN msdb.dbo.sysjobs j ON h.job_id=j.job_id WHERE h.run_status=0 AND h.step_id=0 AND h.run_date >= CONVERT(int, CONVERT(varchar(8), DATEADD(day, -{days}, GETDATE()), 112)) ORDER BY h.run_date DESC",
}


def q(ctx, inst, sql):
    cmd = ["sqlcmd", "-S", inst.get("server", "localhost"), "-h", "-1", "-W", "-b", "-l", "15"]
    if inst.get("username"):
        cmd += ["-U", inst["username"]]
    else:
        cmd += ["-E"]
    if inst.get("trust_server_certificate", "yes") == "yes":
        cmd += ["-C"]
    env = {}
    pw = ctx.cfg.secret("sqlserver:" + inst["name"])
    if pw:
        env["SQLCMDPASSWORD"] = pw
    r = ctx.run(cmd + ["-Q", sql], timeout=90, env=env)
    return r, [l.split("|") for l in r.lines() if "|" in l]


def check(ctx, res, inst):
    label = "sqlserver:%s" % inst["name"]
    info = {"instance": inst["name"], "engine": "sqlserver"}
    r, ver = q(ctx, inst, QUERIES["ver"])
    if not r.ok or not ver:
        res.add("CRITICAL", "SQLSERVER_UNAVAILABLE", label, "SQL Server %s is not reachable" % inst["name"],
                evidence=r.brief_error(), recommendation="Check mssql-server status and the monitoring login.")
        return info
    _, dbs = q(ctx, inst, QUERIES["dbs"])
    _, bak = q(ctx, inst, QUERIES["bak"])
    _, jobs = q(ctx, inst, QUERIES["jobs"].format(days=int(ctx.days)))
    v = ver[0] + ["", "", ""]
    info.update({"version": v[0], "edition": v[1], "databases": dbs, "backups": bak, "failed_jobs": jobs})
    res.table("SQL Server %s — overview" % inst["name"], ["Item", "Value"], [
        ["Version", v[0]], ["Edition", v[1]], ["Uptime", "%.1f days" % (to_float(v[2], 0) / 86400.0)],
        ["Databases", len(dbs)], ["Failed agent jobs (%dd)" % ctx.days, len(jobs)]], kind="summary")
    ages = dict((b[0], b[1]) for b in bak if len(b) >= 2)
    res.table("SQL Server %s — databases" % inst["name"], ["Database", "State", "Recovery", "Size", "Last full/diff backup (h)"],
              [[d[0], d[1], d[2], fmt_bytes(to_int(d[3], 0)), ages.get(d[0], "")] for d in dbs if len(d) >= 4])
    limit = ctx.t("sqlserver", "backup_max_age_hours", 26)
    for d in dbs:
        if len(d) >= 2 and d[1] != "ONLINE":
            res.add("CRITICAL", "SQLSERVER_DB_STATE", label, "Database %s is %s" % (d[0], d[1]))
    for name, age in ages.items():
        if age == "never":
            res.add("WARNING", "SQLSERVER_NO_BACKUP", label, "Database %s has never been backed up" % name)
        elif to_float(age, 0) > limit:
            res.add("CRITICAL", "SQLSERVER_BACKUP_OLD", label, "Last backup of %s is %.0f hours old" % (name, to_float(age, 0)))
    if jobs:
        res.table("SQL Server %s — failed agent jobs" % inst["name"], ["Job", "Date", "Message"], jobs)
        res.add("WARNING", "SQLSERVER_JOBS_FAILED", label, "%d failed SQL Agent job run(s)" % len(jobs))
    return info
