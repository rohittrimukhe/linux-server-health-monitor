# Linux Server Health Monitor Enterprise
# Copyright (c) 2026 LRS Services Pvt Ltd, Mumbai. All rights reserved.
"""Static guards for RHEL/Oracle Linux 7 (Python 3.6) compatibility and read-only safety."""
import ast
import os
import re
import sys
import unittest

from fakes import ROOT

PKG = os.path.join(ROOT, "lshme")

# APIs/syntax newer than Python 3.6 that would break on RHEL/OL 7
FORBIDDEN = [
    (r"\.fromisoformat\(", "datetime.fromisoformat is 3.7+"),
    (r"capture_output\s*=", "subprocess capture_output is 3.7+"),
    (r"\btext\s*=\s*True", "subprocess text= is 3.7+"),
    (r"^\s*from dataclasses|^\s*import dataclasses", "dataclasses is 3.7+"),
    (r"from __future__ import annotations", "3.7+"),
    (r"\.removeprefix\(|\.removesuffix\(", "3.9+"),
    (r"functools\.cache\b|@cache\b", "3.9+"),
    (r"math\.prod\(|math\.isqrt\(", "3.8+"),
    (r"asyncio\.run\(", "3.7+"),
    (r"\bimport importlib\.resources|importlib\.metadata", "3.7/3.8+"),
    (r"\.reconfigure\(", "TextIOWrapper.reconfigure is 3.7+"),
    (r"time\.time_ns\(|perf_counter_ns", "3.7+"),
    (r"zoneinfo", "3.9+"),
    (r"\bmatch\s+\S+:\s*$", "match statement is 3.10+"),
]

# commands a read-only monitor must never execute (argument lists are scanned)
DANGEROUS = re.compile(r'\[\s*"(rm|reboot|shutdown|mkfs[\w.]*|fsck|lvremove|lvextend|lvreduce|vgremove|kill|pkill|'
                       r'virsh"\s*,\s*"(destroy|start|shutdown|reboot|undefine)|systemctl"\s*,\s*"(restart|stop|start|kill))"')


def sources():
    for root, _, files in os.walk(PKG):
        for f in files:
            if f.endswith(".py"):
                yield os.path.join(root, f)


class CompatTest(unittest.TestCase):
    def test_grammar(self):
        version = (3, 7) if sys.version_info >= (3, 13) else (3, 6)  # 3.13 dropped feature_version 3.6
        for path in sources():
            with open(path, encoding="utf-8") as f:
                src = f.read()
            try:
                ast.parse(src, filename=path, feature_version=version)
            except SyntaxError as e:
                self.fail("%s is not Python %d.%d compatible: %s" % (path, version[0], version[1], e))
            self.assertNotIn(":=", src, path)

    def test_no_newer_apis(self):
        for path in sources():
            with open(path, encoding="utf-8") as f:
                src = f.read()
            for rx, why in FORBIDDEN:
                m = re.search(rx, src, re.M)
                self.assertIsNone(m, "%s: %s (%s)" % (path, m.group(0) if m else "", why))

    def test_text_io_is_utf8(self):
        for path in sources():
            with open(path, encoding="utf-8") as f:
                for n, line in enumerate(f, 1):
                    for m in re.finditer(r"(?<![\w.])open\(([^\n]*)", line):
                        args = m.group(1)
                        if '"rb"' in args or '"wb"' in args:
                            continue
                        self.assertIn('encoding="utf-8"', args, "%s:%d text open() without utf-8: %s" % (path, n, line.strip()))
            self.assertNotIn("universal_newlines=True", open(path, encoding="utf-8").read(), path)

    def test_read_only_commands(self):
        allowed = {os.path.join(PKG, "ops", "scheduler.py")}  # manages its own timers only
        for path in sources():
            if path in allowed:
                continue
            with open(path, encoding="utf-8") as f:
                src = f.read()
            m = DANGEROUS.search(src)
            self.assertIsNone(m, "%s runs a state-changing command: %s" % (path, m.group(0) if m else ""))

    def test_scheduler_only_touches_lshme_units(self):
        with open(os.path.join(PKG, "ops", "scheduler.py"), encoding="utf-8") as f:
            src = f.read()
        for m in re.finditer(r'"systemctl",\s*"(enable|disable)"[^\]]*\]', src):
            literal_ok = "lshme-" in m.group(0)
            guarded = m.group(0).endswith(", f]") and 'f.startswith("lshme-")' in src
            self.assertTrue(literal_ok or guarded, m.group(0))


if __name__ == "__main__":
    unittest.main()
