"""Log analysis over the configured window (journald or classic syslog files).

Lines are classified by pattern, counted, and turned into findings with sample
evidence, rather than dumped raw.
"""
import re
import time
from collections import Counter

from lshme.core.util import parse_log_time

NAME = "logs"
TITLE = "Logs & Events"
WEIGHT = 10

# (category, severity, regex, description, recommendation, min_count)
# Order matters: the first matching pattern classifies a line, so specific patterns come first.
PATTERNS = [
    ("oom", "CRITICAL", r"Out of memory|oom-kill|invoked oom-killer|Killed process \d+", "OOM killer events",
     "Processes were killed for lack of memory; review memory sizing and the top consumers.", 1),
    ("fs_error", "CRITICAL", r"EXT[234]-fs error|XFS \(.*\): (Corruption|metadata I/O error|Log I/O Error)|Remounting filesystem read-only|xfs_force_shutdown",
     "Filesystem errors", "Schedule a filesystem check in maintenance; verify backups first.", 1),
    ("io_error", "CRITICAL", r"I/O error|blk_update_request|Buffer I/O error|end_request: I/O error|critical medium error",
     "Disk I/O errors", "Check the disk/LUN, multipath and SAN fabric; I/O errors can corrupt data.", 1),
    ("hardware", "CRITICAL", r"Machine check|mce: \[Hardware Error\]|Hardware Error|EDAC .*(UE|CE) |PCIe Bus Error",
     "Hardware errors", "Engage the hardware vendor; check IPMI/iLO/iDRAC event logs.", 1),
    ("multipath", "CRITICAL", r"remaining active paths: 0|multipathd.*(failing path|checker failed)|rport-.*blocked",
     "Multipath/SAN path failures", "Check FC/iSCSI paths, HBAs and SAN switches.", 1),
    ("nfs", "WARNING", r"nfs: server \S+ not responding|NFS: .*timed out|CIFS VFS: .*(failed|reconnect)",
     "NFS/CIFS server not responding", "Check connectivity to the file server.", 1),
    ("kernel", "WARNING", r"Kernel panic|Call Trace:|BUG: |soft lockup|hung_task|blocked for more than \d+ seconds|kernel: WARNING:",
     "Kernel warnings, lockups or hung tasks", "Review kernel messages; hung tasks often point to storage latency.", 1),
    ("segfault", "WARNING", r"segfault at|general protection fault|core dumped", "Application crashes (segfault/core dump)",
     "Identify the crashing binary and check for patches.", 1),
    ("service_fail", "WARNING", r"Failed to start |entered failed state|failed with result|Main process exited, code=(exited|killed), status=(?!0)",
     "Service start failures", "Check 'systemctl status' and the unit's journal.", 1),
    ("ssh_fail", "WARNING", r"Failed password for|authentication failure;|Invalid user \S+ from|Connection closed by authenticating user|maximum authentication attempts exceeded",
     "Failed SSH / authentication attempts", "Investigate source IPs; consider fail2ban, firewall restrictions or key-only auth.", 0),
    ("root_login", "INFO", r"Accepted \S+ for root from|session opened for user root by \(uid=0\)|ROOT LOGIN",
     "Direct root logins", "Prefer named accounts with sudo for auditability.", 1),
    ("sudo", "INFO", r"sudo(\[\d+\])?: +\S+ : .*COMMAND=", "sudo commands executed", "", 1),
    ("selinux", "WARNING", r"avc: +denied|SELinux is preventing", "SELinux denials",
     "Review with 'ausearch -m avc'; fix labels or policy rather than disabling SELinux.", 1),
    ("firewall", "INFO", r"(FINAL_REJECT|_DROP|REJECT|DROP|UFW BLOCK).*SRC=", "Firewall dropped packets", "", 1),
    ("cron_fail", "WARNING", r"CROND?\[\d+\]: .*(ERROR|FAILED|ORPHAN)|cron.*(error|failed)|\(CRON\) error",
     "Failed scheduled jobs", "Check the cron job's output/mail and script exit codes.", 1),
    ("ntp", "WARNING", r"chronyd.*(Can't synchronise|No suitable source|System clock wrong)|ntpd.*(no servers can be used|time reset)",
     "Time synchronisation problems", "Check NTP server reachability.", 1),
]
COMPILED = [(c, s, re.compile(rx, re.I), d, rec, mn) for c, s, rx, d, rec, mn in PATTERNS]
IP_RE = re.compile(r"from (\d{1,3}(?:\.\d{1,3}){3}|[0-9a-f:]{3,})", re.I)


def journal_lines(ctx, days, max_lines):
    r = ctx.run(["journalctl", "--since", "%d days ago" % days, "--no-pager", "-q", "-o", "short-iso",
                 "-n", str(max_lines)], timeout=180)
    if not r.ok and not r.out:
        return None, r.brief_error()
    return r.out.splitlines(), "journalctl"


def file_lines(ctx, days, max_lines, files):
    cutoff = time.time() - days * 86400
    lines, used = [], []
    for path in files:
        if not ctx.runner.exists(path):
            continue
        used.append(path)
        last_ts = None
        for line in ctx.runner.tail(path).splitlines():
            ts = parse_log_time(line)
            if ts is not None:
                last_ts = ts
            if (last_ts or 0) >= cutoff:
                lines.append(line)
    return lines[-max_lines:], "files: " + ", ".join(used)


def reboots(ctx, days):
    r = ctx.run(["last", "-x", "-F", "reboot", "shutdown"], timeout=30)
    cutoff = time.time() - days * 86400
    out = []
    for line in r.lines():
        m = re.search(r"(\w{3} \w{3} +\d+ \d\d:\d\d:\d\d \d{4})", line)
        if line.startswith(("reboot", "shutdown")) and m:
            try:
                ts = time.mktime(time.strptime(m.group(1), "%a %b %d %H:%M:%S %Y"))
            except ValueError:
                continue
            if ts >= cutoff:
                out.append((line.split()[0], time.strftime("%Y-%m-%d %H:%M", time.localtime(ts))))
    return out


def run(ctx, res):
    days = ctx.days
    max_lines = ctx.cfg.getint("main", "logs", "max_lines", 200000)
    source = ""
    lines = None
    if ctx.has("journalctl") and ctx.runner.isdir("/run/systemd/system"):
        lines, source = journal_lines(ctx, days, max_lines)
    if lines is None:
        lines, source = file_lines(ctx, days, max_lines, ["/var/log/messages", "/var/log/syslog", "/var/log/secure",
                                                          "/var/log/auth.log", "/var/log/cron", "/var/log/kern.log"])
    # journald normally contains auth logs, but some systems keep secure only in files
    if source == "journalctl" and ctx.runner.exists("/var/log/secure") and ctx.runner.is_root():
        extra, _ = file_lines(ctx, days, max_lines, ["/var/log/secure"])
        lines.extend(extra)
    extra_files = ctx.cfg.getlist("main", "logs", "extra_files", "")
    custom = []
    for spec in ctx.cfg.getlist("main", "logs", "extra_patterns", ""):
        # format: category|SEVERITY|regex
        parts = spec.split("|", 2)
        if len(parts) == 3:
            try:
                custom.append((parts[0], parts[1].upper(), re.compile(parts[2], re.I), "Custom pattern %s" % parts[0], "", 1))
            except re.error:
                res.error("invalid custom log pattern: %s" % spec)
    app_lines = []
    if extra_files:
        app_lines, _ = file_lines(ctx, days, max_lines, extra_files)

    counts = Counter()
    samples = {}
    ips = Counter()
    for line in lines + app_lines:
        for cat, sev, rx, desc, rec, mn in COMPILED + custom:
            if rx.search(line):
                counts[cat] += 1
                samples.setdefault(cat, [])
                if len(samples[cat]) < 5:
                    samples[cat].append(line[:400])
                if cat == "ssh_fail":
                    m = IP_RE.search(line)
                    if m:
                        ips[m.group(1)] += 1
                break
    ctx.shared["log_counts"] = dict(counts)

    ssh_warn, ssh_crit = ctx.t("logs", "ssh_fail_warning", 20), ctx.t("logs", "ssh_fail_critical", 200)
    rows = []
    for cat, sev, rx, desc, rec, mn in COMPILED + custom:
        n = counts.get(cat, 0)
        rows.append([desc, n])
        res.metric("logs.count:" + cat, n)
        if not n:
            continue
        evidence = "\n".join(samples.get(cat, []))
        if cat == "ssh_fail":
            if n >= ssh_crit:
                sev = "CRITICAL"
            elif n >= ssh_warn:
                sev = "WARNING"
            else:
                continue
            top = ", ".join("%s (%d)" % kv for kv in ips.most_common(5))
            res.add(sev, "LOG_SSH_FAILURES", "authentication", "%d failed authentication attempts in %d day(s); top sources: %s" % (n, days, top or "n/a"),
                    evidence=evidence, recommendation=rec)
            continue
        if n >= mn:
            res.add(sev, "LOG_" + cat.upper(), cat, "%s: %d occurrence(s) in the last %d day(s)" % (desc, n, days),
                    evidence=evidence, recommendation=rec)
    boots = reboots(ctx, days)
    if boots:
        res.add("INFO", "LOG_REBOOTS", "reboot", "%d reboot/shutdown event(s) in the last %d day(s)" % (len(boots), days),
                evidence="\n".join("%s %s" % b for b in boots))
    res.table("Log event summary (last %d day%s)" % (days, "s" if days > 1 else ""), ["Event", "Count"], rows, kind="summary")
    if ips:
        res.table("Top failed-login sources", ["Source", "Attempts"], [list(x) for x in ips.most_common(15)])
    if boots:
        res.table("Reboots and shutdowns", ["Event", "Time"], [list(b) for b in boots])
    for cat in ("oom", "io_error", "fs_error", "kernel", "service_fail", "selinux"):
        if samples.get(cat):
            res.table("Sample: %s" % cat, ["Log line"], [[s] for s in samples[cat]])
    res.data = {"source": source, "lines_analysed": len(lines) + len(app_lines), "window_days": days,
                "counts": dict(counts), "ssh_sources": dict(ips.most_common(20)), "reboots": boots}
    res.summary = "%d log lines analysed from %s over %d day(s)" % (len(lines) + len(app_lines), source.split(":")[0], days)
    if not lines:
        res.add("UNKNOWN", "LOGS_UNAVAILABLE", "logs", "No log lines could be read (%s)" % source,
                recommendation="Run as root so the journal and /var/log files are readable.")
