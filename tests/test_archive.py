# Linux Server Health Monitor Enterprise
# Copyright (c) 2026 LRS Services Pvt Ltd, Mumbai. All rights reserved.
import gzip
import io
import json
import os
import tarfile
import time
import unittest
from contextlib import redirect_stdout

from fakes import TempHome
from lshme.delivery import mailer
from lshme.ops import archive, reportlist, retention

NL = chr(10)


def make_report(home, host, cat, stamp, age_days, status="WARNING"):
    d = os.path.join(home.paths.report_dir, host, cat, stamp)
    os.makedirs(d)
    lines = ["line %d of %s %s" % (i, host, stamp) for i in range(1, 101)] + ["disk /u01 is 93% full on " + host]
    with open(os.path.join(d, "report.txt"), "w", encoding="utf-8") as f:
        f.write(NL.join(lines) + NL)
    with open(os.path.join(d, "report.html"), "w", encoding="utf-8") as f:
        f.write("<html><style>.x{}</style><body><h1>Health of %s</h1><p>tablespace USERS is 97%% used</p>%s</body></html>" % (host, "<p>filler</p>" * 300))
    with open(os.path.join(d, "report.json"), "w", encoding="utf-8") as f:
        json.dump({"host": host, "health": {"score": 77, "status": status, "counts": {"CRITICAL": 0, "WARNING": 2}}, "modules": []}, f)
    with open(os.path.join(d, "report.xlsx"), "wb") as f:
        f.write(b"PK" + b"x" * 500)
    t = time.time() - age_days * 86400
    os.utime(d, (t, t))
    return d


class Holder(object):
    def __init__(self, home):
        self.cfg, self.paths = home.cfg, home.paths


class ArchiveTest(unittest.TestCase):
    def setUp(self):
        self.home = TempHome({"retention": "[retention]" + NL + "compress_after_days = 1" + NL})
        self.old = make_report(self.home, "db01", "daily", "20261101_090000", 5)
        self.mid = make_report(self.home, "db01", "daily", "20261110_090000", 3)
        self.new = make_report(self.home, "db01", "daily", "20261120_090000", 0.1)

    def tearDown(self):
        self.home.cleanup()

    def test_compress_file_by_file_like_rotated_logs(self):
        out = archive.compress_old(self.home.cfg, self.home.paths)
        self.assertEqual((out["folders"], out["files"]), (2, 6))       # txt, html, json in two old folders; newest untouched
        self.assertGreater(out["saved"], 0)
        names = sorted(os.listdir(self.old))
        self.assertEqual(names, ["report.html.gz", "report.json.gz", "report.txt.gz", "report.xlsx"])
        self.assertEqual(sorted(os.listdir(self.new)), ["report.html", "report.json", "report.txt", "report.xlsx"])
        with gzip.open(os.path.join(self.old, "report.txt.gz"), "rt", encoding="utf-8") as f:   # what zcat sees
            self.assertIn("disk /u01 is 93% full", f.read())
        self.assertGreater(time.time() - os.path.getmtime(self.old), 4 * 86400)              # folder keeps its age
        self.assertEqual(archive.compress_old(self.home.cfg, self.home.paths)["files"], 0)     # nothing left to do

    def test_read_tail_and_grep_without_unpacking(self):
        archive.compress_old(self.home.cfg, self.home.paths)
        p = os.path.join(self.old, "report.txt")
        self.assertFalse(os.path.exists(p))
        self.assertIn("line 1 of db01", archive.read_text(p))
        self.assertEqual(archive.tail_lines(p, 2), ["line 100 of db01 20261101_090000", "disk /u01 is 93% full on db01"])
        self.assertEqual(archive.original_size(p), len(archive.read_bytes(p)))
        hits = list(archive.grep(self.home.paths, r"93% full"))
        self.assertEqual(len(hits), 3)
        self.assertTrue(all(h[1] == "report.txt" for h in hits))
        self.assertFalse(any(f.endswith(".tmp") for f in os.listdir(self.old)))

    def test_report_list_and_saved_run_read_compressed_json(self):
        archive.compress_old(self.home.cfg, self.home.paths)
        rows = dict((r["dir"], r) for r in reportlist.list_reports(self.home.paths))
        self.assertEqual(rows[self.old]["files"], ["report.html", "report.json", "report.txt", "report.xlsx"])
        self.assertEqual((rows[self.old]["score"], rows[self.old]["status"]), (77, "WARNING"))

    def test_resolve_and_pick(self):
        rid, d = archive.resolve(self.home.paths, "latest")
        self.assertEqual(d, self.new)
        self.assertEqual(archive.resolve(self.home.paths, "20261110")[1], self.mid)
        self.assertEqual(archive.resolve(self.home.paths, "latest:daily")[1], self.new)
        with self.assertRaises(ValueError):
            archive.resolve(self.home.paths, "202611")                  # three reports match
        self.assertTrue(archive.pick_file(self.old).endswith("report.txt"))
        self.assertTrue(archive.pick_file(self.old, "html").endswith("report.html"))

    def test_index_and_search(self):
        archive.compress_old(self.home.cfg, self.home.paths)
        ix = archive.Index(self.home.paths)
        try:
            ix.rebuild()
            s = ix.stats()
            self.assertEqual((s["reports"], s["files"], s["compressed_files"]), (3, 12, 6))
            self.assertGreater(s["original"], s["stored"])
            hits = ix.search("tablespace USERS")
            self.assertEqual(len(hits), 3)
            self.assertEqual(hits[0]["rid"], "db01/daily/20261120_090000")       # newest first
            self.assertNotIn("<p>", hits[0]["snippet"])
            self.assertEqual(ix.search("nothing-like-this"), [])
            self.assertEqual(len(ix.listing(category="daily")), 3)
        finally:
            ix.close()
        # retention deletes a report: the index follows
        acts = [{"action": "delete", "path": self.old, "age_days": 5, "bytes": 1, "reason": "t"}]
        retention.apply(self.home.cfg, self.home.paths, acts)
        ix = archive.Index(self.home.paths)
        self.assertEqual(ix.stats()["reports"], 2)
        ix.close()

    def test_old_tarballs_become_readable_folders(self):
        tgz = self.mid + ".tar.gz"
        with tarfile.open(tgz, "w:gz") as tar:
            tar.add(self.mid, arcname=os.path.basename(self.mid))
        t = time.time() - 3 * 86400
        os.utime(tgz, (t, t))
        import shutil
        shutil.rmtree(self.mid)
        archive.compress_old(self.home.cfg, self.home.paths)
        self.assertFalse(os.path.exists(tgz))
        self.assertIn("report.txt.gz", os.listdir(self.mid))
        self.assertIn("93% full", archive.read_text(os.path.join(self.mid, "report.txt")))

    def test_email_a_compressed_report(self):
        archive.compress_old(self.home.cfg, self.home.paths)
        for k, v in (("host", "smtp.example.com"), ("from", "vitals@example.com"), ("to", "ops@example.com"), ("retries", "1")):
            self.home.cfg.set("smtp", "smtp", k, v)
        sent = []
        real = mailer._send_once
        mailer._send_once = lambda ms, msg, rcpt: sent.append((msg, rcpt))
        try:
            res = archive.email_report(Holder(self.home), self.old, ["dba@example.com"], ["txt", "json"])
        finally:
            mailer._send_once = real
        self.assertTrue(res["ok"], res)
        self.assertEqual(sorted(res["attached"]), ["report.json", "report.txt"])
        msg, rcpt = sent[0]
        self.assertEqual(rcpt, ["dba@example.com"])
        att = dict((p.get_filename(), p.get_content()) for p in msg.iter_attachments())
        self.assertIn("93% full", att["report.txt"])
        self.assertIn("WARNING", str(msg["Subject"]) + msg.get_body(("plain",)).get_content())

    def test_cli(self):
        from lshme.ui import cli
        archive.compress_old(self.home.cfg, self.home.paths)
        base = ["--home", self.home.dir, "--no-color", "archive"]
        buf = io.StringIO()
        with redirect_stdout(buf):
            self.assertEqual(cli.main(base + ["tail", "20261101", "txt", "-n", "1"]), 0)
            self.assertEqual(cli.main(base + ["search", "USERS"]), 0)
            self.assertEqual(cli.main(base + ["stats"]), 0)
        out = buf.getvalue()
        self.assertIn("disk /u01 is 93% full on db01", out)
        self.assertIn("db01/daily/20261110_090000/report", out)
        self.assertIn("compressed", out)


if __name__ == "__main__":
    unittest.main()
