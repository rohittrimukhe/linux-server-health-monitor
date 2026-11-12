# Linux Server Health Monitor Enterprise (LSHME)

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
   Linux Server Health Monitor Enterprise  v1.1.0
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
  [10] SMTP & email settings             [ 0] Exit
```

## Quick start

```bash
sudo ./install.sh              # installs to /opt/lshme, config in /etc/lshme, command 'healthcheck'
sudo healthcheck setup         # guided setup: identity, thresholds, services, databases, SMTP, schedules, retention
sudo healthcheck               # interactive menu
sudo healthcheck run --formats all --email
sudo healthcheck schedule install
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

See [docs/MODULES.md](docs/MODULES.md) for every module and all 214 finding codes.

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
