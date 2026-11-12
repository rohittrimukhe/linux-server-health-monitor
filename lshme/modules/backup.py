"""Backup monitoring: NetBackup (master and client), Backup Exec RALUS, Veeam
Agent, generic backup directories/log files and backup cron inventory.
Oracle RMAN backups are evaluated by the database module."""
import fnmatch
import os
import re
import time

from lshme.core.util import fmt_bytes, listening_ports, to_float, to_int

NAME = "backup"
TITLE = "Backup & Recovery"
WEIGHT = 10

NBU_BIN = "/usr/openv/netbackup/bin"


def enabled(ctx, section, detected):
    v = ctx.cfg.get("backup", section, "enabled", "auto").lower()
    return detected if v == "auto" else v in ("yes", "true", "1", "on")


def netbackup(ctx, res):
    master_cmd = NBU_BIN + "/admincmd/bpdbjobs"
    client_cmd = NBU_BIN + "/bpclimagelist"
    detected = ctx.runner.exists(NBU_BIN)
    if not enabled(ctx, "netbackup", detected):
        return None
    max_age = ctx.cfg.getfloat("backup", "netbackup", "max_age_hours", 26)
    out = {"role": "client", "jobs": [], "last_backup": None}
    if ctx.runner.exists(master_cmd):
        out["role"] = "master/media"
        r = ctx.run([master_cmd, "-report", "-most_columns"], timeout=180)
        cutoff = time.time() - ctx.days * 86400
        fails, partial, total = [], [], 0
        for line in r.lines():
            p = line.split(",")
            # jobid,jobtype,state,status,policy,schedule,client,server,started,elapsed,ended,...
            if len(p) < 11 or not p[0].isdigit():
                continue
            started = to_int(p[8], 0)
            if started and started < cutoff:
                continue
            if p[2] != "3":  # 3 = done
                continue
            total += 1
            status = to_int(p[3], -1)
            job = {"id": p[0], "policy": p[4], "schedule": p[5], "client": p[6], "status": status,
                   "started": time.strftime("%Y-%m-%d %H:%M", time.localtime(started)) if started else ""}
            out["jobs"].append(job)
            if status == 1:
                partial.append(job)
            elif status not in (0, 1):
                fails.append(job)
        out.update({"total": total, "failed": len(fails), "partial": len(partial)})
        res.table("NetBackup jobs (last %d day(s))" % ctx.days, ["Job", "Policy", "Schedule", "Client", "Status", "Started"],
                  [[j["id"], j["policy"], j["schedule"], j["client"], j["status"], j["started"]] for j in (fails + partial)[:200]],
                  note="Only failed and partially successful jobs are listed. %d jobs completed in total." % total)
        if fails:
            res.add("CRITICAL" if len(fails) > max(1, total // 10) else "WARNING", "NBU_JOBS_FAILED", "netbackup",
                    "%d of %d NetBackup jobs failed in the last %d day(s)" % (len(fails), total, ctx.days),
                    evidence="\n".join("job %s %s/%s client %s status %s" % (j["id"], j["policy"], j["schedule"], j["client"], j["status"]) for j in fails[:15]),
                    recommendation="Look up each status code (bperror -S <code>) and rerun failed jobs.")
        if partial:
            res.add("WARNING", "NBU_JOBS_PARTIAL", "netbackup", "%d NetBackup jobs were only partially successful (status 1)" % len(partial))
        if not r.ok:
            res.add("UNKNOWN", "NBU_QUERY_FAILED", "netbackup", "bpdbjobs failed", evidence=r.brief_error())
    if ctx.runner.exists(client_cmd):
        r = ctx.run([client_cmd, "-client", ctx.hostname, "-ct", "0"], timeout=120)
        last = None
        for line in r.lines():
            m = re.match(r"^(\d{2})/(\d{2})/(\d{4})\s+(\d{2}):(\d{2})", line.strip())
            if m:
                try:
                    ts = time.mktime(time.strptime(m.group(0), "%m/%d/%Y %H:%M"))
                    last = ts if last is None else max(last, ts)
                except ValueError:
                    pass
        out["last_backup"] = last
        if last is None:
            res.add("WARNING", "NBU_NO_IMAGE", "netbackup", "No NetBackup image found for this client",
                    evidence=r.brief_error() if not r.ok else "")
        else:
            age = (time.time() - last) / 3600
            out["age_hours"] = round(age, 1)
            res.metric("backup.netbackup_age_hours", age)
            if age > max_age:
                res.add("CRITICAL", "NBU_BACKUP_OLD", "netbackup", "Last NetBackup image is %.0f hours old (limit %.0f)" % (age, max_age),
                        recommendation="Check the client's backup policy schedule and recent job errors on the master.")
    if ctx.runner.exists(NBU_BIN + "/bpclntcmd"):
        r = ctx.run([NBU_BIN + "/bpclntcmd", "-pn"], timeout=60)
        out["master_connectivity"] = r.ok
        if not r.ok:
            res.add("WARNING", "NBU_MASTER_UNREACHABLE", "netbackup", "NetBackup client cannot reach its master server",
                    evidence=r.brief_error(), recommendation="Check bp.conf SERVER entries, DNS and ports 1556/13724.")
    return out


def backupexec(ctx, res, ports):
    detected = ctx.runner.exists("/opt/VRTSralus") or ctx.runner.exists("/etc/init.d/VRTSralus.init")
    if not enabled(ctx, "backupexec", detected):
        return None
    ps = ctx.run(["ps", "-eo", "args"]).out
    running = "beremote" in ps
    listening = 10000 in set(p["port"] for p in ports)
    if not running:
        res.add("WARNING", "BE_AGENT_DOWN", "backupexec", "Backup Exec agent (beremote) is not running",
                recommendation="Start the agent: /etc/init.d/VRTSralus.init start")
    elif not listening:
        res.add("WARNING", "BE_AGENT_PORT", "backupexec", "Backup Exec agent is running but NDMP port 10000 is not listening")
    return {"running": running, "port_10000": listening}


def veeam(ctx, res):
    detected = ctx.has("veeamconfig")
    if not enabled(ctx, "veeam", detected):
        return None
    max_age = ctx.cfg.getfloat("backup", "veeam", "max_age_hours", 26)
    r = ctx.run(["veeamconfig", "session", "list"], timeout=60)
    last_ok, last_fail = None, None
    for line in r.lines():
        m = re.search(r"(Success|Warning|Failed)\s+(\d{4}-\d{2}-\d{2} \d{2}:\d{2})", line)
        if m:
            ts = time.mktime(time.strptime(m.group(2), "%Y-%m-%d %H:%M"))
            if m.group(1) == "Failed":
                last_fail = max(last_fail or 0, ts)
            else:
                last_ok = max(last_ok or 0, ts)
    if last_ok is None:
        res.add("WARNING", "VEEAM_NO_SUCCESS", "veeam", "No successful Veeam Agent session found", evidence=r.brief_error() if not r.ok else "")
    else:
        age = (time.time() - last_ok) / 3600
        res.metric("backup.veeam_age_hours", age)
        if age > max_age:
            res.add("CRITICAL", "VEEAM_BACKUP_OLD", "veeam", "Last successful Veeam backup is %.0f hours old" % age)
    if last_fail and (last_ok is None or last_fail > last_ok):
        res.add("WARNING", "VEEAM_LAST_FAILED", "veeam", "Most recent Veeam session failed")
    return {"last_success": last_ok, "last_failure": last_fail}


def directory_job(ctx, name, s):
    path = s.get("path", "")
    pattern = s.get("pattern", "*")
    newest, total, count = None, 0, 0
    newest_size = 0
    if not ctx.runner.isdir(path):
        return {"ok": False, "error": "directory %s not found or not mounted" % path}
    for root, dirs, files in os.walk(path):
        for f in files:
            if fnmatch.fnmatch(f, pattern):
                full = os.path.join(root, f)
                mt, sz = ctx.runner.mtime(full), ctx.runner.size(full) or 0
                count += 1
                total += sz
                if mt and (newest is None or mt > newest):
                    newest, newest_size = mt, sz
        if s.get("recursive", "yes").lower() not in ("yes", "true", "1"):
            break
    return {"ok": True, "newest": newest, "newest_size": newest_size, "files": count, "total_bytes": total}


def logfile_job(ctx, name, s):
    path = s.get("path", "")
    if not ctx.runner.exists(path):
        return {"ok": False, "error": "log file %s not found" % path}
    text = ctx.runner.tail(path, 2 * 1024 * 1024)
    succ = re.compile(s.get("success_pattern", r"(?i)completed successfully|backup (finished|completed)"))
    err = re.compile(s.get("error_pattern", r"(?i)\b(error|failed|failure)\b"))
    last_ok, errors = None, []
    for line in text.splitlines():
        if succ.search(line):
            last_ok = line
        elif err.search(line):
            errors.append(line[:300])
    return {"ok": True, "newest": ctx.runner.mtime(path) if last_ok else None, "last_success_line": last_ok,
            "errors": errors[-10:], "files": 1, "total_bytes": ctx.runner.size(path) or 0, "newest_size": 0}


def generic_jobs(ctx, res):
    rows = []
    default_age = ctx.t("backup", "default_max_age_hours", 26)
    for section in ctx.cfg.sections("backup", "job:"):
        name = section.split(":", 1)[1]
        s = ctx.cfg.section("backup", section)
        kind = s.get("type", "directory")
        max_age = to_float(s.get("max_age_hours"), default_age)
        min_size = to_float(s.get("min_size_mb"), 0) * 1024 * 1024
        info = logfile_job(ctx, name, s) if kind == "logfile" else directory_job(ctx, name, s)
        if not info["ok"]:
            rows.append([name, kind, s.get("path", ""), "-", "-", "-", "MISSING"])
            res.add("CRITICAL", "BACKUP_TARGET_MISSING", name, info["error"],
                    recommendation="Check that the backup destination is mounted and reachable.")
            continue
        age = (time.time() - info["newest"]) / 3600 if info["newest"] else None
        status = "OK"
        if age is None:
            status = "NO BACKUP"
            res.add("CRITICAL", "BACKUP_NONE_FOUND", name, "No backup matching '%s' found for job %s" % (s.get("pattern", s.get("success_pattern", "")), name))
        elif age > max_age:
            status = "STALE"
            res.add("CRITICAL", "BACKUP_STALE", name, "Newest backup for %s is %.1f hours old (limit %.0f h)" % (name, age, max_age),
                    recommendation="Check the backup job schedule/logs; RPO is not being met.")
        if age is not None and kind != "logfile" and min_size and info["newest_size"] < min_size:
            status = "TOO SMALL"
            res.add("WARNING", "BACKUP_TOO_SMALL", name, "Newest backup for %s is only %s" % (name, fmt_bytes(info["newest_size"])),
                    recommendation="A tiny backup file usually means the job failed early.")
        for e in info.get("errors", [])[-3:]:
            res.add("WARNING", "BACKUP_LOG_ERROR", name, "Error in backup log: %s" % e[:200])
        if age is not None:
            res.metric("backup.age_hours:" + name, age)
        rows.append([name, kind, s.get("path", ""), "%.1f h" % age if age is not None else "never",
                     info["files"], fmt_bytes(info["total_bytes"]), status])
    if rows:
        res.table("Configured backup jobs", ["Job", "Type", "Path", "Newest age", "Files", "Total size", "Status"], rows, kind="summary")
    return rows


def run(ctx, res):
    ports = ctx.shared.get("listening_ports") or listening_ports(ctx.runner)
    nbu = netbackup(ctx, res)
    be = backupexec(ctx, res, ports)
    vm = veeam(ctx, res)
    jobs = generic_jobs(ctx, res)
    tools = dict((t, ctx.has(t)) for t in ("rman", "restic", "borg", "duplicity", "bacula-fd", "bconsole", "rsnapshot", "rear", "veeamconfig", "dsmc"))
    cron = []
    for path in ["/etc/crontab"] + ctx.runner.glob("/etc/cron.d/*") + ctx.runner.glob("/var/spool/cron/*") + ctx.runner.glob("/var/spool/cron/crontabs/*"):
        for line in ctx.read(path).splitlines():
            if re.search(r"(?i)backup|rman|dump|restic|borg|rsync|tar ", line) and not line.strip().startswith("#"):
                cron.append([path, line.strip()[:200]])
    ps = ctx.run(["ps", "-eo", "pid,etime,args"]).out
    active = [l.strip()[:200] for l in ps.splitlines() if re.search(r"(?i)\b(rman|bpbkar|bpbkar32|beremote|veeamagent|restic|borg|duplicity|dsmc)\b", l)]
    res.data = {"netbackup": nbu, "backupexec": be, "veeam": vm, "jobs": jobs, "tools": tools, "cron": cron, "active_processes": active}
    products = [n for n, v in (("NetBackup", nbu), ("Backup Exec", be), ("Veeam", vm)) if v is not None]
    res.table("Backup software", ["Item", "Value"], [
        ["Products detected", ", ".join(products) or "none"],
        ["Tools present", ", ".join(t for t, v in tools.items() if v) or "none"],
        ["Configured jobs", len(jobs)], ["Backup cron entries", len(cron)],
        ["Backup processes running now", len(active)],
    ], kind="summary")
    if cron:
        res.table("Backup-related cron entries", ["File", "Entry"], cron)
    if active:
        res.table("Running backup processes", ["Process"], [[a] for a in active])
    if not products and not jobs:
        res.add("INFO", "BACKUP_NOT_CONFIGURED", "backup", "No backup product detected and no backup jobs configured",
                recommendation="Define [job:NAME] sections in backup.conf so backup freshness (RPO) is verified.")
    res.summary = "%s; %d configured job(s)" % (", ".join(products) or "no backup product detected", len(jobs))
