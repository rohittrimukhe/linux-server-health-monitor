# Linux Server Health Monitor Enterprise
# Copyright (c) 2026 LRS Services Pvt Ltd, Mumbai. All rights reserved.
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


def drive(keys, term="xterm", rows=24, cols=80):
    """Start 'healthcheck setup' on a pty, send each key, return (text drawn after start, exit code)."""
    home = tempfile.mkdtemp(prefix="lshme-pty-")
    master, slave = os.openpty()
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
    proc = subprocess.Popen([os.path.join(ROOT, "healthcheck"), "--home", home, "setup"], stdin=slave, stdout=slave,
                            stderr=slave, env=dict(os.environ, TERM=term), close_fds=True)
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
        pump(1.0)
        mark = len(buf[0])
        for k in keys:
            os.write(master, k.encode())
            pump(0.35)
        pump(0.3)
        try:
            code = proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
            code = None
    finally:
        os.close(master)
        shutil.rmtree(home, ignore_errors=True)
    return buf[0][mark:].decode("utf-8", "replace"), code


@unittest.skipUnless(HAVE_PTY and os.name == "posix", "needs a POSIX pseudo-terminal and curses")
class RealTerminalTests(unittest.TestCase):
    MODULES_FORM = "switches a module"  # text shown only by the second entry, Monitoring modules

    def test_down_arrow_in_every_common_encoding(self):
        for name, seq in (("ESC [ B (PuTTY, plink, Windows Terminal)", "\x1b[B"), ("ESC O B (application mode)", "\x1bOB"),
                          ("ESC [ 1;2 B (with Shift)", "\x1b[1;2B")):
            out, _ = drive([seq, "\r", "\x1b", "\x1b"])
            self.assertIn(self.MODULES_FORM, out, name)

    def test_up_arrow_and_paging(self):
        out, _ = drive(["\x1b[B", "\x1b[B", "\x1b[A", "\r", "\x1b", "\x1b"])  # down, down, up -> second entry
        self.assertIn(self.MODULES_FORM, out)

    def test_lone_escape_leaves_and_does_not_hang(self):
        _, code = drive(["\x1b"])
        self.assertEqual(code, 0)

    def test_escape_backs_out_of_a_form_then_the_menu(self):
        _, code = drive(["\r", "\x1b", "\x1b"])  # open identity, back, quit
        self.assertEqual(code, 0)

    def test_works_with_other_terminal_types(self):
        for term in ("xterm-256color", "linux", "screen", "vt100"):
            out, _ = drive(["\x1b[B", "\r", "\x1b", "\x1b"], term=term)
            self.assertIn(self.MODULES_FORM, out, term)

    def test_text_entry_and_save_through_real_keys(self):
        out, code = drive(["\r", "\r", "R", "T", "\r", "\x1b[21~", "\x1b"])  # edit Company, F10 (as plink sends it), quit
        self.assertEqual(code, 0)
        self.assertIn("Saved main.conf", out)


if __name__ == "__main__":
    unittest.main()
