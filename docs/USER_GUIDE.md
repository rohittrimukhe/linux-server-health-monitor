# LSHME user guide

## 1. Installation

```bash
tar xzf lshme-1.1.0.tar.gz && cd linux-server-health-monitor     # or git clone
sudo ./install.sh --check            # dependency check only
sudo ./install.sh                    # install or upgrade
sudo ./install.sh --with-optional    # also install ReportLab/openpyxl/python-pptx (needs repos or pip)
sudo ./install.sh --setup            # install, then start the wizard
```

| Path | Contents |
|---|---|
| `/opt/lshme` | Program (replaced on upgrade; the previous version is kept in `/opt/lshme.previous`) |
| `/etc/lshme/*.conf` | Configuration (mode 700/600; never overwritten by upgrades) |
| `/var/lib/lshme` | `history.sqlite3`, baseline, config backups, cache |
| `/var/lib/lshme/reports/<host>/<schedule>/<timestamp>/` | Reports |
| `/var/log/lshme` | `lshme-YYYYMMDD.log`, `error-YYYYMMDD.log`, `email.log`, `audit.log` |
| `/usr/local/bin/healthcheck` | Command |

Upgrade: run `install.sh` from the new version. Rollback: `rm -rf /opt/lshme && mv /opt/lshme.previous /opt/lshme`.
Uninstall: `sudo ./uninstall.sh` keeps configuration and data; add `--purge` to remove everything. Schedules, the watcher and the Cockpit page are removed only when they belong to the installation being removed (same `--config-dir`), so a second or test installation never touches the main one.

Portable mode, with no installation: `./healthcheck --home /some/dir ...` keeps
everything under that directory. This is useful for trials and for running
without root.

Run as root for full coverage. As a normal user, checks that need root are
skipped or reported as UNKNOWN: `/etc/shadow`, `sshd -T`, `smartctl`,
`dmidecode` and database OS authentication.

## 2. Configuration

`healthcheck setup` opens full-screen configuration forms in the style of `nmtui`.
Menu option 17 opens the same screens. Files can also be edited directly; every
setting has a default.

| Key | Action |
|---|---|
| Up / Down, Tab | Move between fields and buttons |
| Enter | Edit a text or number, open a list of choices, or press a button |
| Space | Switch yes/no, step through a short list, tick or untick in a checklist |
| F10 | Validate and save |
| Esc | Go back (asks first if there are unsaved changes) |
| Ctrl-U / Ctrl-K | While typing: delete to the start / end of the line |
| Ctrl-N / Ctrl-P, or j / k | Down / up, for connections that do not send the arrow keys |
| Ctrl-B, or q | Back, for connections that do not send Esc |

Use PuTTY, Windows Terminal, an SSH session in a terminal, or any other terminal emulator
and the arrow keys and Esc work. Some Windows command-line SSH tools (plink run inside a
console window) never send arrow keys or Esc at all; the alternative keys above work there.
(`j`, `k` and `q` are only shortcuts when you are not typing in a field.)

Screens: organisation and report identity; monitoring modules (switch each on or
off); thresholds (with a check that warning is not above critical); services,
ports, processes and per-filesystem limits; databases (auto-discovery and
instances, with passwords kept in `credentials.conf`); report settings; email
(with a **Test email** button); schedules (with an **Install** button for the
timers); retention and cleanup; real-time monitoring and alerts (with **Status**,
**Run once**, **Install** and **Remove**); backup jobs; fleet servers; security,
network and certificate policy; and tools to validate, back up and restore the
configuration.

Every value is checked before it is written. A save keeps the comments in the
file, writes it with mode 600, takes a backup of the configuration first and
adds an entry to the audit log. Run as root to save; as another user the screens
open read-only. Terminals of at least 80 x 24 are best; on a terminal without
curses, or when output is not a terminal, `healthcheck setup` falls back to plain
prompts. Use `healthcheck setup --text` to choose the plain prompts, or
`healthcheck setup --page smtp` to open one screen directly (`identity`,
`modules`, `thresholds`, `services`, `databases`, `reports`, `smtp`, `schedules`,
`retention`, `watch`, `backups`, `fleet`, `policy`, `tools`, `validate`, `backup`).

| File | Purpose |
|---|---|
| `main.conf` | Company, customer, environment, logo; module enable/disable; security policy; extra log files and patterns; network targets; KVM URI; Cockpit; HTTP checks; integrations |
| `thresholds.conf` | All warning/critical limits, with per-mount overrides in `[disk_overrides]` (`/archive = 90,97`) |
| `services.conf` | `[critical] services = sshd, crond, oracle-ohasd`, `[ports] listener = 1521`, `[processes] pmon = ora_pmon_` |
| `database.conf` | Database instances (auto-discovery covers most local ones) |
| `smtp.conf` | Mail server, recipients, critical recipients, send policy, attachments |
| `reports.conf` | Default formats, report type, title |
| `schedule.conf` | Schedules (see section 6) |
| `retention.conf` | Retention per report category, logs and history |
| `servers.conf` | Fleet servers for central monitoring |
| `backup.conf` | Backup product switches and `[job:NAME]` RPO checks |
| `certificates.conf` | Certificate files, directories and `host:port` endpoints |
| `custom.conf` | Custom Nagios-style check scripts |
| `credentials.conf` | Passwords only (mode 600) |

Useful commands: `healthcheck config validate`, `config backup`,
`config restore FILE`, `config set FILE SECTION KEY VALUE` and `config show`.
Every change made through LSHME takes a configuration backup first and is
recorded in `audit.log`.

## 3. Running checks

```bash
healthcheck                                   # menu
healthcheck run                               # all modules, default formats
healthcheck run --days 7 --formats all --report-type full
healthcheck run --modules database,storage --formats html
healthcheck run --email                       # email per send_policy
healthcheck run --force-email --to me@example.com
healthcheck run --exit-status                 # exit 0/1/2 = healthy/warning/critical
healthcheck check kvm --details               # on-screen only, no reports
healthcheck modules                           # list modules
healthcheck doctor                            # self-diagnostics
```

`--days` sets the period a report covers: log analysis, trend windows,
archive-log and RMAN history, and failed jobs.

Overlapping runs are prevented by a lock (exit code 75). Each module has a
command time limit, and the whole run has `run_timeout`. A module that fails
is reported as UNKNOWN and never stops the other modules.

## 4. Reports

| Format | Best for |
|---|---|
| HTML | Interactive single file: score gauge, charts, collapsible sections |
| PDF | Formal report: cover summary, charts, coloured tables, page footer |
| XLSX | Analysis: Summary, Findings, Actions, one sheet per module, Metrics, Score history |
| PPTX | Management presentation: summary, health table, key issues, filesystem chart, trends, actions |
| TXT | Plain technical report |
| CSV | Findings plus a metrics file |
| JSON | Machine-readable; can be re-rendered with `healthcheck report FILE.json` |

Report types:
- `executive`: summary tables and key issues only.
- `technical`: every table, plus evidence for each finding.
- `full`: technical plus an appendix of raw evidence and module errors.

Set the company name, customer, environment and logo in `main.conf [general]`.

## 5. Email

Configure with `healthcheck setup` or menu option 10, then run `healthcheck email-test`.
Supported: SMTP with STARTTLS, implicit TLS or plain, with or without authentication, or local `sendmail`.

- `send_policy`: `always`, `issues` (warning or critical only), `critical`, or `never`.
- `critical_to`: extra recipients added only when the status is CRITICAL. Critical mail is also sent with high priority.
- `attach_formats` and `max_attachment_mb`: attachments over the limit are left out, and the email lists them.
- `retries` and `retry_delay`: retry with back-off. Authentication and recipient errors are not retried.
- Every delivery is logged in `email.log` and the history database (`healthcheck history --deliveries`).
- While maintenance mode is on, email is suppressed (`healthcheck maintenance on --minutes 120 --reason patching`).

## 6. Schedules

Three schedules are predefined and can be changed:

| Schedule | Default | Period | Formats |
|---|---|---|---|
| daily | Monday to Saturday 09:00 | 1 day | html, pdf, xlsx (daily checklist report) |
| weekly | Sunday 12:00 | 7 days | html, pdf, xlsx, csv (weekly report with growth by date) |
| monthly | 1st of the month 10:00 | 30 days | html, pdf, xlsx, csv (summary and comprehensive report, both emailed) |

Add your own in `schedule.conf`:

```ini
[schedule:hourly-db]
frequency = hourly        ; hourly | daily | weekly | monthly | custom
time = 00:15              ; minute is used for hourly
modules = database,storage
days = 1
formats = html,json
email = yes
send_policy = issues
to = dba-team@example.com
```

`healthcheck schedule install` writes systemd timers (`lshme-<name>.timer`) or
`/etc/cron.d/lshme` if systemd is not available. Use `schedule status` to see
next runs, `schedule show` to preview the files and `schedule remove` to delete
them. Scheduled runs retry on failure (`[scheduler] retries`) and apply
retention automatically afterwards.

## 7. Retention and space optimisation

`healthcheck retention preview` shows what would happen; `retention apply`
does it. Scheduled runs apply it automatically.

**Age rules.** Report folders older than their category limit (`daily_days`,
`weekly_days`, `monthly_days`, `adhoc_days`, ...) are deleted, or moved to
`archive_dir` if one is set. Older folders are compressed after
`compress_after_days` (see below). Execution and error logs, email and audit logs, history
and configuration backups are trimmed as well.

**Compressed report archive.** Reports older than `compress_after_days` (default 1;
0 = never) are gzip-compressed file by file, the same way Linux rotates its logs:
`report.html` becomes `report.html.gz`, the folder keeps its name and its age. The
newest report of each type is never compressed. Excel files are already compressed
and stay as they are. Text reports usually shrink to a tenth of their size.

Nothing has to be unpacked to use them:

| What | How |
|---|---|
| Standard Linux tools | `zcat`, `zless`, `zgrep`, `zcat file.gz \| tail` work directly on the `.gz` files |
| List stored reports, size saved | `vitals archive list`, `vitals archive stats` |
| Files of one report | `vitals archive files latest:daily` |
| Print / tail a report | `vitals archive cat 20261110 txt`, `vitals archive tail latest:weekly txt -n 40` |
| Search lines (like zgrep) | `vitals archive grep "tablespace.*9[0-9]%" --category daily` |
| Indexed word search | `vitals archive search tablespace USERS` |
| Email a stored report | `vitals archive email 20261110 pdf,html --to dba@example.com` |
| Compress now / rebuild the index | `vitals archive compress [--days N]`, `vitals archive reindex` |

A report is named by `host/category/stamp`, `latest`, `latest:daily`, or any part of
the stamp that is unique (`20261110`). The web dashboard opens and downloads compressed
reports as normal files, has a **Search stored reports** box on the Reports page, and
administrators can send any stored report by email from its summary. Every report file
and its text are kept in an index (`data/reports_index.sqlite3`, full-text search when
SQLite supports it) that is updated after every run and every cleanup. Older installations
that compressed whole folders into `<stamp>.tar.gz` are converted automatically.

**Space rules.** These delete reports early when space matters:

| Setting | Default | Meaning |
|---|---|---|
| `space_cleanup` | yes | Turn space-based deletion on or off |
| `min_free_percent` | 10 | Start when the report filesystem has less free space than this |
| `free_target_percent` | 15 | Then delete until this much is free |
| `max_report_dir_gb` | 0 (off) | Size cap for the whole report folder |
| `space_min_keep_days` | 1 | Never delete reports younger than this |

Space cleanup deletes the least valuable reports first: adhoc, then daily,
custom, weekly, fleet, and monthly last, oldest first within each group. It
runs before every run (to make room) and after every run (to enforce the
cap). The newest report of each server and schedule is never deleted. If
the target can't be met by deleting eligible reports, a warning is shown and
logged instead of deleting more.

```bash
healthcheck retention space            # preview space-only cleanup and current usage
healthcheck retention space --apply    # delete now
```

Every deletion is written to `audit.log` with its reason.

## 8. History, trends, forecasts, drift

Every full run is stored in `history.sqlite3`. Reports include:
- a trend table comparing now with 1, 7 and 30 days ago, plus averages,
- a filesystem fill forecast (a linear trend over 30 days, clearly labelled as an estimate; it needs at least `forecast_min_samples` runs covering `forecast_min_span_hours`, default 24 h, so a few runs minutes apart never produce a forecast),
- recurring issues,
- a score history chart.

Use `healthcheck history` for past runs and `healthcheck baseline save` to
capture a baseline. Later runs report drift: enabled services, ports, mounts,
packages, UID 0 and login accounts, kernel, cron files, and the
sshd/sudoers/fstab/hosts/resolv/SELinux configuration.

## 9. Databases

Running Oracle instances are discovered from their `pmon` processes and
`/etc/oratab`, then checked with OS authentication (`/ as sysdba`, run as the
instance owner through `runuser`). Nothing needs configuring when LSHME runs
as root. To monitor with a dedicated account instead:

```sql
-- Oracle 11g R2+ (read-only monitoring account)
CREATE USER lshme_monitor IDENTIFIED BY "<password>";
GRANT CREATE SESSION, SELECT_CATALOG_ROLE TO lshme_monitor;   -- 12c+: SELECT ANY DICTIONARY also works
```

```ini
# database.conf                          # credentials.conf
[oracle:PROD]                            [oracle:PROD]
oracle_home = /u01/app/oracle/product/19.0.0/dbhome_1   password = <password>
username = lshme_monitor
connect_identifier = //dbhost:1521/PROD
backup_max_age_hours = 26
```

- **PostgreSQL**: auto-discovered with peer authentication as `postgres`, or configure `host`, `port`, `user`, and a password or `passfile`.
- **MySQL/MariaDB**: point `defaults_file` at a mode-600 file containing credentials.
- **MongoDB**: set `uri` and `username`, and put the password in credentials.conf.
- **SQL Server**: set `server` and `username`; the password goes in `SQLCMDPASSWORD` via credentials.conf.

## 10. Multi-server (fleet)

1. Install LSHME on every server.
2. On the central server, set up SSH key access to each one; `BatchMode=yes` is the default.
3. List the servers in `servers.conf`.
4. Run `healthcheck fleet run --formats html,pdf,xlsx --email`.

The result is a fleet report: an overview of every server, a matrix of module
scores, every warning and critical finding, and per-server summaries.
Unreachable servers are reported as CRITICAL. Servers the central host cannot
reach can write their results to a drop directory instead (`main.conf
[integrations] agent_dropdir`), which you load with
`healthcheck fleet import --dir DIR`.

## 11. Integrations

- **Nagios/Icinga**: `healthcheck nagios` prints the standard status line with performance data and exits 0, 1, 2 or 3.
- **Prometheus/Grafana**: set `[integrations] prometheus_textfile = /var/lib/node_exporter/textfile_collector/lshme.prom`, or run `healthcheck export prometheus --output FILE`.
- **JSON**: `healthcheck agent` or `healthcheck export json`.

## 12. Real-time monitoring and alerts

```bash
healthcheck watch install      # start continuous monitoring (systemd service, or cron every minute)
healthcheck watch status       # active problems, last check, CPU/memory used by the watcher
healthcheck watch once         # run one check now
healthcheck watch remove       # stop and remove
```

Settings are in `main.conf [watch]`:

| Setting | Default | Meaning |
|---|---|---|
| `interval` | 60 | Seconds between checks |
| `confirm` | 2 | Consecutive bad checks before alerting (prevents flapping alerts) |
| `repeat_minutes` | 60 | Reminder interval while a problem continues (0 = never) |
| `recovery_alerts` | yes | Send a message when a problem clears, and an IMPROVED message when it eases (e.g. CRITICAL to WARNING) |
| `rotation_modules` | services, database, kvm, cockpit, network, backup, cluster, certificates | Heavier modules run one per check, in turn |
| `rotation_minutes` | 15 | How often each rotated module repeats |
| `alert_min_severity` | WARNING | `WARNING` or `CRITICAL` |
| `alert_email` / `alert_to` | yes / (smtp.conf) | Email alerts and their recipients |
| `syslog` | yes | Also write alerts to syslog (for SIEM forwarding) |
| `webhook_url` | | POST JSON `{"text": ...}` to Teams/Slack-style webhooks |
| `max_rss_mb` | 100 | The watcher restarts cleanly if it ever uses more memory |

The watcher runs at nice 19 with the idle I/O class. Its systemd unit limits
it to 5% CPU and 128 MB of memory. Thresholds come from `thresholds.conf`,
critical services, ports and processes from `services.conf`, and email
settings from `smtp.conf`.

## 13. Exclusions (accepted findings)

Some findings are known and accepted, for example Cockpit using its own
self-signed certificate. An exclusion removes such a finding from the score,
the status and all alerts, including real-time ones. The finding still
appears in reports under **Excluded findings**, with the rule, reason, owner
and expiry, so nothing is hidden.

```bash
healthcheck exclude add cockpit-self-signed --code COCKPIT_SELF_SIGNED --module cockpit \
    --reason "Internal console; CA certificate not required" --owner infra-team
healthcheck exclude add archive-migration --code DISK_USAGE --component "/archive*" \
    --reason "Migration CHG-1234" --until 2026-12-31
healthcheck exclude list
healthcheck exclude remove archive-migration
```

How rules work:
- `code` accepts wildcards (`ORACLE_INVALID_*`).
- `component` is a glob on the component, such as a mount point, service or database.
- `module` limits the rule to one module.
- `hosts` limits it to certain hosts in fleet setups (`dev-*`).
- A `reason` is required.
- After the `until` date the rule stops applying and the report shows an `EXCLUSION_EXPIRED` warning, so exceptions are reviewed instead of forgotten.

Menu option 21 runs a fresh check and lets you pick findings by number to
exclude. Rules are stored in `exclusions.conf`, and every change is backed up
and recorded in the audit log. See `docs/MODULES.md` for all finding codes.

## 14. Cockpit page

`install.sh` adds an **LSHME Health** page to Cockpit whenever Cockpit is
installed. You can also run `healthcheck cockpit-page install`, or add
`--user` to install for your own account only.

The page shows:
- the latest score and status, issues with recommended actions, and module health;
- the real-time watcher's active problems and its own CPU and memory use;
- buttons to open the latest HTML, PDF or JSON report.

**Run health check now** runs a low-priority check and needs administrative
access in Cockpit. The page watches the result files and refreshes itself; it
does no polling and runs no background service. To remove it, run
`healthcheck cockpit-page remove`.

## 14a. Live graphs (real-time graphical monitoring)

While `healthcheck watch` runs, it keeps a rolling 24-hour history (about
200 KB). `main.conf [watch] metrics_hours` changes the length. Each sample
holds CPU, memory, swap, load per CPU, the eight fullest filesystems and the
number of active problems. The live page draws:

- line charts of CPU, memory and swap, with warning and critical lines; of load per CPU; and of active critical and warning problems, switchable between 1 h, 6 h and 24 h;
- current filesystem usage bars;
- a findings-by-severity pie and module score bars from the latest full check.

There are two ways to open it:

- **In Cockpit:** the *LSHME Health* page updates the instant new data is written.
- **Without Cockpit:** `healthcheck live` starts a tiny read-only web server on `127.0.0.1:8089`. Open it from your PC through an SSH tunnel: `ssh -L 8089:127.0.0.1:8089 user@server`, then browse to http://localhost:8089. It refreshes every 15 seconds. It serves only the page and three read-only data endpoints, with a strict content-security policy. Use `--bind` only behind a firewall or reverse proxy; there is no login.

Reports also contain charts: pie charts (findings by severity, module or
server status) and bar charts (module scores, filesystem usage) in HTML, PDF,
Excel (native charts on a *Charts* sheet) and PowerPoint (a native "Health at a
glance" slide), and in the fleet dashboard.

## 15. Fleet web dashboard

Every `healthcheck fleet run` saves each server's latest result and rebuilds
`reports/_dashboard/index.html`, a single self-contained page that refreshes
every 5 minutes. It shows each server's status, score, top issues, group and
environment. Servers whose last result is older than `stale_hours` are marked
STALE, and failed collections UNREACHABLE. Each server has a full drill-down
report.

```bash
healthcheck dashboard build                       # rebuild from the latest results
healthcheck dashboard serve --port 8088           # read-only web server on 127.0.0.1
healthcheck dashboard serve --bind 0.0.0.0        # serve to the network (no login: restrict with a firewall/proxy)
```

The settings are in `main.conf [dashboard]`: `title`, `stale_hours` (default
26), `refresh_seconds` (default 300) and `auto_build` (default yes).

## 16. Uptime / SLA report

The real-time watcher records every confirmed incident: when it opened,
escalated and closed. `healthcheck sla` turns that history into an
availability report:

- **Availability**: time without a confirmed CRITICAL problem, divided by the period. Overlapping incidents are counted once.
- **Degraded time**: time with WARNING-only problems.
- **Per-component availability**: for services, filesystems, ports, processes, VMs, databases and so on.
- **Incident log**: with duration, mean time to recover and the longest incident.
- **Monitoring coverage**: if the watcher was stopped for part of the month, the report says so.
- **Breaches**: anything below `main.conf [sla] target_percent` (default 99.9) is a finding.

```bash
healthcheck sla                         # current month to date
healthcheck sla --month 2026-10 --formats html,pdf,xlsx,json
```

The monthly schedule attaches the previous month's SLA report automatically
(`sla_report = yes` in `[schedule:monthly]`).

## 17. Asset inventory

```bash
healthcheck inventory                          # this server: XLSX + CSV + JSON
healthcheck inventory --fleet                  # every server in servers.conf over SSH
healthcheck inventory --formats xlsx,html,pdf
```

The inventory has eight sheets:
- **Servers**: manufacturer, model, serial, BIOS, CPU, memory, OS, kernel, virtualization and counts.
- **Disks**, **Filesystems** and **Network** (MAC, speed, addresses).
- **Packages**: name, version, architecture, vendor and install date.
- **Databases**: engine, instance, version and owner.
- **VMs** and **Services** (listening ports).

The monthly schedule attaches it as XLSX (`inventory_report = yes`).

## 18. Branding

Reports carry the LRS Services logo and "© 2026 LRS Services Pvt Ltd,
Mumbai. All rights reserved." A customer logo set in `main.conf [general]
logo` appears alongside it. The built-in PDF and PPTX writers, used when
ReportLab or python-pptx are not installed, show the copyright as text
without the logo image.

## 19. Troubleshooting

| Symptom | What to do |
|---|---|
| Many UNKNOWN findings | Run as root; `healthcheck doctor` lists missing tools |
| `another LSHME run is in progress` (exit 75) | A scheduled run is active; wait, or check `data/lshme.lock` |
| Oracle `ORACLE_CONNECT_FAILED` | Check `oracle_home`/`os_user` in database.conf; try `su - oracle -c "sqlplus / as sysdba"` |
| Email fails | `healthcheck email-test`; check `email.log`; verify port and security (587 STARTTLS, 465 SSL) |
| PDF has no charts | Install `python3-reportlab` (the built-in renderer is used otherwise) |
| Schedules not running | `healthcheck schedule status`; `systemctl list-timers 'lshme-*'`; `journalctl -u lshme-daily` |
| Reports too large | Use `report_type = executive`, reduce `formats`, set `max_attachment_mb` |

## 20. HTTPS certificate for the web interface

By default the web interface creates and uses a **self-signed certificate** (`tls_source = self-signed`). The connection is encrypted;
browsers show a one-time warning because no public authority vouches for it. Nothing needs to be set up.

Optionally (`tls_source = letsencrypt`) it can use a **genuine, free, publicly trusted Let's Encrypt certificate** instead. In that
mode a self-signed certificate is never used: the HTTPS web interface waits until the real one is issued, and
`vitals web cert status` shows what is missing. `tls_source = custom` uses your own certificate and key (`cert` and `key`).
The certificate is used by the web interface only; no other service and no system setting changes.

**What Let's Encrypt needs: a public DNS name.** An IP address, a LAN-only host name such as `oraclelinux9`, and names ending in
`.local`, `.lan` or `.internal` cannot get a certificate. Then prove you control the name, in one of two ways:

| Check | What must be true | Works for a server on a private address? |
|---|---|---|
| `acme_challenge = http` | The name points at this server and **port 80 is reachable from the internet** (or another web server on port 80 serves `acme_webroot`) | No |
| `acme_challenge = dns` | You can edit the domain's DNS: a TXT record is added and removed automatically (`acme_dns_provider = cloudflare`, `duckdns` or `hook`, token in `credentials.conf` under `[acme]`) | **Yes** (the name may point at 192.168.x.x) |

**Free setup for a server on a private network (DuckDNS, about 5 minutes)**

1. Sign in at duckdns.org, create a sub-domain such as `lrsvitals` and set its address to the server's address (for example 192.168.1.3). Copy your token.
2. Put the token in `/etc/lshme/credentials.conf` (mode 600):

       [acme]
       token = YOUR-DUCKDNS-TOKEN

3. In `/etc/lshme/web.conf`:

       tls_source = letsencrypt
       acme_domains = lrsvitals.duckdns.org
       acme_challenge = dns
       acme_dns_provider = duckdns
       acme_email = you@example.com
       acme_directory = staging        # test first; change to production when it works

4. `vitals web cert issue`, then `vitals web install`. Open `https://lrsvitals.duckdns.org:8443/` (some routers block names that
   answer with a private address; if so, add the name to the router's DNS exceptions or use your own domain).

With your own domain on Cloudflare use `acme_dns_provider = cloudflare` and a token that may edit that zone's DNS.

**Renewal is automatic.** While the web service runs (`vitals web install`) it checks about hourly and renews when fewer than
`acme_renew_days` (30) days are left; the new certificate is loaded without a restart. A failed attempt keeps the old certificate
and shows the reason in Operations > Web certificate and in `vitals web cert status`. Failed starts are not retried more than every
15 minutes, so Let's Encrypt's limits are not hit. `staging` certificates are not trusted by browsers: they only prove the setup works.
