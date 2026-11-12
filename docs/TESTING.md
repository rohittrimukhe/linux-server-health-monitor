# Testing and production acceptance

## Automated test suite

`cd tests && python3 -m unittest discover -p 'test_*.py'` runs 104 tests:

| Test file | Covers |
|---|---|
| `test_storage.py` | df/inode parsing, per-mount overrides, read-only mounts, fstab entries not mounted, hung NFS, multipath (partial and total path loss), md RAID degraded, missing PV, LVM snapshot fill, df failure |
| `test_oracle.py` | SQL*Plus output parsing; tablespaces (UNDO/TEMP excluded), FRA, archive destination errors, redo and control files, process limits, blocking sessions, RMAN failure and backup age, archived-log backup age, invalid objects, failed jobs, PGA; alert log in 11g and 12c+ timestamp formats within the time window; instance down; Data Guard lag and MRP down on a standby; password sent over stdin and never on the command line; ASM ignored |
| `test_modules.py` | Services, network (bond, route, duplex, errors, DNS), KVM (domstats rates, crashed and autostart VMs, pool fill, old snapshots, overcommit, libvirt down), Cockpit, log classification, SSH brute force, syslog time window, security policy, NetBackup master and client, backup directory RPO, MegaCli, EDAC, Pacemaker, containers, real custom-check scripts (including refusing unsafe ones), real openssl certificate expiry, time sync and reboot, CPU/load/memory/swap, MySQL, MongoDB, SQL Server, PostgreSQL unreachable |
| `test_engine.py` | Scoring caps, skipped modules, history save, trend values, filesystem forecast (days to full), recurring issues, pruning, drift comparison, maintenance window, redaction, log timestamp formats |
| `test_reports_mail.py` | All 7 formats for executive, technical and full reports, with the optional libraries and with the built-in fallbacks; XLSX/PPTX re-opened with openpyxl and python-pptx; no secrets in any report; a live SMTP session through a local test server (attachments, size limit, format filter, critical recipients, BCC not in headers, delivery log); retry and back-off; permanent errors not retried; send policy; validation |
| `test_ops.py` | Scheduler: systemd units and cron rendering, validation, install and uninstall into temp dirs. Retention: delete, compress, archive, keep-latest, log trim, audit; space cleanup on low free space and size cap (priority order, minimum age, newest kept, warning when the target can't be met). Fleet: SSH collection, failures, agent drop directory, fleet report. Nagios and Prometheus output; run lock |
| `test_cli.py` | The real `healthcheck` launcher end to end: `run` in every format, a C-locale run, `nagios`, `agent`, history, baseline, maintenance, config backup/restore/set, schedule show, retention, bad input, lock busy (exit 75), fleet collection over a fake `ssh` that runs the real agent; scripted wizard steps (including rejecting invalid input) and a scripted menu session |
| `test_install.py` | `install.sh` → upgrade (config kept, rollback copy made) → `uninstall.sh` → `--purge`, in a sandbox |
| `test_live_postgres.py` | Starts a real PostgreSQL server and checks for lock waits, an inactive replication slot and long queries (skipped if PostgreSQL is not installed) |
| `test_compat.py` | Python 3.6/3.7 grammar; no APIs newer than 3.6; every text file opened as UTF-8; no state-changing commands (restart, kill, rm, virsh destroy, lvremove, ...) anywhere in the modules |

## Verified in development

- The full suite passed on **Python 3.13** (with ReportLab, openpyxl and python-pptx) and on **Python 3.8** (without them, so the built-in PDF/XLSX/PPTX writers were exercised). The CLI and installer tests also passed through the launcher on 3.8.
- A live run on Ubuntu 24.04 (no systemd in the container) found real conditions correctly: read-only and nearly full mounts, critical services not running, SELinux disabled.
- The PostgreSQL adapter ran against a real PostgreSQL 16 server with planted problems.
- PDF, XLSX and PPTX output from the built-in writers was converted with LibreOffice and checked visually. ReportLab PDFs and the HTML report were checked as rendered images, including at phone width.

## Verified against real software in the test container

| Area | Real software | Problems planted and detected |
|---|---|---|
| KVM | libvirt 10.0 with a custom `test:///` hypervisor | Crashed VM (CRITICAL), paused VM, storage pool 95% full (CRITICAL), libvirt unreachable |
| MySQL/MariaDB | MariaDB 10.11 server | Connections at 18 of 20 (CRITICAL), replication broken (CRITICAL; this found and fixed a parsing bug) |
| PostgreSQL | PostgreSQL 16 server | Lock waits, idle-in-transaction session, long queries, inactive replication slot |
| Cockpit | cockpit-ws 314 with its self-signed certificate | HTTPS probe 200, unencrypted access flagged, self-signed certificate noted |
| Storage | Real ext4 filesystems on loop devices, and LVM PVs/VGs | 97% full, inodes at 100%, read-only mount, fstab entry not mounted (all CRITICAL) |
| Exclusions | Real cockpit-ws and read-only mounts | Self-signed certificate and `/opt/*` read-only findings excluded and listed in the report; expired rule raised a warning; the watcher ignored excluded problems |
| Cockpit page | cockpit-ws 314 with a local session | Page loaded with no browser errors and showed live results, real-time problems and report buttons; *Run health check now* ran a check and the page refreshed itself |
| Fleet dashboard | Built from 7 server results + 1 unreachable, served over HTTP | Status counts, filters and phone layout correct; path traversal (404) and directory listing (403) refused |
| Inventory | MariaDB 10.11 running | Hardware/OS/877 packages/disks/network collected in 1.8 s; MariaDB server version 10.11.14 and owner `mysql` detected |
| Real-time monitoring | Continuous `watch run` with a local SMTP server | Alert email after 2 checks, recovery email when the missing process started; nice 19, idle I/O, 170 ms CPU over 52 s, 30 MB memory |

LVM logical volumes and snapshots, and md RAID, could not be created because
the container's kernel lacks device-mapper targets and md support. Those
checks are covered by unit tests with captured output.

During development, `test_watch` failed once and then passed 75 times in a
row. The cause was not found. If it fails in your CI, please report the output.

## Not yet verified (needs your environment)

These need real infrastructure that a development container does not have.
Parsers are based on the documented output formats and covered by fixtures,
but must be confirmed on site:

- **Python 3.6 itself** (RHEL/OL 7). The code passes static 3.6 checks and runs on 3.8, but has not been run on a 3.6 interpreter.
- **Oracle Database** 11g R2 and 19c against a live instance, including Data Guard and RMAN catalogs.
- **MySQL/MariaDB, MongoDB and SQL Server** against live servers.
- **Production KVM hosts, multipath/SAN, hardware RAID CLIs, IPMI, Pacemaker, NetBackup, Backup Exec, Veeam**.
- **systemd timers** on a real systemd host. Unit files were generated and checked, but never run by systemd in development.
- **Your SMTP relay** (TLS and authentication).

## Production acceptance checklist

Run these on a non-production RHEL/OL 7, 8 and 9 server before rolling out:

1. `sudo ./install.sh --check`, then `sudo ./install.sh`. `healthcheck doctor` should show no CRITICAL items.
2. `sudo healthcheck setup`: company, thresholds, critical services and SMTP. `healthcheck email-test` arrives.
3. `sudo healthcheck run --formats all --report-type full`. Open each report; compare filesystem, LVM, multipath and service figures with `df -h`, `lvs`, `multipath -ll` and `systemctl --failed`.
4. **Oracle**: `healthcheck check database --details`. Compare tablespaces, FRA, RMAN and Data Guard against your own DBA queries; check the alert-log errors match `adrci`.
5. **KVM**: `healthcheck check kvm --details` against `virsh list --all`, `virsh pool-list --details` and `virsh snapshot-list`.
6. **Cockpit**: `healthcheck check cockpit`.
7. Plant safe test conditions and confirm each is detected: fill a test filesystem past 90%, stop a non-critical test service listed as critical, unplug one multipath path in a maintenance window, create a VM snapshot.
8. `healthcheck schedule install`, then `systemctl list-timers 'lshme-*'`. Wait for one run; confirm the report folder and email arrive.
9. `healthcheck retention preview`, then `apply` on test data; check `audit.log`.
10. **Fleet**: set up SSH keys, two servers in `servers.conf`, then `healthcheck fleet run`.
11. Leave it running on a schedule for a week. Review `error-*.log`, and confirm trends and forecasts appear.
