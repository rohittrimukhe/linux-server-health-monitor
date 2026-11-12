#!/usr/bin/env python3
# Linux Server Health Monitor Enterprise
# Copyright (c) 2026 LRS Services Pvt Ltd, Mumbai. All rights reserved.
"""Regenerate config/*.conf templates from the built-in defaults (keeps them in sync).

Usage: python3 packaging/gen_config.py
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from lshme.core.config import CONFIG_FILES, DEFAULTS  # noqa: E402

HEADERS = {
    "main": "General settings, module selection, security policy, log/network/KVM/Cockpit options and integrations.",
    "thresholds": "Warning/critical thresholds. Percent unless the key says otherwise.\n"
                  "[disk_overrides] sets per-mount thresholds:  /archive = 90,97",
    "services": "Critical services (CRITICAL when not active), expected listening ports, required processes.",
    "database": "Database instances. Running Oracle/PostgreSQL/MySQL instances are auto-discovered when\n"
                "LSHME runs as root; add sections only to override or to monitor remote/password-based instances.\n"
                "Passwords never go here: put them in credentials.conf under the same section name.",
    "smtp": "Email delivery. The SMTP password goes in credentials.conf [smtp] password = ...\n"
            "send_policy: always | issues (warning/critical only) | critical | never",
    "reports": "Report defaults. formats: html,pdf,xlsx,pptx,txt,csv,json or all. report_type: executive | technical | full",
    "schedule": "Schedules. Each [schedule:NAME] runs with its own modules, period (days), formats, type and recipients.\n"
                "frequency: hourly | daily | weekly | monthly | custom (calendar = systemd OnCalendar, or cron with backend=cron)\n"
                "Apply changes with: healthcheck schedule install",
    "retention": "Retention per report category, logs, history and config backups, plus space-based cleanup.\n"
                 "Space cleanup order: adhoc, daily, custom, weekly, fleet, monthly (oldest first); the newest report per\n"
                 "host/schedule is always kept. Preview: healthcheck retention preview | healthcheck retention space",
    "servers": "Central multi-server monitoring. Each [server:NAME] is reached over SSH with key authentication.",
    "backup": "Backup verification. Products are auto-detected (enabled = auto). Add [job:NAME] sections to verify\n"
              "backup files/directories or backup log files against a maximum age (RPO).",
    "certificates": "TLS certificate expiry checks. files / scan_dirs / endpoints (host:port), comma-separated.",
    "custom": "Custom checks using the Nagios plugin convention (exit 0 OK, 1 WARNING, 2 CRITICAL, 3 UNKNOWN).\n"
              "Scripts must be owned by root and not writable by group/others.",
    "exclusions": "Exclusions: accepted findings that should not affect the score or raise alerts.\n"
                  "Excluded findings are still listed in reports with the rule and reason. Expired rules stop applying.\n"
                  "Manage with: healthcheck exclude list | add | remove   (or menu option 21)",
    "credentials": "SECRETS - keep this file mode 600, owned by root. Section names match the other files.",
}

EXAMPLES = {
    "thresholds": "\n# [disk_overrides]\n# /archive = 90,97\n# /u01 = 85,92\n",
    "services": "\n# [ports]\n# oracle-listener = 1521\n# cockpit = 9090\n#\n# [processes]\n# oracle-pmon = ora_pmon_\n# app = java.*myapp\n",
    "database": ("\n# --- Oracle (11g R2 .. 23ai). OS authentication as the instance owner:\n"
                 "# [oracle:ORCL]\n# sid = ORCL\n# oracle_home = /u01/app/oracle/product/11.2.0/dbhome_1\n# os_user = oracle\n"
                 "# listener = LISTENER\n# backup_max_age_hours = 26\n#\n"
                 "# --- Oracle with a monitoring account (password in credentials.conf [oracle:PROD]):\n"
                 "# [oracle:PROD]\n# oracle_home = /u01/app/oracle/product/19.0.0/dbhome_1\n# username = lshme_monitor\n"
                 "# connect_identifier = //dbhost:1521/PROD\n#   grant: CREATE SESSION, SELECT_CATALOG_ROLE (11g) or SELECT ANY DICTIONARY\n#\n"
                 "# [mysql:main]\n# defaults_file = /root/.my.cnf\n#\n"
                 "# [postgresql:main]\n# os_user = postgres\n# port = 5432\n#\n"
                 "# [mongodb:main]\n# uri = mongodb://127.0.0.1:27017/admin\n# username = monitor\n#\n"
                 "# [sqlserver:main]\n# server = localhost\n# username = lshme_monitor\n"),
    "schedule": ("\n# [schedule:hourly-db]\n# enabled = yes\n# frequency = hourly\n# time = 00:15\n# modules = database,storage\n"
                 "# days = 1\n# formats = html,json\n# email = yes\n# send_policy = issues\n# to = dba-team@example.com\n"),
    "servers": ("\n# [server:db01]\n# host = 10.0.0.11\n# user = root\n# group = Production\n# environment = Production\n#\n"
                "# [server:dr-db01]\n# host = 10.1.0.11\n# group = DR\n# environment = DR\n"),
    "backup": ("\n# [job:oracle-export]\n# type = directory\n# path = /backup/exports\n# pattern = *.dmp*\n# max_age_hours = 26\n# min_size_mb = 100\n#\n"
               "# [job:rsync-nightly]\n# type = logfile\n# path = /var/log/rsync-backup.log\n# success_pattern = (?i)backup completed\n# max_age_hours = 26\n"),
    "custom": "\n# [check:app-health]\n# command = /usr/local/lib/lshme-checks/check_app.sh --warn 80\n# timeout = 30\n# recommendation = Restart the app via the runbook\n",
    "exclusions": ("\n# Cockpit uses its self-signed certificate on purpose:\n# [exclude:cockpit-self-signed]\n# code = COCKPIT_SELF_SIGNED\n"
                   "# module = cockpit\n# reason = Internal admin console; CA-signed certificate not required\n# owner = infra-team\n#\n"
                   "# A planned temporary condition with an expiry date:\n# [exclude:archive-full-until-migration]\n# code = DISK_USAGE\n"
                   "# component = /archive\n# reason = Archive migration in progress (CHG-1234)\n# until = 2026-12-31\n#\n"
                   "# Wildcards: every INFO-level Oracle invalid-object note on a dev host\n# [exclude:dev-invalid-objects]\n"
                   "# code = ORACLE_INVALID_*\n# hosts = dev-*\n# reason = Development databases\n"),
    "credentials": "\n# [smtp]\n# password =\n#\n# [oracle:PROD]\n# password =\n#\n# [mongodb:main]\n# password =\n",
    "main": "",
}

COMMENTS = {
    ("main", "modules", "enabled"): "all, or a comma list (system,cpu_memory,storage,services,network,hardware,kvm,cockpit,database,backup,logs,security,certificates,containers,cluster,applications,custom)",
    ("main", "logs", "extra_patterns"): "category|SEVERITY|regex entries, comma-separated",
    ("main", "network", "tcp_checks"): "host:port entries, comma-separated",
    ("main", "applications", "http_checks"): "url|expected_status|required_text entries, comma-separated",
    ("main", "integrations", "prometheus_textfile"): "e.g. /var/lib/node_exporter/textfile_collector/lshme.prom",
    ("main", "integrations", "agent_dropdir"): "also write each run as <host>.json here (for central import)",
    ("retention", "retention", "archive_dir"): "move expired reports here instead of deleting them",
    ("retention", "retention", "min_free_percent"): "space cleanup starts when the report filesystem has less free space than this",
    ("retention", "retention", "free_target_percent"): "...and deletes the least valuable old reports until this much is free",
    ("retention", "retention", "max_report_dir_gb"): "size cap for the report directory in GB (0 = no cap)",
    ("retention", "retention", "space_min_keep_days"): "space cleanup never deletes reports newer than this (days)",
    ("retention", "retention", "space_cleanup"): "yes = also delete reports for space (before every run and on 'retention apply')",
}


def main():
    out_dir = os.path.join(ROOT, "config")
    os.makedirs(out_dir, exist_ok=True)
    for name in CONFIG_FILES:
        lines = ["# LSHME %s.conf" % name]
        lines += ["# " + l for l in HEADERS.get(name, "").splitlines()]
        lines.append("")
        for section, keys in DEFAULTS.get(name, {}).items():
            lines.append("[%s]" % section)
            for k, v in keys.items():
                c = COMMENTS.get((name, section, k))
                if c:
                    lines.append("# " + c)
                lines.append("%s = %s" % (k, v))
            lines.append("")
        lines.append(EXAMPLES.get(name, "").rstrip("\n"))
        with open(os.path.join(out_dir, name + ".conf"), "w") as f:
            f.write("\n".join(lines).rstrip("\n") + "\n")
    print("wrote %d templates to %s" % (len(CONFIG_FILES), out_dir))


if __name__ == "__main__":
    main()
