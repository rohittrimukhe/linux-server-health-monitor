# Changelog

## 1.1.0

A complete re-implementation of the v1.0.0 prototype package, which was a
single 570-line script. The architecture is now modular: 17 monitoring
modules, an engine, report renderers, delivery, operations and UI layers.

Fixed from the prototype:
- `installer.sh` failed on a clean install. It ran `chmod` on directories that did not exist, under `set -e`.
- Scheduled runs never sent email, because the cron line had no `--email`.
- History was always empty on Python 3.6 (RHEL/OL 7), because of `datetime.fromisoformat`.
- The Oracle check ran SQL*Plus twice; the first run had no input and could hang for 60 seconds.
- Collected data was not analysed: logs, KVM, Cockpit, network, backups and security produced no findings.
- Oracle checks were limited to name/open mode/role. They now cover tablespaces, FRA, archive logs, redo, control files, sessions, locks, waits, memory, RMAN, Data Guard, invalid objects, jobs, accounts, the alert log and the listener.
- Retention was a single period and could delete the newest report.
- There was no run locking, systemd timers were not installed, and the wizard only configured thresholds.
- Under a C locale on Python 3.6 (typical for cron on RHEL 7), non-ASCII output crashed the program.

New:
- Deep checks for KVM, Cockpit, hardware RAID, multipath, md RAID, SMART, NFS, bonding, NetBackup, Backup Exec, Veeam, Pacemaker, containers, certificates, HTTP endpoints and the postfix queue.
- MySQL/MariaDB, PostgreSQL, MongoDB and SQL Server adapters.
- Health scoring with critical caps, history, trend comparison, filesystem fill forecast, recurring issues, configuration drift and maintenance windows.
- Seven report formats in three report types, with built-in PDF/XLSX/PPTX writers so no optional library is required.
- SMTP with retries, critical recipients, send policy, attachment limits and a delivery log.
- systemd or cron scheduling with per-schedule modules, period, formats and recipients.
- Retention with compression, archiving, keep-latest and an audit trail, plus space-based cleanup (free-space target and size cap) before and after every run.
- Central fleet monitoring over SSH or an agent drop directory; Nagios and Prometheus outputs.
- A full setup wizard, a DOS-style menu, self-diagnostics, config backup and restore, and installer upgrade/rollback.
- 82 automated tests.
