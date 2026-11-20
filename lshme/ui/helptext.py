# Linux Server Health Monitor Enterprise
# Copyright (c) 2026 LRS Services Pvt Ltd, Mumbai. All rights reserved.
"""The short guides shown after installation and from Help in the vitals screen."""

QUICK_START = """\
LRS Vitals - Linux server health monitor, from LRS Services

  vitals                  open the health screen (live graphs, reports, settings)
  vitals setup            run the first-time setup again, page by page
  vitals run              run a complete health check now and write the reports
  vitals scan             scan this server for what is present
  vitals web              start the web interface
  vitals help             show this guide

First time? Run:  sudo vitals
(The older names  systemhealth  and  healthcheck  still work.)
It scans this server, then asks a few questions, one page at a time.
"""

KEYS = """\
Moving around
  Up / Down      move            Enter   open or edit          Esc   go back
  Tab / Shift-Tab  next / previous   Space   switch yes/no     F10   save
  If your connection does not send arrow keys or Esc (some Windows tools):
  Ctrl-N / Ctrl-P or j / k move, Ctrl-B or q goes back.

Live dashboard
  1-5 or Tab     switch view     p   pause     + / -   faster / slower     q   back
"""

HELP_PAGES = (
    ("What this does", """\
LRS Vitals checks the health of this Linux server and writes reports.

  - It scans the server and only checks what is really there. If there is no KVM,
    no database or no backup product, that part is left out of every scan and report.
  - Every Sunday at 02:00 it scans again, so new software is picked up automatically.
  - Reports (HTML, PDF, Excel, PowerPoint, JSON) are written on the schedule you choose
    and can be emailed.
  - A light watcher can check the server every minute and send alerts.
  - The same screens are available in a web browser (Web interface).
"""),
    ("First-time setup", """\
Run  sudo vitals  and follow the pages:

  1  Welcome
  2  System scan         - finds what is present on this server
  3  Organisation        - company, customer, environment (shown on reports)
  4  Email               - mail server and recipients, with a test message
  5  Alert levels        - when CPU, memory and disks count as warning or critical
  6  Services to watch   - which services must be running
  7  Schedules           - daily, weekly and monthly reports
  8  Real-time alerts    - the always-on watcher
  9  Web interface       - optional browser access with a password
  10 Review and finish

Every page can be skipped and everything can be changed later under Configuration.
"""),
    ("Where things are", """\
  Program         /opt/lshme          Commands   vitals (also systemhealth, healthcheck)
  Settings        /etc/lshme          (edit with Configuration, or the web interface)
  Reports         /var/lib/lshme/reports/<host>/<schedule>/<date>/
  History         /var/lib/lshme/history.sqlite3
  Logs            /var/log/lshme

Settings are backed up before every change (Configuration > Tools > Back up / restore).
"""),
)


def full_help():
    parts = [QUICK_START, KEYS] + ["%s\n%s\n%s" % (t, "-" * len(t), b) for t, b in HELP_PAGES]
    return "\n".join(parts).splitlines()


INSTALLED_BANNER = """\

  LRS Vitals is installed.

  Get started:
      sudo vitals                first-time setup (scans this server, then a few questions)

  Afterwards, the same command opens the live health screen:
      vitals                     live graphs, reports and settings
      vitals help                all commands and keys

"""
