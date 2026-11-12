# Linux Server Health Monitor Enterprise (LSHME)

![LRS Services](lshme/reports/lrs_logo.png)

**© 2026 LRS Services Pvt Ltd, Mumbai. All rights reserved.** See [LICENSE](LICENSE).

Read-only health monitoring, reporting and alerting for Linux servers. LSHME is
a menu-driven application with a full command-line interface. It checks the
operating system, storage, services, network, hardware, KVM, Cockpit,
databases (Oracle 11g R2 to 23ai, MySQL/MariaDB, PostgreSQL, MongoDB and
SQL Server), backups, logs, security and certificates. Each check produces
findings with recommended actions, and the overall result is a health score.
LSHME writes reports in HTML, PDF, XLSX, PPTX, TXT, CSV and JSON, emails them
over SMTP, runs daily, weekly, monthly or custom schedules, applies retention
rules (including space-based cleanup of old reports), keeps history with trends and capacity forecasts, and can monitor a
whole fleet of servers from one central host.

```
 ==========================================================================
   Linux Server Health Monitor Enterprise  v1.2.0
   © 2026 LRS Services Pvt Ltd, Mumbai. All rights reserved.
   Host: db01   2026-11-12 09:00
 ==========================================================================
  Load 0.42 / 0.51 / 0.48   Memory 63%   Fullest filesystem 81%

  [ 1] Complete health check (all mod... [11] Scheduling & automation
  [ 2] Check a single module             [12] Report & log retention
  [ 3] Storage & mount points            [13] History & trends
  [ 4] Services & processes              [14] Baseline & configuration drift
  [ 5] Database health                   [15] Maintenance mode
  [ 6] KVM virtual machines              [16] Multi-server (fleet)
  [ 7] Cockpit web console               [17] Configuration & setup wizard
  [ 8] Network & connectivity            [18] Self-diagnostics
  [ 9] Generate reports (choose forma... [19] View logs
  [10] SMTP & email settings             [20] Real-time monitoring & alerts
                                         [ 0] Exit
```

## Quick start

```bash
sudo ./install.sh              # installs to /opt/lshme, config in /etc/lshme, command 'healthcheck'
sudo healthcheck setup         # guided setup: identity, thresholds, services, databases, SMTP, schedules, retention
sudo healthcheck               # interactive menu
sudo healthcheck run --formats all --email
sudo healthcheck schedule install
sudo healthcheck watch install   # real-time monitoring with alerts
```

Requirements: Python 3.6 or newer, which RHEL/Oracle Linux 7 provide through
`yum install python3`; 8 and 9 include it already. The core uses only the
standard library. ReportLab, openpyxl and python-pptx add charts and richer
formatting, and `install.sh --with-optional` installs them. Without them the
built-in PDF, XLSX and PPTX writers are used, so every format still works.

## What it checks

| Area | Highlights |
|---|---|
| Operating system | Distribution and kernel, uptime, NTP/chrony sync, pending reboot |
| CPU & memory | Utilisation, I/O wait and steal, load per CPU, memory and swap, top processes |
| Storage | Every filesystem with per-mount thresholds, inodes, read-only mounts, fstab entries not mounted, hung NFS/CIFS mounts, LVM (PV/VG/LV, snapshot and thin-pool fill, missing PVs), multipath path failures, md RAID, SMART, disk latency and utilisation |
| Services | Critical-service policy, failed units, restart loops, expected listening ports, required processes |
| Network | Interfaces, errors and drops, duplex, bonding and teaming, default route, gateway, ping and TCP targets, DNS, firewall |
| Hardware | Inventory, temperatures, ECC/EDAC errors, IPMI sensors, hardware RAID (storcli, perccli, MegaCli, ssacli) |
| KVM / libvirt | Every VM's state, CPU %, memory, disk and network throughput, autostart, snapshot age, storage-pool capacity, virtual networks, vCPU and memory overcommit |
| Cockpit | Packages, socket and service, port, HTTPS probe, certificate expiry, unencrypted access, root login |
| Oracle 11g R2 to 23ai | Instance and open mode, tablespaces (percent of max with autoextend), datafiles, FRA, archive generation and destinations, redo and control files, processes and sessions, blocking and long sessions, waits, SGA/PGA, RMAN failures and backup age, Data Guard lag and MRP, invalid objects, failed jobs, expiring accounts, alert-log ORA- errors, listener |
| MySQL / MariaDB, PostgreSQL, MongoDB, SQL Server | Availability, connections, replication and lag, sizes, long queries, lock waits, XID wraparound, WAL archiving, replica-set health, database states, backup freshness, failed agent jobs |
| Backup | NetBackup (master job status, client image age, master connectivity), Backup Exec agent, Veeam Agent, backup directories and log files checked against an RPO |
| Logs | OOM kills, I/O and filesystem errors, hardware errors, multipath, NFS, kernel hangs, segfaults, service failures, SSH brute force with top source IPs, root logins, sudo, SELinux denials, firewall drops, cron failures, reboots; over 1 to N days |
| Security | SELinux, firewall, effective SSH policy, extra UID 0 accounts, empty passwords, auditd, pending security updates, last patch date, NOPASSWD sudo |
| Also | TLS certificates (files and endpoints), Docker/Podman, Pacemaker/Corosync, HTTP endpoints, postfix queue, custom Nagios-style checks, configuration drift against a baseline, trend comparison, filesystem fill forecast |

See [docs/MODULES.md](docs/MODULES.md) for every module and all finding codes.

## Real-time monitoring

`healthcheck watch install` starts a lightweight watcher, using systemd or cron
every minute on hosts without systemd:

- **Every minute:** checks CPU, load, memory, swap, every filesystem (space,
  inodes, read-only), hung NFS mounts, critical services, expected ports and
  required processes. These read `/proc` directly, with no subprocesses apart
  from one `systemctl` call.
- **Rotation:** heavier modules (database, KVM, services, Cockpit, network,
  backup, cluster, certificates) run one at a time, each every 15 minutes.
- **Alerts:** sent by email, syslog and/or a webhook after 2 consecutive bad
  checks (so short spikes don't trigger), again on escalation, as a reminder
  every 60 minutes, and once on recovery. Alerts are suppressed during
  maintenance windows.

## Exclusions

Accepted findings, such as Cockpit's self-signed certificate, can be excluded
so they no longer affect the score or trigger alerts. They stay visible in
reports with the reason and an optional expiry date:

```bash
healthcheck exclude add cockpit-self-signed --code COCKPIT_SELF_SIGNED --module cockpit --reason "Internal console"
```

## Resource use

LSHME is built to stay out of the way on production servers:

| Run | Typical cost (measured) |
|---|---|
| Real-time check (one tick) | about 2 ms of CPU inside the running watcher; about 30 MB resident; 170 ms of CPU per minute of checks including startup |
| Full check with HTML and JSON reports | about 2.5 s; 0.4 s of CPU |
| Full check with all 7 formats | about 3.3 s; 1.5 s of CPU; under 80 MB of memory |

How it keeps the cost down:
- Every run lowers its own priority (nice 10, and 19 for the watcher) and uses the idle I/O class.
- systemd units cap CPU and memory: the watcher at 5% CPU and 128 MB, scheduled reports at 50% CPU and 512 MB.
- Every command has a timeout, and hung network mounts are probed in a helper thread.
- Runs are locked so they never overlap, and the watcher restarts itself if its memory grows past 100 MB.

## Safety

LSHME only reads. It never restarts services, changes databases, LVM, KVM or
the firewall, deletes archive logs or backups, or repairs filesystems. Its
only writes outside its own directories are the schedule files it manages
(`lshme-*.timer`/`.service` or `/etc/cron.d/lshme`), and only when you ask for
them. Database checks are read-only SQL. Passwords stay in `credentials.conf`
(mode 600), are passed over stdin or environment variables (never on the
command line), and are masked in every report.

## Documentation

- [User guide](docs/USER_GUIDE.md): installation, configuration, the menu and CLI, reports, email, schedules, retention, fleet, database setup, troubleshooting
- [Module reference](docs/MODULES.md): checks and finding codes
- [Testing & acceptance](docs/TESTING.md): what has been verified and the production acceptance checklist
- [Changelog](CHANGELOG.md)

## Running the tests

```bash
cd tests && python3 -m unittest discover -p 'test_*.py'
```
