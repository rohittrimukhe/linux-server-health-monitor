# Linux Server Health Monitor Enterprise
# Copyright (c) 2026 LRS Services Pvt Ltd, Mumbai. All rights reserved.
import os
import socket
import socketserver
import threading
import time
import unittest
import zipfile

from fakes import slurp, TempHome
from lshme import reports
from lshme.core.model import ModuleResult, RunResult
from lshme.delivery import mailer
from lshme.engine import scoring


def sample_run():
    r = RunResult("db01", days=7, schedule="weekly")
    r.fqdn, r.ip, r.version = "db01.example.com", "10.0.0.11", "test"
    r.meta = {"company": "Example Ltd", "customer": "ACME", "environment": "Production"}
    st = ModuleResult("storage", "Storage & Filesystems", 20)
    st.data = {"filesystems": [{"mountpoint": "/", "used_percent": 45.0}, {"mountpoint": "/oracle", "used_percent": 93.0}]}
    st.table("Filesystems", ["Mount", "Use%", "Status"], [["/", "45%", "OK"], ["/oracle", "93%", "CRITICAL"]], kind="summary")
    st.add("CRITICAL", "DISK_USAGE", "/oracle", "Filesystem /oracle is 93% full", evidence="df output",
           recommendation="Extend the filesystem.")
    st.metric("fs.used_percent:/oracle", 93)
    db = ModuleResult("database", "Databases", 15)
    db.add("WARNING", "ORACLE_CONNECT", "oracle:ORCL", "check failed for scott/Tiger123@ORCL",
           evidence="connect scott/Tiger123@ORCL\nORA-01017", recommendation="Fix credentials.")
    db.table("Oracle — tablespaces", ["Tablespace", "Used%"], [["USERS", "97%"]] + [["T%d" % i, "1%"] for i in range(40)], kind="summary")
    sk = ModuleResult("kvm", "KVM", 10)
    sk.status = "SKIPPED"
    r.modules = [st, db, sk]
    scoring.score_run(r)
    r.trends = {"score_history": [(time.time() - 86400 * i, 60 + i) for i in range(5, 0, -1)]}
    return r


class ReportTest(unittest.TestCase):
    def setUp(self):
        self.home = TempHome()

    def tearDown(self):
        self.home.cleanup()

    def generate(self, rtype):
        _, outdir, files, notes = reports.generate(sample_run(), self.home.cfg, self.home.paths, list(reports.FORMATS), rtype, "weekly")
        return outdir, files, notes

    def check_files(self, files):
        by = dict((os.path.splitext(f)[1], f) for f in files)
        for ext in (".html", ".pdf", ".xlsx", ".pptx", ".txt", ".csv", ".json"):
            self.assertIn(ext, by)
            self.assertGreater(os.path.getsize(by[ext]), 50)
        with open(by[".pdf"], "rb") as f:
            self.assertTrue(f.read(5) == b"%PDF-")
        for ext in (".xlsx", ".pptx"):
            self.assertIsNone(zipfile.ZipFile(by[ext]).testzip())
        for f in files:  # secrets must never reach a report
            if f.endswith((".html", ".txt", ".csv", ".json")):
                with open(f, encoding="utf-8") as fh:
                    self.assertNotIn("Tiger123", fh.read(), f)
        try:
            import openpyxl
            wb = openpyxl.load_workbook(by[".xlsx"])
            self.assertIn("Findings", wb.sheetnames)
        except ImportError:
            pass
        try:
            import pptx
            self.assertGreaterEqual(len(pptx.Presentation(by[".pptx"]).slides), 4)
        except ImportError:
            pass
        return by

    def test_all_formats_optional_libs(self):
        for rtype in ("executive", "technical", "full"):
            outdir, files, notes = self.generate(rtype)
            self.check_files(files)
            self.assertIn(os.path.join("db01", "weekly"), outdir)

    def test_all_formats_builtin_fallbacks(self):
        os.environ["LSHME_NO_OPTIONAL"] = "1"
        try:
            outdir, files, notes = self.generate("technical")
            self.check_files(files)
            self.assertEqual(len([n for n in notes if "built-in" in n or "text slides" in n]), 3)
        finally:
            del os.environ["LSHME_NO_OPTIONAL"]

    def test_executive_limits_rows_and_html_content(self):
        _, files, _ = self.generate("executive")
        html = slurp([f for f in files if f.endswith(".html")][0])
        self.assertIn("Example Ltd", html)
        self.assertIn("/oracle", html)
        self.assertIn("rows shown", html)  # 41-row table trimmed in executive view

    def test_unknown_format_rejected(self):
        with self.assertRaises(ValueError):
            reports.parse_formats("pdf,docx")
        self.assertEqual(reports.parse_formats("all"), list(reports.FORMATS))


# --- a tiny SMTP server for delivery tests ---------------------------------
class SMTPHandler(socketserver.StreamRequestHandler):
    def handle(self):
        srv = self.server
        w = lambda s: self.wfile.write((s + "\r\n").encode())
        w("220 test ESMTP")
        data_mode, buf, rcpts = False, [], []
        while True:
            line = self.rfile.readline()
            if not line:
                return
            text = line.decode("utf-8", "replace").rstrip("\r\n")
            if data_mode:
                if text == ".":
                    srv.messages.append({"rcpts": rcpts, "data": "\n".join(buf)})
                    data_mode, buf = False, []
                    w("250 queued")
                else:
                    buf.append(text)
                continue
            cmd = text.upper()
            if cmd.startswith("EHLO") or cmd.startswith("HELO"):
                w("250-test\r\n250 SIZE 100000000")
            elif cmd.startswith("MAIL FROM"):
                w("250 ok")
            elif cmd.startswith("RCPT TO"):
                addr = text.split(":", 1)[1].strip(" <>")
                if addr.startswith("reject"):
                    w("550 no such user")
                else:
                    rcpts.append(addr)
                    w("250 ok")
            elif cmd == "DATA":
                data_mode = True
                w("354 go")
            elif cmd == "QUIT":
                w("221 bye")
                return
            else:
                w("250 ok")


class SMTPServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


class MailTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = SMTPServer(("127.0.0.1", 0), SMTPHandler)
        cls.srv.messages = []
        cls.port = cls.srv.server_address[1]
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()

    def settings(self, extra=""):
        home = TempHome({"smtp": "[smtp]\nhost = 127.0.0.1\nport = %d\nsecurity = none\nfrom = lshme@example.com\n"
                                 "to = ops@example.com\ncc = lead@example.com\nbcc = audit@example.com\ncritical_to = oncall@example.com\n"
                                 "retries = 2\nretry_delay = 0\n%s" % (self.port, extra)})
        ms = mailer.MailSettings(home.cfg)
        home.cleanup()
        return ms

    def test_send_with_attachments_and_critical_recipients(self):
        self.srv.messages[:] = []
        ms = self.settings("attach_formats = pdf,html\nmax_attachment_mb = 1\n")
        d = TempHome()
        small = os.path.join(d.dir, "r.pdf")
        big = os.path.join(d.dir, "big.html")
        skipped_fmt = os.path.join(d.dir, "r.json")
        with open(small, "wb") as f:
            f.write(b"%PDF-1.4 test")
        with open(big, "wb") as f:
            f.write(b"x" * (2 * 1024 * 1024))
        with open(skipped_fmt, "w") as f:
            f.write("{}")
        msg, rcpts, attached, skipped = mailer.build_message(ms, "subj", "body", "<b>body</b>", [small, big, skipped_fmt], "CRITICAL")
        self.assertEqual(attached, ["r.pdf"])
        self.assertTrue(skipped and "big.html" in skipped[0])
        self.assertEqual(sorted(rcpts), sorted(["ops@example.com", "oncall@example.com", "lead@example.com", "audit@example.com"]))
        res = mailer.send(ms, msg, rcpts, d.paths.log_dir)
        self.assertTrue(res["ok"], res)
        self.assertEqual(len(self.srv.messages), 1)
        data = self.srv.messages[0]["data"]
        self.assertIn("r.pdf", data)
        self.assertNotIn("audit@example.com", data.split("\n\n")[0])  # BCC not in headers
        self.assertTrue(os.path.exists(os.path.join(d.paths.log_dir, "email.log")))
        d.cleanup()

    def test_test_email(self):
        self.srv.messages[:] = []
        res = mailer.send_test(self.settings())
        self.assertTrue(res["ok"], res)

    def test_retry_then_fail_on_closed_port(self):
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()
        ms = self.settings()
        ms.port = port
        sleeps = []
        msg, rcpts, _, _ = mailer.build_message(ms, "s", "b", None, [])
        res = mailer.send(ms, msg, rcpts, sleep=sleeps.append)
        self.assertFalse(res["ok"])
        self.assertEqual(res["attempts"], 2)
        self.assertEqual(len(sleeps), 1)

    def test_recipient_refused_is_not_retried(self):
        ms = self.settings()
        msg, _, _, _ = mailer.build_message(ms, "s", "b", None, [])
        res = mailer.send(ms, msg, ["reject@example.com"], sleep=lambda s: None)
        self.assertFalse(res["ok"])
        self.assertEqual(res["attempts"], 1)

    def test_policy(self):
        self.assertFalse(mailer.should_send("issues", "HEALTHY")[0])
        self.assertTrue(mailer.should_send("issues", "WARNING")[0])
        self.assertFalse(mailer.should_send("critical", "WARNING")[0])
        self.assertFalse(mailer.should_send("always", "CRITICAL", maintenance=True)[0])
        self.assertTrue(mailer.should_send("never", "CRITICAL", force=True)[0])

    def test_validation(self):
        home = TempHome({"smtp": "[smtp]\nusername = u\n"})
        probs = mailer.MailSettings(home.cfg).validate()
        home.cleanup()
        self.assertTrue(any("host" in p for p in probs))
        self.assertTrue(any("password" in p for p in probs))


if __name__ == "__main__":
    unittest.main()
