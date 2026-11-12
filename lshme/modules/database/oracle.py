"""Oracle Database adapter (11g R2, 12c, 18c, 19c, 21c, 23ai).

Runs one read-only SQL*Plus session per instance. Every query emits tagged,
pipe-delimited rows (TAG|field|field...) so parsing does not depend on column
widths, NLS settings or version. Views used exist in 11g R2; queries that fail
on a given version are reported, the rest still run.

Authentication: OS authentication ('/ as sysdba' run as the instance owner) by
default, or a user/password from credentials.conf sent over stdin (never argv).
"""
import os
import pwd
import re
import time

from lshme.core.util import fmt_bytes, parse_log_time, severity_for, to_float, to_int

SQL = r"""
whenever sqlerror continue
whenever oserror continue
set define off
set pagesize 0 linesize 32767 feedback off heading off trimspool on trimout on verify off echo off tab off termout on long 4000
alter session set nls_date_format='YYYY-MM-DD HH24:MI:SS';
select 'DB|'||name||'|'||nvl(db_unique_name,name)||'|'||open_mode||'|'||database_role||'|'||log_mode||'|'||force_logging||'|'||flashback_on||'|'||to_char(created,'YYYY-MM-DD') from v$database;
select 'INST|'||instance_name||'|'||host_name||'|'||version||'|'||status||'|'||to_char(startup_time,'YYYY-MM-DD HH24:MI:SS')||'|'||database_status||'|'||archiver||'|'||round((sysdate-startup_time)*86400) from v$instance;
select 'BANNER|'||banner from v$version where rownum=1;
select 'SIZE|'||round(sum(bytes)) from dba_data_files;
select 'TS|'||m.tablespace_name||'|'||t.contents||'|'||round(m.used_percent,2)||'|'||round(m.used_space*t.block_size)||'|'||round(m.tablespace_size*t.block_size)||'|'||t.status from dba_tablespace_usage_metrics m, dba_tablespaces t where m.tablespace_name=t.tablespace_name order by m.used_percent desc;
select 'DF|'||file_id||'|'||tablespace_name||'|'||round(bytes)||'|'||autoextensible||'|'||round(maxbytes)||'|'||status||'|'||file_name from dba_data_files order by file_id;
select 'DFOFF|'||file#||'|'||status||'|'||name from v$datafile where status not in ('ONLINE','SYSTEM');
select 'RECOVER|'||file#||'|'||nvl(error,'-') from v$recover_file;
select 'FRA|'||name||'|'||space_limit||'|'||space_used||'|'||space_reclaimable||'|'||number_of_files from v$recovery_file_dest;
select 'ARCH|'||to_char(trunc(completion_time),'YYYY-MM-DD')||'|'||count(*)||'|'||round(sum(blocks*block_size)) from v$archived_log where completion_time > sysdate-&&DAYS and standby_dest='NO' group by trunc(completion_time) order by 1;
select 'ADEST|'||dest_id||'|'||dest_name||'|'||status||'|'||replace(nvl(error,'-'),'|','/')||'|'||nvl(destination,'-')||'|'||target from v$archive_dest where status<>'INACTIVE';
select 'REDO|'||l.group#||'|'||l.thread#||'|'||l.status||'|'||l.bytes||'|'||l.members||'|'||nvl(f.status,'VALID')||'|'||f.member from v$log l, v$logfile f where l.group#=f.group# order by l.group#;
select 'SWITCH|'||count(*) from v$log_history where first_time > sysdate-1;
select 'CTL|'||nvl(status,'VALID')||'|'||name from v$controlfile;
select 'SESS|'||status||'|'||type||'|'||count(*) from v$session group by status,type;
select 'LIMIT|'||resource_name||'|'||current_utilization||'|'||max_utilization||'|'||trim(limit_value) from v$resource_limit where resource_name in ('processes','sessions');
select 'BLOCK|'||s.sid||'|'||s.serial#||'|'||nvl(s.username,'-')||'|'||s.blocking_session||'|'||s.seconds_in_wait||'|'||replace(s.event,'|','/')||'|'||nvl(s.sql_id,'-') from v$session s where s.blocking_session is not null;
select 'LONG|'||sid||'|'||nvl(username,'-')||'|'||last_call_et||'|'||nvl(sql_id,'-')||'|'||replace(nvl(module,'-'),'|','/') from v$session where status='ACTIVE' and type='USER' and last_call_et > &&LONGSECS;
select * from (select 'WAIT|'||event||'|'||wait_class||'|'||total_waits||'|'||round(time_waited_micro/1000000) from v$system_event where wait_class<>'Idle' order by time_waited_micro desc) where rownum<=10;
select 'SGA|'||round(sum(value)) from v$sga;
select 'PGA|'||name||'|'||round(value) from v$pgastat where name in ('total PGA allocated','aggregate PGA target parameter','maximum PGA allocated');
select 'PARAM|'||name||'|'||nvl(value,'-') from v$parameter where name in ('memory_target','sga_target','pga_aggregate_target','processes','sessions','db_recovery_file_dest_size','log_archive_dest_1','cluster_database','db_block_size','compatible');
select 'RMAN|'||to_char(start_time,'YYYY-MM-DD HH24:MI:SS')||'|'||to_char(end_time,'YYYY-MM-DD HH24:MI:SS')||'|'||input_type||'|'||status||'|'||round(output_bytes)||'|'||round(elapsed_seconds) from v$rman_backup_job_details where start_time > sysdate-&&DAYS order by start_time;
select 'RMANLAST|'||input_type||'|'||round((sysdate-max(end_time))*24,2) from v$rman_backup_job_details where status in ('COMPLETED','COMPLETED WITH WARNINGS') group by input_type;
select 'DG|'||name||'|'||nvl(value,'-')||'|'||nvl(time_computed,'-') from v$dataguard_stats where name in ('transport lag','apply lag','apply finish time');
select 'DGDEST|'||dest_id||'|'||status||'|'||replace(nvl(error,'-'),'|','/')||'|'||nvl(gap_status,'-')||'|'||nvl(database_mode,'-') from v$archive_dest_status where type in ('PHYSICAL','LOGICAL','SNAPSHOT') or (status<>'INACTIVE' and dest_id>1);
select 'MRP|'||process||'|'||status||'|'||sequence# from v$managed_standby where process like 'MRP%';
select 'INVALID|'||owner||'|'||object_type||'|'||count(*) from dba_objects where status='INVALID' group by owner,object_type order by 4 desc;
select 'JOBFAIL|'||owner||'|'||job_name||'|'||to_char(log_date,'YYYY-MM-DD HH24:MI')||'|'||status||'|'||nvl(error#,0) from dba_scheduler_job_run_details where log_date > systimestamp - &&DAYS and status <> 'SUCCEEDED' and rownum <= 50;
select 'BROKEN|'||job||'|'||nvl(schema_user,'-')||'|'||failures||'|'||broken from dba_jobs where broken='Y' or failures>0;
select 'ACCT|'||username||'|'||account_status||'|'||nvl(to_char(expiry_date,'YYYY-MM-DD'),'-') from dba_users where account_status='OPEN' and expiry_date < sysdate+14;
select 'DIAG|'||name||'|'||value from v$diag_info where name in ('Diag Trace','Diag Alert','ADR Home');
select 'BDUMP|'||value from v$parameter where name='background_dump_dest';
select 'END|done' from dual;
exit
"""

CRITICAL_ORA = ("ORA-00600", "ORA-07445", "ORA-04031", "ORA-00257", "ORA-19809", "ORA-19815", "ORA-01578",
                "ORA-00353", "ORA-00312", "ORA-27037", "ORA-01110", "ORA-00470", "ORA-00474", "ORA-16014",
                "ORA-00494", "ORA-29740", "ORA-04030")
IGNORED_ORA = ("ORA-3136", "ORA-03136", "ORA-12012", "ORA-28")  # noisy / informational


def discover(ctx):
    """SIDs from running pmon processes, with ORACLE_HOME from /etc/oratab and process owner."""
    homes = {}
    for line in ctx.read("/etc/oratab").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and ":" in line:
            p = line.split(":")
            if len(p) >= 2:
                homes[p[0]] = p[1]
    found = {}
    r = ctx.run(["ps", "-eo", "user,args"])
    for line in r.lines():
        m = re.search(r"^(\S+)\s+(?:ora|db)_pmon_(\S+)", line)
        if m:
            user, sid = m.group(1), m.group(2)
            if sid.startswith("+"):  # ASM instance
                continue
            home = homes.get(sid) or homes.get(sid.rstrip("0123456789"))  # RAC: ORCL1 -> ORCL
            found[sid] = {"sid": sid, "oracle_home": home or "", "os_user": user, "source": "pmon"}
    return found, homes


def build_command(ctx, inst):
    home = inst.get("oracle_home", "")
    sqlplus = os.path.join(home, "bin", "sqlplus") if home else "sqlplus"
    env = {"ORACLE_SID": inst.get("sid", ""), "NLS_LANG": "AMERICAN_AMERICA.AL32UTF8"}
    if home:
        env["ORACLE_HOME"] = home
        env["LD_LIBRARY_PATH"] = os.path.join(home, "lib")
    if inst.get("tns_admin"):
        env["TNS_ADMIN"] = inst["tns_admin"]
    user = inst.get("os_user", "")
    me = pwd.getpwuid(os.getuid()).pw_name if hasattr(os, "getuid") else ""
    if user and user != me and ctx.runner.is_root():
        envargs = ["%s=%s" % kv for kv in sorted(env.items())]
        wrapper = ["runuser", "-u", user, "--"] if ctx.has("runuser") else ["su", "-s", "/bin/sh", user, "-c"]
        if wrapper[0] == "runuser":
            return wrapper + ["env"] + envargs + [sqlplus, "-S", "-L", "/nolog"], {}
        return wrapper + [" ".join(["env"] + envargs + [sqlplus, "-S", "-L", "/nolog"])], {}
    return [sqlplus, "-S", "-L", "/nolog"], env


def connect_line(ctx, inst):
    user = inst.get("username", "")
    if user:
        pw = ctx.cfg.secret("oracle:" + inst["name"])
        target = inst.get("connect_identifier", "")
        role = inst.get("role", "")
        s = "connect %s/\"%s\"%s%s" % (user, pw, ("@" + target) if target else "", (" as " + role) if role else "")
        return s
    return "connect / as sysdba"


def parse(out):
    rows = {}
    errors = []
    for line in out.splitlines():
        line = line.rstrip()
        if not line:
            continue
        m = re.match(r"^([A-Z]+)\|(.*)$", line)
        if m:
            rows.setdefault(m.group(1), []).append(m.group(2).split("|"))
        elif re.search(r"(ORA|SP2|TNS)-\d+", line):
            errors.append(line.strip())
    return rows, errors


def alert_log(ctx, inst, rows):
    trace_dir = ""
    for r in rows.get("DIAG", []):
        if r[0] == "Diag Trace":
            trace_dir = r[1]
    if not trace_dir and rows.get("BDUMP"):
        trace_dir = rows["BDUMP"][0][0]
    if not trace_dir:
        return None
    path = os.path.join(trace_dir, "alert_%s.log" % inst["sid"])
    if not ctx.runner.exists(path):
        return {"path": path, "error": "not readable"}
    max_mb = ctx.t("oracle", "alert_log_max_mb", 16)
    text = ctx.runner.tail(path, int(max_mb * 1024 * 1024))
    cutoff = time.time() - ctx.days * 86400
    current = None
    errs = {}
    samples = []
    for line in text.splitlines():
        ts = parse_log_time(line)
        if ts is not None:
            current = ts
            continue
        if current is None or current < cutoff:
            continue
        for code in re.findall(r"ORA-\d{4,5}", line):
            if code.startswith(IGNORED_ORA):
                continue
            errs[code] = errs.get(code, 0) + 1
            if len(samples) < 15:
                samples.append("%s %s" % (time.strftime("%Y-%m-%d %H:%M", time.localtime(current)), line.strip()[:250]))
    return {"path": path, "errors": errs, "samples": samples}


def listener(ctx, inst):
    home = inst.get("oracle_home", "")
    lsnrctl = os.path.join(home, "bin", "lsnrctl") if home else "lsnrctl"
    name = inst.get("listener", "")
    cmd = [lsnrctl, "status"] + ([name] if name else [])
    env = {"ORACLE_HOME": home} if home else {}
    user = inst.get("os_user", "")
    if user and ctx.runner.is_root() and ctx.has("runuser"):
        envargs = ["%s=%s" % kv for kv in sorted(env.items())]
        cmd = ["runuser", "-u", user, "--", "env"] + envargs + cmd
        env = {}
    r = ctx.run(cmd, timeout=45, env=env)
    if r.missing:
        return None
    services = re.findall(r'Service "([^"]+)" has \d+ instance', r.out)
    ready = re.findall(r'Instance "([^"]+)", status (\w+)', r.out)
    return {"ok": "completed successfully" in r.out, "services": services, "instances": ready,
            "error": "" if r.ok else (re.findall(r"TNS-\d+[^\n]*", r.out) or [r.brief_error()])[0]}


def check(ctx, res, inst):
    """Run all checks for one Oracle instance; findings go to res."""
    label = "oracle:%s" % inst["name"]
    args, env = build_command(ctx, inst)
    script = "set define off\n" + connect_line(ctx, inst) + "\n" + SQL.replace("&&DAYS", str(int(ctx.days))).replace(
        "&&LONGSECS", str(int(ctx.t("oracle", "long_running_seconds", 3600))))
    r = ctx.run(args, input_text=script, timeout=int(inst.get("timeout", 180)), env=env)
    rows, errors = parse(r.out)
    lsn = listener(ctx, inst) if inst.get("check_listener", "yes") != "no" else None
    info = {"instance": inst["name"], "engine": "oracle", "rows": rows, "errors": errors[:50], "listener": lsn}
    if r.missing:
        res.add("UNKNOWN", "ORACLE_SQLPLUS_MISSING", label, "sqlplus not found for %s" % inst["name"],
                recommendation="Set oracle_home for [oracle:%s] in database.conf." % inst["name"])
        return info
    if "DB" not in rows:
        conn_err = next((e for e in errors if "ORA-" in e or "SP2-" in e), r.brief_error())
        down = any(x in conn_err for x in ("ORA-01034", "ORA-27101", "ORA-12541", "ORA-01089"))
        res.add("CRITICAL", "ORACLE_UNAVAILABLE" if down else "ORACLE_CONNECT_FAILED", label,
                "Oracle %s is %s" % (inst["name"], "not available (instance down)" if down else "not reachable for monitoring"),
                evidence=conn_err,
                recommendation="Check the instance, listener and the monitoring credentials/OS authentication.")
        return info
    db = rows["DB"][0] + [""] * 9
    it = (rows.get("INST") or [[""] * 9])[0] + [""] * 9
    name, uniq, open_mode, role, log_mode, force_log, flashback = db[0], db[1], db[2], db[3], db[4], db[5], db[6]
    version, status, started, archiver = it[2], it[3], it[4], it[6]
    info.update({"name": name, "role": role, "open_mode": open_mode, "version": version, "log_mode": log_mode})
    uptime = to_int(it[7], 0)
    res.metric("oracle.%s.uptime_hours" % inst["name"], (uptime or 0) / 3600.0)
    size = to_int((rows.get("SIZE") or [["0"]])[0][0], 0)
    res.metric("oracle.%s.datafile_bytes" % inst["name"], size)
    banner = (rows.get("BANNER") or [[""]])[0][0]
    res.table("Oracle %s — instance" % inst["name"], ["Item", "Value"], [
        ["Database / unique name", "%s / %s" % (name, uniq)], ["Version", banner or version],
        ["Instance status", status], ["Open mode", open_mode], ["Role", role], ["Log mode", log_mode],
        ["Force logging / flashback", "%s / %s" % (force_log, flashback)], ["Archiver", archiver],
        ["Started", started], ["Datafile size", fmt_bytes(size)],
        ["Listener", ("OK: " + ", ".join(lsn["services"][:6])) if lsn and lsn["ok"] else (lsn["error"] if lsn else "not checked")],
    ], kind="summary")

    primary = role == "PRIMARY"
    if primary and open_mode != "READ WRITE":
        res.add("CRITICAL", "ORACLE_NOT_OPEN", label, "Primary database %s is %s (expected READ WRITE)" % (name, open_mode),
                recommendation="Open the database after investigating why it is not open.")
    if not primary and "STANDBY" in role and open_mode not in ("MOUNTED", "READ ONLY", "READ ONLY WITH APPLY"):
        res.add("WARNING", "ORACLE_STANDBY_MODE", label, "Standby %s is in mode %s" % (name, open_mode))
    if status not in ("OPEN", "MOUNTED"):
        res.add("CRITICAL", "ORACLE_INSTANCE_STATUS", label, "Instance %s status is %s" % (inst["name"], status))
    if log_mode == "NOARCHIVELOG" and primary:
        res.add("WARNING", "ORACLE_NOARCHIVELOG", label, "%s runs in NOARCHIVELOG mode — no point-in-time recovery" % name,
                recommendation="Enable ARCHIVELOG mode for production databases.")
    if archiver in ("STOPPED", "FAILED"):
        res.add("CRITICAL", "ORACLE_ARCHIVER_STUCK", label, "Archiver is %s" % archiver,
                recommendation="Usually the archive destination/FRA is full: free space (via RMAN) and check archive destinations.")
    if lsn is not None and not lsn["ok"]:
        res.add("CRITICAL", "ORACLE_LISTENER_DOWN", label, "Listener for %s is not running" % inst["name"], evidence=lsn["error"],
                recommendation="Start the listener (lsnrctl start) and check listener.log.")
    if uptime and uptime < 86400:
        res.add("INFO", "ORACLE_RECENT_START", label, "Instance started %s" % started)

    # tablespaces
    tw, tc = ctx.t("oracle", "tablespace_warning", 85), ctx.t("oracle", "tablespace_critical", 95)
    incl = ctx.cfg.getbool("thresholds", "oracle", "include_undo_temp", False)
    ts_rows = []
    for t in rows.get("TS", []):
        if len(t) < 6:
            continue
        tname, contents, used_pct, used_b, max_b = t[0], t[1], to_float(t[2], 0), to_int(t[3], 0), to_int(t[4], 0)
        sev = severity_for(used_pct, tw, tc)
        ts_rows.append([tname, contents, "%.1f%%" % used_pct, fmt_bytes(used_b), fmt_bytes(max_b), fmt_bytes(max_b - used_b), sev or "OK"])
        res.metric("oracle.%s.ts_used_percent:%s" % (inst["name"], tname), used_pct)
        if sev and (incl or contents == "PERMANENT"):
            res.add(sev, "ORACLE_TABLESPACE", "%s %s" % (label, tname),
                    "Tablespace %s is %.1f%% of its maximum size (%s free)" % (tname, used_pct, fmt_bytes(max_b - used_b)),
                    recommendation="Add a datafile or enable/raise autoextend MAXSIZE; check the filesystem/ASM space behind it.")
    res.table("Oracle %s — tablespaces (percent of max size incl. autoextend)" % inst["name"],
              ["Tablespace", "Contents", "Used%", "Used", "Max size", "Free", "Status"], ts_rows, kind="summary")
    df_rows = [[d[0], d[1], fmt_bytes(to_int(d[2], 0)), d[3], fmt_bytes(to_int(d[4], 0)) if d[3] == "YES" else "-", d[5], d[6]]
               for d in rows.get("DF", []) if len(d) >= 7]
    res.table("Oracle %s — datafiles" % inst["name"], ["File#", "Tablespace", "Size", "Autoextend", "Max size", "Status", "Path"], df_rows)
    for d in rows.get("DFOFF", []):
        res.add("CRITICAL", "ORACLE_DATAFILE_OFFLINE", label, "Datafile %s is %s: %s" % (d[0], d[1], d[2] if len(d) > 2 else ""))
    for d in rows.get("RECOVER", []):
        res.add("CRITICAL", "ORACLE_DATAFILE_RECOVERY", label, "Datafile %s needs media recovery (%s)" % (d[0], d[1] if len(d) > 1 else ""))

    # FRA
    fw, fc = ctx.t("oracle", "fra_warning", 80), ctx.t("oracle", "fra_critical", 90)
    for f in rows.get("FRA", []):
        if len(f) < 5:
            continue
        limit, used, recl = to_int(f[1], 0), to_int(f[2], 0), to_int(f[3], 0)
        if not limit:
            continue
        upct = round(100.0 * (used - recl) / limit, 1)
        res.metric("oracle.%s.fra_used_percent" % inst["name"], upct)
        res.table("Oracle %s — Fast Recovery Area" % inst["name"], ["Location", "Limit", "Used", "Reclaimable", "Effective used%", "Files"],
                  [[f[0], fmt_bytes(limit), fmt_bytes(used), fmt_bytes(recl), "%.1f%%" % upct, f[4]]], kind="summary")
        sev = severity_for(upct, fw, fc)
        if sev:
            res.add(sev, "ORACLE_FRA_FULL", label, "FRA is %.1f%% used after reclaimable space" % upct,
                    recommendation="Back up and delete archived logs via RMAN, or raise db_recovery_file_dest_size. Never delete files manually.")
    # archive logs
    arch = rows.get("ARCH", [])
    if arch:
        res.table("Oracle %s — archived log generation" % inst["name"], ["Day", "Logs", "Volume"],
                  [[a[0], a[1], fmt_bytes(to_int(a[2], 0))] for a in arch if len(a) >= 3])
        res.metric("oracle.%s.archive_bytes_per_day" % inst["name"], sum(to_int(a[2], 0) for a in arch) / float(max(1, len(arch))))
    for a in rows.get("ADEST", []):
        if len(a) >= 4 and a[2] not in ("VALID", "DEFERRED"):
            res.add("CRITICAL", "ORACLE_ARCHIVE_DEST_ERROR", label, "Archive destination %s is %s" % (a[1], a[2]), evidence=a[3],
                    recommendation="Fix the destination (space, permissions, standby connectivity).")
    sw = to_int((rows.get("SWITCH") or [["0"]])[0][0], 0)
    res.metric("oracle.%s.log_switches_24h" % inst["name"], sw)
    # redo / control files
    redo = rows.get("REDO", [])
    res.table("Oracle %s — redo logs" % inst["name"], ["Group", "Thread", "Status", "Size", "Members", "Member status", "Member"],
              [[x[0], x[1], x[2], fmt_bytes(to_int(x[3], 0)), x[4], x[5], x[6]] for x in redo if len(x) >= 7])
    for x in redo:
        if len(x) >= 6 and x[5] in ("INVALID", "STALE", "DELETED"):
            res.add("WARNING" if x[5] == "STALE" else "CRITICAL", "ORACLE_REDO_MEMBER", label,
                    "Redo log group %s member is %s: %s" % (x[0], x[5], x[6] if len(x) > 6 else ""),
                    recommendation="Drop and re-add the damaged redo member.")
    if redo and len(set(x[0] for x in redo)) == len(redo) and len(redo) > 0:
        res.add("INFO", "ORACLE_REDO_NOT_MULTIPLEXED", label, "Redo log groups are not multiplexed (1 member each)",
                recommendation="Multiplex redo logs across separate disks.")
    if sw > 24 * 6:
        res.add("WARNING", "ORACLE_LOG_SWITCHES", label, "%d log switches in 24h (more than 6/hour)" % sw,
                recommendation="Consider larger redo logs to reduce checkpoint pressure.")
    ctl = rows.get("CTL", [])
    for c in ctl:
        if c and c[0] not in ("VALID", ""):
            res.add("CRITICAL", "ORACLE_CONTROLFILE", label, "Control file %s status %s" % (c[1] if len(c) > 1 else "", c[0]))
    if len(ctl) == 1:
        res.add("WARNING", "ORACLE_SINGLE_CONTROLFILE", label, "Only one control file is configured",
                recommendation="Multiplex control files on separate storage.")

    # sessions & limits
    lw, lc = ctx.t("oracle", "limits_warning", 80), ctx.t("oracle", "limits_critical", 90)
    lim_rows = []
    for l in rows.get("LIMIT", []):
        if len(l) < 4:
            continue
        cur, mx, limit = to_int(l[1], 0), to_int(l[2], 0), to_int(l[3])
        p = round(100.0 * cur / limit, 1) if limit else None
        lim_rows.append([l[0], cur, mx, l[3], "%s%%" % p if p is not None else "-"])
        res.metric("oracle.%s.%s_percent" % (inst["name"], l[0]), p)
        sev = severity_for(p, lw, lc)
        if sev:
            res.add(sev, "ORACLE_LIMIT", label, "%s at %s of %s (%.0f%%)" % (l[0], cur, l[3], p),
                    recommendation="Find connection leaks/storms; raise the parameter if load is legitimate.")
    sess = rows.get("SESS", [])
    active = sum(to_int(s[2], 0) for s in sess if len(s) >= 3 and s[0] == "ACTIVE" and s[1] == "USER")
    total_user = sum(to_int(s[2], 0) for s in sess if len(s) >= 3 and s[1] == "USER")
    res.metric("oracle.%s.active_sessions" % inst["name"], active)
    lim_rows.append(["user sessions (active/total)", "%d/%d" % (active, total_user), "", "", ""])
    res.table("Oracle %s — sessions and limits" % inst["name"], ["Resource", "Current", "Max reached", "Limit", "Used"], lim_rows)
    bs = ctx.t("oracle", "blocking_seconds", 300)
    blocks = [b for b in rows.get("BLOCK", []) if len(b) >= 6]
    long_blocks = [b for b in blocks if to_int(b[4], 0) >= bs]
    if blocks:
        res.table("Oracle %s — blocked sessions" % inst["name"], ["SID", "Serial", "User", "Blocked by", "Waiting s", "Event", "SQL ID"], blocks)
    if long_blocks:
        res.add("WARNING", "ORACLE_BLOCKING", label, "%d session(s) blocked for more than %d seconds" % (len(long_blocks), bs),
                evidence="\n".join("sid %s blocked by %s for %ss on %s" % (b[0], b[3], b[4], b[5]) for b in long_blocks[:10]),
                recommendation="Identify the blocking session's transaction; coordinate with the application before killing it.")
    longs = rows.get("LONG", [])
    if longs:
        res.table("Oracle %s — long-running active sessions" % inst["name"], ["SID", "User", "Seconds", "SQL ID", "Module"], longs)
        res.add("INFO", "ORACLE_LONG_RUNNING", label, "%d user session(s) active for more than %d s" % (len(longs), ctx.t("oracle", "long_running_seconds", 3600)))
    waits = rows.get("WAIT", [])
    if waits:
        res.table("Oracle %s — top wait events since startup" % inst["name"], ["Event", "Class", "Waits", "Seconds waited"], waits)
    sga = to_int((rows.get("SGA") or [["0"]])[0][0], 0)
    pga = dict((p[0], to_int(p[1], 0)) for p in rows.get("PGA", []) if len(p) >= 2)
    params = dict((p[0], p[1]) for p in rows.get("PARAM", []) if len(p) >= 2)
    res.table("Oracle %s — memory and parameters" % inst["name"], ["Item", "Value"],
              [["SGA size", fmt_bytes(sga)], ["PGA allocated", fmt_bytes(pga.get("total PGA allocated"))],
               ["PGA target", fmt_bytes(pga.get("aggregate PGA target parameter"))],
               ["PGA max allocated", fmt_bytes(pga.get("maximum PGA allocated"))]] + [[k, v] for k, v in sorted(params.items())])
    tgt = pga.get("aggregate PGA target parameter")
    if tgt and pga.get("total PGA allocated", 0) > 1.2 * tgt:
        res.add("WARNING", "ORACLE_PGA_OVER_TARGET", label, "PGA allocated %s exceeds target %s" % (
            fmt_bytes(pga["total PGA allocated"]), fmt_bytes(tgt)))

    # RMAN
    if ctx.cfg.getbool("thresholds", "oracle", "check_rman", True) and inst.get("check_rman", "yes") != "no":
        jobs = rows.get("RMAN", [])
        res.table("Oracle %s — RMAN jobs (last %d day(s))" % (inst["name"], ctx.days), ["Start", "End", "Type", "Status", "Output", "Elapsed s"],
                  [[j[0], j[1], j[2], j[3], fmt_bytes(to_int(j[4], 0)), j[5]] for j in jobs if len(j) >= 6], kind="summary")
        failed = [j for j in jobs if len(j) >= 4 and j[3] in ("FAILED", "COMPLETED WITH ERRORS")]
        if failed:
            res.add("WARNING", "ORACLE_RMAN_FAILED", label, "%d RMAN job(s) failed in the last %d day(s)" % (len(failed), ctx.days),
                    evidence="\n".join("%s %s %s" % (j[0], j[2], j[3]) for j in failed[:10]),
                    recommendation="Review the RMAN logs / v$rman_output for the failed sessions.")
        last = dict((x[0], to_float(x[1])) for x in rows.get("RMANLAST", []) if len(x) >= 2)
        db_types = [last[k] for k in last if k in ("DB FULL", "DB INCR", "DATAFILE FULL", "DATAFILE INCR") and last[k] is not None]
        max_age = to_float(inst.get("backup_max_age_hours"), ctx.t("oracle", "backup_max_age_hours", 26))
        if primary:
            if not db_types:
                res.add("CRITICAL", "ORACLE_NO_DB_BACKUP", label, "No successful RMAN database backup recorded in the control file",
                        recommendation="Configure and schedule RMAN backups (or verify they run from another site/catalog).")
            elif min(db_types) > max_age:
                res.add("CRITICAL", "ORACLE_BACKUP_OLD", label, "Last successful RMAN database backup is %.1f hours old (limit %.0f)" % (min(db_types), max_age),
                        recommendation="Investigate the backup schedule; recovery point objective is not met.")
            if db_types:
                res.metric("oracle.%s.backup_age_hours" % inst["name"], min(db_types))
            arch_age = last.get("ARCHIVELOG")
            alim = ctx.t("oracle", "archivelog_backup_max_age_hours", 24)
            if log_mode == "ARCHIVELOG" and arch_age is not None and arch_age > alim:
                res.add("WARNING", "ORACLE_ARCHLOG_BACKUP_OLD", label, "Last archived-log backup is %.1f hours old" % arch_age)

    # Data Guard
    dg = dict((d[0], d[1]) for d in rows.get("DG", []) if len(d) >= 2)
    if dg or rows.get("DGDEST") or rows.get("MRP"):
        def lag_seconds(v):
            m = re.match(r"\+?(\d+) (\d+):(\d+):(\d+)", v or "")
            return int(m.group(1)) * 86400 + int(m.group(2)) * 3600 + int(m.group(3)) * 60 + int(m.group(4)) if m else None
        rows_dg = [[k, v] for k, v in dg.items()]
        for d in rows.get("DGDEST", []):
            rows_dg.append(["dest %s" % d[0], "%s gap=%s mode=%s %s" % (d[1], d[3] if len(d) > 3 else "", d[4] if len(d) > 4 else "", d[2] if d[2] != "-" else "")])
            if d[1] not in ("VALID", "DEFERRED") or (len(d) > 3 and d[3] not in ("NO GAP", "-", "")):
                res.add("CRITICAL", "ORACLE_DG_DEST", label, "Data Guard destination %s: status %s, gap %s" % (d[0], d[1], d[3] if len(d) > 3 else "?"),
                        evidence=d[2], recommendation="Check standby connectivity and archive shipping (v$archive_gap).")
        for p in rows.get("MRP", []):
            rows_dg.append([p[0], "%s seq %s" % (p[1], p[2] if len(p) > 2 else "")])
        res.table("Oracle %s — Data Guard" % inst["name"], ["Item", "Value"], rows_dg, kind="summary")
        dw, dc = ctx.t("oracle", "dg_lag_warning_seconds", 300), ctx.t("oracle", "dg_lag_critical_seconds", 1800)
        for k in ("transport lag", "apply lag"):
            s = lag_seconds(dg.get(k))
            if s is not None:
                res.metric("oracle.%s.%s_seconds" % (inst["name"], k.replace(" ", "_")), s)
                sev = severity_for(s, dw, dc)
                if sev:
                    res.add(sev, "ORACLE_DG_LAG", label, "Data Guard %s is %s" % (k, dg[k]),
                            recommendation="Check redo transport network and the standby's apply process (MRP).")
        if "STANDBY" in role and not rows.get("MRP"):
            res.add("CRITICAL", "ORACLE_MRP_DOWN", label, "Managed recovery (MRP) is not running on standby %s" % name,
                    recommendation="Start apply: ALTER DATABASE RECOVER MANAGED STANDBY DATABASE DISCONNECT;")

    # objects, jobs, accounts
    inv = rows.get("INVALID", [])
    n_invalid = sum(to_int(i[2], 0) for i in inv if len(i) >= 3)
    res.metric("oracle.%s.invalid_objects" % inst["name"], n_invalid)
    if inv:
        res.table("Oracle %s — invalid objects" % inst["name"], ["Owner", "Type", "Count"], inv[:50])
    if n_invalid >= ctx.t("oracle", "invalid_objects_warning", 50):
        res.add("WARNING", "ORACLE_INVALID_OBJECTS", label, "%d invalid objects" % n_invalid,
                recommendation="Recompile with utlrp.sql after confirming no deployment is in progress.")
    elif n_invalid:
        res.add("INFO", "ORACLE_INVALID_OBJECTS", label, "%d invalid objects" % n_invalid)
    jf = rows.get("JOBFAIL", [])
    if jf:
        res.table("Oracle %s — failed scheduler jobs" % inst["name"], ["Owner", "Job", "Time", "Status", "Error"], jf)
        res.add("WARNING", "ORACLE_JOBS_FAILED", label, "%d failed scheduler job run(s) in the last %d day(s)" % (len(jf), ctx.days),
                evidence="\n".join("%s.%s %s %s ORA-%s" % (j[0], j[1], j[2], j[3], j[4]) for j in jf[:10]))
    for b in rows.get("BROKEN", []):
        res.add("WARNING", "ORACLE_JOB_BROKEN", label, "DBMS_JOB %s (%s) failures=%s broken=%s" % tuple((b + ["", "", "", ""])[:4]))
    for a in rows.get("ACCT", []):
        res.add("INFO", "ORACLE_ACCOUNT_EXPIRING", label, "Account %s password expires %s" % (a[0], a[2] if len(a) > 2 else "?"),
                recommendation="Change the password before expiry to avoid application outages.")

    # alert log
    al = alert_log(ctx, inst, rows)
    info["alert_log"] = al
    if al and al.get("errors"):
        crit = dict((k, v) for k, v in al["errors"].items() if k.startswith(CRITICAL_ORA))
        other = dict((k, v) for k, v in al["errors"].items() if k not in crit)
        res.table("Oracle %s — alert log errors (last %d day(s))" % (inst["name"], ctx.days), ["Error", "Count"],
                  sorted([[k, v] for k, v in al["errors"].items()], key=lambda x: -x[1]))
        if crit:
            res.add("CRITICAL", "ORACLE_ALERT_CRITICAL", label, "Critical errors in alert log: %s" % ", ".join("%s x%d" % kv for kv in sorted(crit.items())),
                    evidence="\n".join(al["samples"]), recommendation="Investigate immediately; open an SR with Oracle Support for ORA-600/7445.")
        if other:
            res.add("WARNING", "ORACLE_ALERT_ERRORS", label, "Errors in alert log: %s" % ", ".join("%s x%d" % kv for kv in sorted(other.items())[:10]),
                    evidence="\n".join(al["samples"]))
    elif al and al.get("error"):
        res.skip("alert log %s %s" % (al["path"], al["error"]))
    query_errors = [e for e in errors if "ORA-00942" in e or "ORA-00904" in e]
    if query_errors:
        res.skip("some Oracle queries are not supported on %s: %s" % (version, "; ".join(query_errors[:3])))
    return info
