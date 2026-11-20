# Changelog

## 1.4.0

- **New report types: daily, weekly, monthly.** A short, colour-coded checklist report with a plain-English summary, status, CPU (every core), memory, network (every adapter, sent/received/total), filesystems, databases and folders with growth, and the top 10 programs and services by CPU, memory, network and connections. Weekly and monthly add growth by date and a comparison with the previous period. Monthly comes as a summary and a comprehensive report (inventory, roles, services, open ports, installed software). HTML with hover charts, PDF (with the LRS logo as a watermark), Excel, CSV. Defaults: daily Monday to Saturday 09:00, weekly Sunday 12:00, monthly day 1 at 10:00.
- **Titles** read "Title (server, IP address, role)"; the role is added only when found.
- **Capacity forecast** (when each filesystem fills, act-by date, speeding-up detection) in weekly and monthly reports; `[reports] capacity_forecast_in` chooses the report types.
- **More report sections:** this period against the last one, repeating problems and log errors, patch and compliance, backup verification (age, size, sudden shrink), certificate and licence expiry calendar (`[licences]` in certificates.conf), every database / biggest tables / Oracle tablespaces with growth (also MongoDB and SQL Server sizes).
- **Audiences:** a schedule can email the summary only to management (`to_summary`) and a database-only report to the DBA team (`to_database`).
- **Alerts:** Slack and Microsoft Teams messages, quiet hours, minimum duration, routing to different people per area (`[alert_routes]`), acknowledgement from the dashboard, a daily digest.
- **Automatic critical services:** enabled or running KVM and database services are watched without being listed (`[critical] auto_detect`).
- **Web dashboard:** Alerts card, a Servers page for central monitoring of every server, Linux account sign-in (`system_users`, rights follow wheel/sudo), customer accounts tied to a group with branding (`[user_groups]`, `[branding:group]`), health of LRS Vitals itself, retention overview per report type, audit log viewer, downloadable configuration backups.
- **Health of the tool itself:** a `selfcheck` module and dashboard card report a stopped watcher, missing timers, late reports, failed email and low disk space.
- **Customer document pack** in `docs/customer`: one-page install guide, "what we monitor" sheet and sample reports (`tools/demo_reports.py` regenerates them).
- **HTTPS certificate for the web interface:** self-signed by default (`tls_source = self-signed`). Optional genuine Let's Encrypt certificate (`tls_source = letsencrypt`, never a self-signed fallback in that mode): ACME client with the HTTP check (port 80 or webroot) or the DNS check (Cloudflare, DuckDNS or a hook script; works for servers on private addresses), automatic renewal with live reload, `vitals web cert status|issue|renew`, an Operations card.
- **Licence:** strict proprietary licence, no use by anyone except the owner.
- **Compressed, indexed report archive:** reports older than a day are gzip-compressed file by file (`name.html.gz`, readable with `zcat`, `zless`, `zgrep`), shown, downloaded, searched and e-mailed without unpacking (`vitals archive list|files|cat|tail|grep|search|email|compress|reindex|stats`, web search box and "send by email"). Full-text index of every stored report. Old folder tarballs are converted.

## 1.3.0

- **Renamed to LRS Vitals.** The main command is `vitals`; `systemhealth` and `healthcheck` keep working. The RPM is `lrs-vitals`. Machine-facing names (the `/etc/lshme` folder, `lshme-*` service units, Nagios output, alert subject prefixes) are unchanged so existing installs, monitoring rules and mail filters keep working.
- **`vitals`: a full-screen home screen** (like `nmtui`) with a live dashboard (CPU, per-core load, memory, swap, filesystems and mounts, network, disk I/O, processes, five views, pause and speed keys), the reports already written with a score trend, run-a-check-now with progress, the server scan, the web interface and every setting.
- **First-time setup** (runs on first use, or `vitals setup`): welcome, system scan, organisation, email, alert levels, services to watch, databases, schedules, real-time alerts, web interface, review and finish. Every page can be skipped.
- **Server scan** (`vitals scan [--apply]`): finds what is present (KVM, databases, backup products, Cockpit, containers, clusters, certificates, applications, custom checks). Modules with nothing to check are left out of every run and every report instead of showing as SKIPPED. A rescan runs every Sunday at 02:00 (`systemd` timer or cron) and reports what changed. `[modules] force_enabled` keeps a module on anyway.
- **Web interface** (`vitals web`): sign-in (salted PBKDF2 hashes, rate limiting, expiring HttpOnly SameSite sessions, CSRF token and Origin check, administrator and read-only roles, strict Content-Security-Policy), live dashboard, reports with summaries, server scan, a settings editor for every configuration file with validation, operations (real-time monitor, schedules, email test, configuration backup and restore, logs, audit log) and accounts. HTTPS with an automatically created certificate; plain HTTP only on 127.0.0.1. Runs as `lshme-web.service`.
- **Reports:** a plain-language "At a glance" summary, a sticky navigation bar with one coloured link per area, expand/collapse/print buttons, and an issues filter. Areas with nothing to check are no longer listed.
- **RPM package** (`packaging/build_rpm.sh`): installs to `/opt/lshme`, settings in `/etc/lshme` (kept on removal), shows a getting-started guide on first install, runs the first scan and schedules the reports and the weekly rescan, refreshes schedules and restarts running services on upgrade, and removes timers and services on removal.
- **Fixes.** `schedule remove` now removes the timers as well as the services (it skipped the timers). The installer resets SELinux labels (the watcher failed with `203/EXEC` otherwise) and restarts a running watcher. Arrow keys and Esc now work from PuTTY and plink; `Ctrl-N`/`Ctrl-P`/`j`/`k`/`q`/`Ctrl-B` work where a connection sends none of them.
- **Full-screen configuration forms** (`vitals` > Configuration, or `healthcheck setup`): identity, modules, thresholds, services/ports/processes, databases, reports, email (test button), schedules (install button), retention, real-time monitoring, backup jobs, fleet servers, policy, web interface, and validate/back up/restore tools. Every value is validated before it is written; saves keep file comments, use mode 600, back up first and are audit-logged. `setup --text` keeps the plain prompts.

## 1.2.0

- **Real-time monitoring** (`healthcheck watch`).
  - Probes every minute read `/proc` and `statvfs` directly; heavier modules run one at a time in rotation.
  - Alerts go out by email, syslog and webhook, with confirmation (no flapping), escalation, reminders and recovery messages.
  - Runs as a systemd service limited to 5% CPU and 128 MB, or from cron.
- **Exclusions** (`healthcheck exclude`, menu option 21, `exclusions.conf`).
  - Accepted findings (for example `COCKPIT_SELF_SIGNED`) are removed from the score, status and alerts, but still listed in reports with the rule, reason, owner and expiry.
  - Rules support code wildcards, component and host globs, and a module filter.
  - A reason is required, and expired or invalid rules raise a warning.
- **Charts everywhere.**
  - Pie charts (findings by severity, module/server status) and bar/column charts (scores, filesystem usage) in HTML reports and the fleet dashboard.
  - ReportLab pie charts in PDFs, native Excel charts (Charts sheet), and a native PowerPoint "Health at a glance" slide.
  - Polished design: donut charts with percentage labels, a centre total and a value/share legend; bar charts sorted worst first with value labels, warning/critical threshold lines, axis ticks and a colour key; line charts with shaded threshold bands and the latest value marked.
  - PDF, Excel and PowerPoint use doughnut charts with percentage labels and bars with value labels, worst on top.
- **Real-time graphical monitoring.**
  - The watcher keeps a rolling metric history.
  - The Cockpit page and the new `healthcheck live` page show live line charts (CPU, memory, swap, load, problems) with a hover crosshair and tooltip, filesystem bars, a severity pie and module score bars, using a dependency-free SVG chart library under a strict CSP.
- **Cockpit page "LSHME Health"**: static package showing the score, issues, real-time problems and reports, with a run-now button. It updates by watching the result files. Installed automatically when Cockpit is present.
- **Fleet web dashboard** (`healthcheck dashboard build | serve`): self-contained, auto-refreshing, with filters, search, stale/unreachable detection and per-server drill-down. Optional read-only web server.
- **Uptime/SLA report** (`healthcheck sla`): availability, downtime and degraded time, per-component availability, incident log, mean time to recover, monitoring coverage and breaches against `[sla] target_percent`. The watcher now records confirmed incidents for this; it is attached to the monthly schedule.
- **Asset inventory** (`healthcheck inventory [--fleet]`): servers, disks, filesystems, network, packages, databases, VMs and services in XLSX/CSV/JSON.
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
- **Fixes found during an end-to-end run on a fresh install.**
  - Uninstalling a second installation (or running the test suite on a server) removed the main installation's schedules, watcher entry and Cockpit page. Removal now only touches files that run the same `--config-dir`, and an upgrade only refreshes its own schedules. `install.sh`/`uninstall.sh` gain `--no-cockpit`.
  - Capacity forecasts could raise "full in 1 day" from a few runs minutes apart. A forecast now needs at least `forecast_min_span_hours` (default 24) of history.
  - When a critical problem eased to warning, nothing was sent and SLA downtime kept counting until it cleared. The watcher now sends an IMPROVED notice and logs the change, and the SLA report counts only the time actually spent critical.
  - `exclude add --component` accepts a comma-separated list of globs, like `--hosts`.
  - `config set` accepts `main.conf` as well as `main`.
  - The menu's two columns no longer run together when a label is long.
  - A second `healthcheck watch run` could start beside the service, so every alert was sent twice or more. Only one watcher runs per installation now; a second one exits with a clear message.
  - Long scores such as an availability of `99.948` no longer wrap inside the PDF summary box.
  - `config set`, `exclude add|remove` and the setup wizard removed every comment from the file they changed, including the documented examples. Edits are now made in place and comments are kept.

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
