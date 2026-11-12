# Changelog

## 1.2.0

- **Real-time monitoring** (`healthcheck watch`).
  - Probes every minute read `/proc` and `statvfs` directly; heavier modules run one at a time in rotation.
  - Alerts go out by email, syslog and webhook, with confirmation (no flapping), escalation, reminders and recovery messages.
  - Runs as a systemd service limited to 5% CPU and 128 MB, or from cron.
- **Exclusions** (`healthcheck exclude`, menu option 21, `exclusions.conf`).
  - Accepted findings (for example `COCKPIT_SELF_SIGNED`) are removed from the score, status and alerts, but still listed in reports with the rule, reason, owner and expiry.
  - Rules support code wildcards, component and host globs, and a module filter.
  - A reason is required, and expired or invalid rules raise a warning.
- **Lower resource impact.**
  - Every run lowers its own CPU and I/O priority.
  - Scheduled units cap CPU and memory.
  - The watcher restarts itself if its memory grows.
- **LRS Services branding.**
  - The logo appears in HTML, PDF, XLSX and PPTX reports.
  - "© 2026 LRS Services Pvt Ltd, Mumbai. All rights reserved." appears in every report, email, the console, and every source file; a LICENSE file is added.
- **Fixes found by testing against real software.**
  - MariaDB/MySQL replication status was not read (the `-N` option dropped the column names), so stopped replication went unreported.
  - The KVM module raised CRITICAL on hosts with only the `virsh` client and no libvirt daemon; it now skips.
  - PostgreSQL now raises a finding for waiting lock requests.
  - The Cockpit summary now shows its state on hosts without systemd.

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
