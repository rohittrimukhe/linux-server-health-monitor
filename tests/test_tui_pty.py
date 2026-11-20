# Linux Server Health Monitor Enterprise
# Copyright (c) 2026 Rohit Santosh Trimukhe. All rights reserved.
"""The real curses screens in a real pseudo-terminal: keys must work however the terminal sends them."""
import os
import select
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

from fakes import ROOT

try:
    import fcntl
    import struct
    import termios
    import curses  # noqa: F401
    HAVE_PTY = True
except ImportError:  # Windows, or Python built without curses
    HAVE_PTY = False


def drive(keys, term="xterm", rows=24, cols=80, args=("setup",), prog=None, home_files=None):
    """Start the program on a pty, send each key, return (text drawn after start, exit code, home folder contents)."""
    home = tempfile.mkdtemp(prefix="lshme-pty-")
    master, slave = os.openpty()
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
    env = dict(os.environ, TERM=term)
    env.pop("LSHME_PROG", None)
    launcher = os.path.join(ROOT, "healthcheck")
    if prog:  # run it under another name, as the installer's symlink does
        launcher = os.path.join(home + "-bin", prog)
        os.makedirs(os.path.dirname(launcher))
        os.symlink(os.path.join(ROOT, "healthcheck"), launcher)
    proc = subprocess.Popen([launcher, "--home", home] + list(args), stdin=slave, stdout=slave,
                            stderr=slave, env=env, close_fds=True)
    os.close(slave)
    buf = [b""]

    def pump(seconds):
        end = time.time() + seconds
        while time.time() < end:
            ready, _, _ = select.select([master], [], [], 0.05)
            if ready:
                try:
                    data = os.read(master, 65536)
                except OSError:
                    return
                if not data:
                    return
                buf[0] += data
    try:
        pump(1.2)
        mark = len(buf[0])
        for k in keys:
            os.write(master, k.encode())
            pump(0.35)
        pump(0.3)
        try:
            code = proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
            code = None
    finally:
        os.close(master)
    text = buf[0][mark:].decode("utf-8", "replace")
    full = buf[0].decode("utf-8", "replace")
    conf = {}
    cdir = os.path.join(home, "config")
    if os.path.isdir(cdir):
        for name in os.listdir(cdir):
            with open(os.path.join(cdir, name), encoding="utf-8") as f:
                conf[name] = f.read()
    shutil.rmtree(home, ignore_errors=True)
    shutil.rmtree(home + "-bin", ignore_errors=True)
    return text, code, conf, full


@unittest.skipUnless(HAVE_PTY and os.name == "posix", "needs a POSIX pseudo-terminal and curses")
class RealTerminalTests(unittest.TestCase):
    MODULES_FORM = "switches a module"  # text shown only by the second entry, Monitoring modules

    def test_down_arrow_in_every_common_encoding(self):
        for name, seq in (("ESC [ B (PuTTY, plink, Windows Terminal)", "\x1b[B"), ("ESC O B (application mode)", "\x1bOB"),
                          ("ESC [ 1;2 B (with Shift)", "\x1b[1;2B")):
            out, _, _, _ = drive([seq, "\r", "\x1b", "\x1b"])
            self.assertIn(self.MODULES_FORM, out, name)

    def test_up_arrow_and_paging(self):
        out, _, _, _ = drive(["\x1b[B", "\x1b[B", "\x1b[A", "\r", "\x1b", "\x1b"])  # down, down, up -> second entry
        self.assertIn(self.MODULES_FORM, out)

    def test_lone_escape_leaves_and_does_not_hang(self):
        _, code, _, _ = drive(["\x1b"])
        self.assertEqual(code, 0)

    def test_escape_backs_out_of_a_form_then_the_menu(self):
        _, code, _, _ = drive(["\r", "\x1b", "\x1b"])  # open identity, back, quit
        self.assertEqual(code, 0)

    def test_works_with_other_terminal_types(self):
        for term in ("xterm-256color", "linux", "screen", "vt100"):
            out, _, _, _ = drive(["\x1b[B", "\r", "\x1b", "\x1b"], term=term)
            self.assertIn(self.MODULES_FORM, out, term)

    def test_text_entry_and_save_through_real_keys(self):
        out, code, conf, _ = drive(["\r", "\r", "R", "T", "\r", "\x1b[21~", "\x1b"])  # edit Company, F10 (as plink sends it), quit
        self.assertEqual(code, 0)
        self.assertIn("RT", conf.get("main.conf", ""))

    def test_systemhealth_opens_the_first_run_setup_and_it_can_be_left(self):
        # no setup done yet: 'systemhealth' starts with the welcome page; Esc then 'y' leaves it, and Quit leaves the menu
        out, code, _, full = drive(["\x1b", "y", "\x1b"], args=(), prog="systemhealth")
        self.assertIn("Welcome to LRS Vitals", full)
        self.assertEqual(code, 0)

    def test_every_command_name_opens_the_same_program(self):
        for name in ("vitals", "systemhealth"):
            out, code, _, full = drive(["\x1b", "y", "\x1b"], args=(), prog=name)
            self.assertIn("Welcome to LRS Vitals", full, name)
            self.assertEqual(code, 0, name)

    def test_systemhealth_menu_and_dashboard_in_a_real_terminal(self):
        # skip the first-run page (Leave), then open the live dashboard, switch views, and quit
        keys = ["\x1b[D", "\r", "y"]                 # welcome: Leave, confirm
        keys += ["\r"]                               # the menu: Live dashboard
        keys += ["\x1b[B"] * 0 + ["2", "3", "4", "5", "1", "\x1b"]  # views, then back
        keys += ["\x1b"]                             # quit the menu
        out, code, _, full = drive(keys, args=(), prog="systemhealth")
        for text in ("Live dashboard", "Overview", "CPU & load", "Filesystems", "Processes", "Memory"):
            self.assertIn(text, full, text)
        self.assertEqual(code, 0)

    def test_systemhealth_help_page_works_without_a_terminal_requirement(self):
        out, code, _, full = drive([], args=("help",), prog="systemhealth")
        self.assertIn("systemhealth", full)


if __name__ == "__main__":
    unittest.main()
