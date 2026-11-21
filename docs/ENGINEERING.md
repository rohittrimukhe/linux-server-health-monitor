# How LRS Vitals is built and released

This is the development life cycle the project follows, so that every change is planned, reviewed, tested, documented and can be undone.

## 1. Requirement

Every change starts from a stated need (a customer request, a defect, a finding in a report). It is written down in the change log entry
and in the commit message: what is wrong or missing, who is affected, what "done" looks like.

## 2. Design

- **Read-only by default.** Monitoring never changes the monitored system. Anything that changes LRS Vitals' own files (settings, logo,
  retention) needs an administrator, is written to the audit log, and keeps a backup (configuration backups, `/opt/lshme.pre-upgrade.*`).
- **Same data, many views.** Reports, the web dashboard, Cockpit and e-mails are built from the same stored results, so they cannot disagree.
- **No new dependencies.** Python 3.6+ standard library only, so it installs on Oracle Linux 7 to 10 without internet access.
- **Fail safe.** An optional part that fails (a format, a section, a remote server) is reported in place and never stops the rest.

## 3. Build: coding rules

- Python 3.6 compatible (checked by `tests/test_compat.py`); text files opened with an explicit encoding.
- Commands are run as argument lists, never through a shell; nothing from a web request reaches a command line (the SSH commands to
  member servers use only values from `servers.conf`).
- Web input is validated server-side (size limits, allowed values, path checks against the report folder, CSRF and origin checks on every
  change, administrator role for changes). Uploaded images are checked by decoding them, not by their name.
- Secrets live only in `credentials.conf` (mode 600) and are masked in every report, log and raw record.
- User-visible text is plain English; every status is shown with a word as well as a colour.
- Web pages follow WCAG 2.1 AA: 4.5:1 text contrast, visible focus, keyboard use, a skip link, labelled landmarks, 44 px touch targets,
  reduced-motion and forced-colour support, and a layout that fits phones up to 4K monitors.

## 4. Test

| Level | What | How |
|---|---|---|
| Unit and integration | every module, report, the web API, the CLI | `python3 -m unittest discover tests` (about 430 tests) |
| Report quality | no text past a PDF margin, byte-identical rebuilds, SLA rules, closed issues | `tests/test_report_quality.py`, `tests/test_regen.py` |
| Compatibility | Python 3.6 syntax and behaviour | `tests/test_compat.py` |
| Smoke | every command on an installed package | `tools/release_check.sh --smoke` on a test server |
| Load | reports and web under full CPU, memory and disk load | the load section of the smoke run |

A change is not released while any test fails. New behaviour comes with a test that fails without it.

## 5. Release

1. `tools/release_check.sh` on a Linux test server: syntax of every Python, shell and JavaScript file, the full test suite, a secret scan,
   and version consistency between the code, the RPM spec and the documents.
2. Update `CHANGELOG.md` (what changed and why) and the user guide.
3. Build the RPM on the test server (gzip payload so Oracle Linux 7 can install it), install it, run the smoke and load checks.
4. Commit with a message that explains the reason; push to `main`; put the RPM in `releases/`.

## 6. Deploy and roll back

- Install or upgrade: the RPM (`dnf install`), or `tools/upgrade_from_github.sh`, or `tools/deploy_fleet.sh` for many servers.
- Upgrade the central server first, check `vitals doctor` and the dashboard, then the other servers.
- Roll back: `dnf downgrade lrs-vitals` to the previous RPM, or move `/opt/lshme.pre-upgrade.<time>` back (the upgrade script prints the
  command). Settings, history, reports and raw data are never touched by an upgrade or a rollback.

## 7. Operate

- `vitals doctor` and the dashboard card "Health of LRS Vitals" show the tool's own health (watcher, timers, late reports, failed e-mail,
  disk space).
- Every administrative action is in the audit log and in the daily raw data.
- Incidents follow the usual life cycle: Open, Acknowledged, Resolved, each with an ID (INC-YYYYMMDD-XXXX), priority (P1 critical,
  P2 warning), opening and closing times and the time to resolve. A resolved incident is history and does not count in the score.
