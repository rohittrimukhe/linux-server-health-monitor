#!/usr/bin/env python3
"""Regenerate docs/MODULES.md (module list and finding codes) from the source.

Usage: python3 packaging/gen_docs.py
"""
import glob
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from lshme import modules  # noqa: E402
from lshme.modules.logs import PATTERNS  # noqa: E402

ADD_RE = re.compile(r'res\.add\(\s*(?:"(CRITICAL|WARNING|UNKNOWN|INFO)"|[\w\[\]]+)\s*,\s*"([A-Z0-9_]+)"')


def codes_in(paths):
    seen = []
    for p in paths:
        with open(p, encoding="utf-8") as f:
            for sev, code in ADD_RE.findall(f.read()):
                if code not in [c for _, c in seen]:
                    seen.append((sev or "WARNING / CRITICAL by threshold", code))
    return seen


def main():
    out = ["# Module and finding reference", "",
           "Generated from the source by `packaging/gen_docs.py`. Every finding has a stable code you can filter on "
           "in CSV/JSON/XLSX exports, Nagios output or the history database.", ""]
    for m in modules.all_modules():
        paths = sorted(glob.glob(os.path.join(ROOT, "lshme/modules/database/*.py"))) if m.NAME == "database" else [m.__file__]
        out += ["## %s — `%s` (weight %d)" % (m.TITLE, m.NAME, m.WEIGHT), "",
                " ".join(l.strip() for l in (m.__doc__ or "").strip().splitlines()), ""]
        seen = codes_in(paths)
        if m.NAME == "logs":
            seen = [(p[1], "LOG_" + p[0].upper()) for p in PATTERNS if p[0] != "ssh_fail"]
            seen += [("WARNING / CRITICAL by count", "LOG_SSH_FAILURES"), ("INFO", "LOG_REBOOTS"), ("UNKNOWN", "LOGS_UNAVAILABLE")]
        if seen:
            out += ["| Code | Severity |", "|---|---|"] + ["| `%s` | %s |" % (c, s) for s, c in seen] + [""]
    out += ["## Trends & Capacity Forecast — `trends`", "", "Added automatically from the history database on full runs.", "",
            "| Code | Severity |", "|---|---|"] + ["| `%s` | %s |" % (c, s) for s, c in codes_in([os.path.join(ROOT, "lshme/engine/history.py")])] + [""]
    drift = ["UID0_ACCOUNTS", "LOGIN_ACCOUNTS", "LISTENING_PORTS", "ENABLED_SERVICES", "MOUNTS", "CONFIG_HASHES", "CRON_FILES", "PACKAGES", "KERNEL"]
    sev = {"UID0_ACCOUNTS": "CRITICAL", "PACKAGES": "INFO", "KERNEL": "INFO"}
    out += ["## Configuration Drift — `drift`", "", "Compares each full run with the saved baseline (`healthcheck baseline save`).", "",
            "| Code | Severity |", "|---|---|"] + ["| `DRIFT_%s` | %s |" % (d, sev.get(d, "WARNING")) for d in drift] + [""]
    out += ["## Engine", "", "| Code | Severity | Meaning |", "|---|---|---|",
            "| `MODULE_ERROR` | UNKNOWN | A module raised an exception; other modules still ran |",
            "| `RUN_TIMEOUT` | UNKNOWN | The overall run time limit was reached before this module |", ""]
    path = os.path.join(ROOT, "docs", "MODULES.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(out) + "\n")
    print("wrote %s" % path)


if __name__ == "__main__":
    main()
