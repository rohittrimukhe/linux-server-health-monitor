# Changelog

## 1.4.0

- **PDF paper and layout:** 8 mm margins (`[reports] pdf_margin_mm`), paper size `pdf_page_size` (a4, letter, legal, a3) and `pdf_orientation` (auto turns the page sideways only when a table cannot fit; portrait; landscape). Wider pages show more status tiles and details per row. Every table cell (text, bars, status chips, headers) is centred top to bottom. The HTML report uses the full browser width and prints on any paper.
- **Graphical email body:** report emails open with a one-minute summary: status band, key figures, a small donut of the checks (passed / warning / critical), usage bars (CPU, memory, swap, fullest disks), SLA, plain-English summary, what needs attention and resolved issues. Built from tables and inline styles so Outlook, Gmail and phones show it the same; the donut and logos travel inline (about 80 KB in total).
- **Dashboard look and speed:** an at-a-glance strip (servers healthy, SAN storage optimal, average CPU, fullest disk or pool) with vector symbols for servers, storage, CPU and disks on every card; smooth 60 fps motion (only transform and opacity, custom easing, press feedback, hover only for mouse, a short entrance when a page opens and never on the live refresh, reduced-motion respected); the folding menu reveals with clip-path instead of resizing; the page script and styles are sent compressed and revalidated with ETags for fast loading.
- **Safe automatic updates:** every day at 21:00 (`[update]` in main.conf) LRS Vitals checks GitHub for a newer version; it waits while a health check, report or fleet check is running (up to 2 hours, otherwise it tries the next night), backs up the program, installs, self-tests, restarts the watcher and web service and confirms they run. On any problem it restores the previous version and restarts it. Settings, history, reports and raw data are never touched. The result is e-mailed and shown in Operations > Automatic updates (Check now, Update now); terminal: `vitals update status | check | now`.
- **Dashboard, live:** server cards and SAN storage cards show moving heart-monitor style traces (servers: CPU and memory; storage: read/write IOPS, MB/s, response time) plus the busiest volumes, pools, failed drives and hardware. Storage health is read every 5 minutes and its I/O every 30 seconds ([fleet] san_live_seconds, san_perf_seconds); servers every 15 seconds. A member running an older LRS Vitals shows "UPGRADE" with the command to run instead of "Unreachable". Clicking a server card opens that server's own page: live figures, open problems, findings with what to do, checks by area, filesystems, VMs and pools.
- **Menu:** the left menu can be pinned open or folded to icons; folded, it opens while you point at it (remembered per browser).
- **SAN storage, any make:** add arrays yourself from the terminal (`vitals san add | list | test | show | remove`, `vitals san types`) or the web dashboard (Servers > SAN storage: make, controller A and B, read-only user, Test connection). Drivers for Lenovo ThinkSystem DE / NetApp E-Series, HPE MSA / Dell PowerVault ME / Lenovo DS, NetApp ONTAP / Lenovo DM, Dell Unity, Dell PowerStore, HPE 3PAR / Primera / Alletra 9000, Pure Storage FlashArray, IBM FlashSystem / Storwize / SVC and Huawei OceanStor, plus a reachability check for any other make. Full health: array status, controllers, drives (failed, predicted failure, SSD wear, spares), pools and volume groups (used, growth, degraded), volumes, fans / power / batteries, critical events, I/O and response time. Read-only; the password stays in credentials.conf. Live alerts through the watcher; report section sized per report type (daily health, weekly / monthly trends, comprehensive inventory). Guide: docs/SAN_STORAGE.md.
- **One-click exceptions:** the dashboard lists open warnings and critical findings with an Exclude button (reason, approved by, optional end date) and the accepted exceptions with Un-exclude. Writes exclusions.conf like `vitals exclude add`; every report still lists them under Accepted exceptions.
- **Only what the server has:** the database section is left out on a server without a database (no more empty section on a KVM host).
- **KVM in every report:** daily, weekly and monthly show VMs on/off, vCPU and memory given to VMs, the busiest VMs with CPU and disk growth, and the storage pools. The comprehensive report adds every VM disk with size and growth, how long each VM ran in the period and how often it stopped, host against VMs, snapshots, networks, and charts of VM disk growth and VM CPU. The health check now records per-VM history for this.
- **Fewer repeat alert emails:** reminders for a problem that is still open now come every 6 hours (`[watch] repeat_minutes = 360`, was 60). In the dashboard (Alerts > Active problems) an administrator can mute one problem for 6 hours, 24 hours, 7 days or until it ends: no reminders, escalations or recovery emails for it, while the incident log still records it. Unmute at any time.
- **Email improvements:** comparison with the previous week or month (health score, CPU, memory, data growth, with green/red arrows), an "Open the full report" button (`[smtp] dashboard_url`, or this server's web interface when it is on the network), a subject that says how many items need attention, and a phone layout (tiles two per row, nothing wider than the screen).
- **Email sender name:** `[general] customer_short` (e.g. PDHHK) and `[smtp] sender_name` (e.g. `{customer_short} Oracle Health Reports`); empty gives "<short name> Health Reports". The old default subject prefix [LSHME] becomes [<short name>].
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
- **Keep the data, rebuild the files:** each report keeps its full source (`.source.json.gz`) and fingerprints. After `keep_rendered_days` (default 7) the PDF, Excel, HTML, PowerPoint, CSV and text files are removed once they are verified to rebuild byte for byte; they are rebuilt identically whenever opened, downloaded or e-mailed. Report files are now repeatable (fixed dates inside PDF and Office files). The web report view lists every format with View, Download and Email.
- **Raw data store:** every measurement as taken, one file per server per day (`<server>_<YYYY-MM-DD>_00-00-00_to_23-59-59.jsonl`), sealed at 02:00 with a manifest (records per type, coverage, gaps, complete, SHA-256). Minute samples now keep every core, filesystem (bytes, inodes), interface (rates, errors, drops), disk I/O, memory detail and open problems; plus top programs, connections and systemd services every 15 minutes, every incident, alert, health-check result, report, e-mail, audit action and fleet result. `vitals raw list|show|tail|grep|stats|seal|verify|backfill|replay`, web "Raw data by day" with downloads, `raw_days` retention.
- **Executive summary** for the CEO and management (daily, weekly, monthly): plain words, large figures, the decisions needed; sent to `to_summary`. **Report templates** explorer (`vitals templates list|preview`, dashboard gallery with live preview).
- **Web dashboard redesigned:** calm "Balanced" default theme (neither dark nor bright) plus Light, Dusk, Forest, High contrast and "match my computer"; navy sidebar with icons that becomes an icon rail on medium screens and a top bar on phones; fluid layout from phones to 4K and wall monitors; WCAG 2.1 AA (contrast, focus, skip link, landmarks, 44 px touch targets, reduced motion, forced colours); refresh interval chosen per browser (Off, 2 s to 1 min).
- **Incidents in the usual service-management form:** Active problems (open now) separate from the Incident log (Open / Acknowledged / Resolved, ID `INC-YYYYMMDD-XXXX`, priority P1/P2, opened, resolved, time to resolve, period filter). Resolved incidents are shown muted and green, never as red problems. KPI tiles: open now, resolved in 24 h, mean time to resolve.
- **All servers, live:** `vitals pulse` on every server; the central server asks each member every 30 s over SSH (one persistent connection, private socket folder) and the dashboard shows CPU, memory, fullest disk, open problems and the last hour for every server, with unreachable servers shown with the reason. `vitals fleet live [--json]`.
- **Cockpit:** the LRS Vitals page in Cockpit also shows all servers live (with its own refresh choice) and the stored reports of every server, which open exactly as sent (compressed and rebuilt ones included).
- **Logos made easy:** any image (PNG, JPG, GIF, WebP, BMP, SVG, any size) is converted to PNG and fitted in the browser; oversized PNGs are fitted on the server too; upload also from Settings > main > general > logo; the customer logo appears next to the LRS logo in the dashboard; wide logos get a wider box in the PDF.
- **Engineering:** `docs/ENGINEERING.md` (development life cycle: requirement, design rules, coding rules, tests, release, rollback, operation) and `tools/release_check.sh` (release gate: syntax, tests, secret scan, version consistency).
- **Closed issues:** a fixed problem is shown only under **Closed issues** and no longer counts in the score, the status, the repeating-problem warnings or the open lists.
- **Report logos:** upload the customer logo in the dashboard or with `vitals logo set`; one for all servers or one per server; `vitals logo push` and the roll-out script send it to every server. Shown on PDF (header and every page), HTML and Excel.
- **PDF layout rebuilt:** exact Helvetica text widths (nothing runs past a margin or is cut off), running header with logo on every page, content-sized KPI tiles, tables that pick a font size that fits, headings kept with their tables, a faint watermark, footer rule.
- **Report fixes:** network totals count physical adapters once (bond, bridge and VM adapters no longer triple the figure) and `bonding_masters` is no longer listed as an adapter; SLA counts only unavailability as downtime and is "not rated yet" until enough monitoring exists; "Accepted exceptions" section; "Only N hours of data" notice on new installations; days before monitoring began are no longer listed as empty rows; local-hour buckets (India UTC+5:30) and HH:MM chart axes; checklist thresholds with consistent units; security findings such as root login in "What needs attention"; per-finding reasons; "Now" column on repeating problems; the report's own helper commands are left out of the top-10 lists.
- **SLA achieved** section and tile in the daily, weekly and monthly reports (availability against the target, downtime allowance, incidents, MTTR, coverage, per component).

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
