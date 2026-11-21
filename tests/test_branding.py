# Linux Server Health Monitor Enterprise
# Copyright (c) 2026 LRS Services Pvt Ltd, Mumbai. All rights reserved.
import io
import os
import struct
import unittest
import zlib
from contextlib import redirect_stdout

from fakes import ROOT, FakeRunner, TempHome
from lshme.ops import branding


def png(w, h, depth=8, interlace=0, ctype=2):
    bpp = {0: 1, 2: 3, 6: 4}[ctype]
    raw = b"".join(b"\x00" + bytes([200, 30, 30, 255][:bpp]) * w for _ in range(h))

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, depth, ctype, 0, 0, interlace)) +
            chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def jpeg(w, h, comps=3):
    return b"\xff\xd8" + b"\xff\xc0" + struct.pack(">HBHHB", 17, 8, h, w, comps) + bytes(9) + b"\xff\xd9"


SERVERS = ("[defaults]\nuser = root\n\n[server:kvm2]\nhost = 10.0.0.2\n\n[server:db1]\nhost = 10.0.0.3\nuser = oracle\nport = 2222\n")


class BrandingTest(unittest.TestCase):
    def setUp(self):
        from lshme.app import App
        self.home = TempHome({"servers": SERVERS})
        self.app = App(ROOT, home=self.home.dir)

    def tearDown(self):
        self.home.cleanup()

    def test_what_is_accepted(self):
        d = branding.inspect(png(300, 100))
        self.assertEqual((d["kind"], d["width"], d["height"], d["ext"]), ("png", 300, 100, ".png"))
        self.assertEqual(branding.inspect(png(120, 40, ctype=6))["kind"], "png")             # with transparency
        self.assertEqual(branding.inspect(jpeg(600, 200))["ext"], ".jpg")
        for bad, word in ((b"", "empty"), (b"GIF89a" + bytes(50), "PNG or JPEG"), (png(50, 20, depth=16, ctype=0), "8-bit"),
                          (png(50, 20, interlace=1), "interlaced"), (png(1000, 800), "700,000 pixels"), (jpeg(100, 40, comps=4), "CMYK"),
                          (b"x" * (800 * 1024), "limit")):
            with self.assertRaises(ValueError) as cm:
                branding.inspect(bad)
            self.assertIn(word, str(cm.exception))

    def test_upload_sets_the_logo_every_report_uses(self):
        info = branding.save(self.app, png(300, 100))
        path = os.path.join(self.app.paths.data_dir, "branding", "customer_logo.png")
        self.assertEqual(info["path"], path)
        self.assertEqual(self.app.cfg.get("main", "general", "logo", ""), path)          # [general] logo: used by HTML, PDF, Excel, PowerPoint
        if os.name == "posix":
            self.assertEqual(oct(os.stat(path).st_mode & 0o777), oct(0o640))
        from lshme.core.config import Config
        self.assertEqual(Config(self.app.paths.config_dir).get("main", "general", "logo", ""), path)     # and it is saved
        branding.save(self.app, jpeg(600, 200))                                           # a JPEG replaces the PNG
        self.assertEqual(sorted(os.listdir(os.path.dirname(path))), ["customer_logo.jpg"])
        self.assertTrue(self.app.cfg.get("main", "general", "logo", "").endswith("customer_logo.jpg"))
        self.assertTrue(branding.remove(self.app))
        self.assertEqual(self.app.cfg.get("main", "general", "logo", ""), "")
        self.assertFalse(branding.remove(self.app))

    def test_a_logo_per_server(self):
        branding.save(self.app, png(300, 100))
        branding.save(self.app, jpeg(500, 150), "db1")
        with self.assertRaises(ValueError):
            branding.save(self.app, png(10, 10), "../etc")
        with self.assertRaises(ValueError):
            branding.save(self.app, png(10, 10), "nosuchserver")
        info = branding.listing(self.app)
        by = dict((s["name"], s) for s in info["servers"])
        self.assertEqual((by["kvm2"]["own"], by["kvm2"]["logo"]["kind"]), (False, "png"))        # no own logo: the default is used
        self.assertEqual((by["db1"]["own"], by["db1"]["logo"]["kind"]), (True, "jpeg"))
        self.assertTrue(info["default"]["path"].endswith("customer_logo.png"))
        self.assertEqual(self.app.cfg.get("main", "general", "logo", "").endswith("customer_logo.png"), True)   # a server logo never changes the default
        branding.remove(self.app, "db1")
        self.assertFalse(dict((s["name"], s) for s in branding.listing(self.app)["servers"])["db1"]["own"])

    def test_push_sends_each_server_its_logo(self):
        self.app.runner = FakeRunner({"ssh": (0, ""), "scp": (0, "")})
        res = branding.push(self.app)
        self.assertEqual([r["detail"] for r in res], ["no logo uploaded yet"] * 2)
        branding.save(self.app, png(300, 100))
        branding.save(self.app, jpeg(500, 150), "db1")
        self.app.runner = FakeRunner({"ssh": (0, ""), "scp": (0, "")})
        res = branding.push(self.app)
        self.assertEqual([(r["server"], r["ok"]) for r in res], [("kvm2", True), ("db1", True)])
        calls = self.app.runner.calls
        scp = [c for c in calls if c.startswith("scp")]
        self.assertTrue(scp[0].endswith("root@10.0.0.2:/var/lib/lshme/branding/customer_logo.png"))
        self.assertIn("-P 2222", scp[1])
        self.assertTrue(scp[1].endswith("oracle@10.0.0.3:/var/lib/lshme/branding/customer_logo.jpg"))    # its own JPEG
        self.assertTrue(any("vitals config set main general logo /var/lib/lshme/branding/customer_logo.jpg" in c for c in calls))
        failing = FakeRunner({"ssh": (255, "", "Permission denied (publickey)"), "scp": (0, "")})
        self.app.runner = failing
        res = branding.push(self.app, ["db1"])
        self.assertEqual((len(res), res[0]["ok"], res[0]["detail"]), (1, False, "Permission denied (publickey)"))

    def test_the_logo_is_on_the_pdf_and_html(self):
        import time
        from lshme import reports
        from test_periodic import make_run, seed
        branding.save(self.app, png(300, 100))
        run = make_run(time.time() - 60)
        seed(self.app.paths, run)
        _, d, files, _ = reports.generate(run, self.app.cfg, self.app.paths, ["pdf", "html"], "daily", "daily")
        pdf = open([f for f in files if f.endswith(".pdf")][0], "rb").read()
        self.assertGreaterEqual(pdf.count(b"/Subtype /Image"), 2)                          # LRS logo and the customer's logo
        html = open([f for f in files if f.endswith(".html")][0], encoding="utf-8").read()
        self.assertIn("data:image/png;base64", html)

    def test_cli(self):
        from lshme.ui import cli
        path = os.path.join(self.home.dir, "mylogo.png")
        with open(path, "wb") as f:
            f.write(png(200, 60))
        base = ["--home", self.home.dir, "--no-color", "logo"]
        buf = io.StringIO()
        with redirect_stdout(buf):
            self.assertEqual(cli.main(base + ["set", path]), 0)
            self.assertEqual(cli.main(base + ["show"]), 0)
            self.assertEqual(cli.main(base + ["remove"]), 0)
        out = buf.getvalue()
        self.assertIn("Stored", out)
        self.assertIn("200x60", out)
        self.assertEqual(cli.main(base + ["set", os.path.join(self.home.dir, "missing.png")]) , 2)


if __name__ == "__main__":
    unittest.main()


class FitTest(unittest.TestCase):
    def setUp(self):
        from lshme.app import App
        self.home = TempHome({"servers": SERVERS})
        self.app = App(ROOT, home=self.home.dir)

    def tearDown(self):
        self.home.cleanup()

    def test_a_logo_that_is_too_big_is_fitted_automatically(self):
        wide = png(2965, 333)                                              # the customer's real logo size: 987,000 pixels
        with self.assertRaises(ValueError):
            branding.inspect(wide)
        info = branding.save(self.app, wide)
        self.assertEqual((info["width"], info["height"]), (900, 101))     # fitted inside 900 x 300, same proportions
        w, h, px = branding.decode_png(open(info["path"], "rb").read())
        self.assertEqual((w, h), (900, 101))
        self.assertEqual(tuple(px[0:4]), (200, 30, 30, 255))              # the colours survive
        self.assertEqual(self.app.cfg.get("main", "general", "logo", ""), info["path"])

    def test_other_png_kinds_are_converted(self):
        info = branding.save(self.app, png(1500, 700, depth=16, ctype=2) if False else png(1200, 700, ctype=6))
        self.assertEqual(info["width"] <= 900 and info["height"] <= 300, True)
        gray = png(100, 40, ctype=0)
        w, h, px = branding.decode_png(gray)
        self.assertEqual((w, h, tuple(px[0:4])), (100, 40, (200, 200, 200, 255)))

    def test_a_small_logo_is_never_enlarged(self):
        w, h, px = branding.decode_png(png(300, 100))
        self.assertEqual(branding.fit_rgba(w, h, px)[:2], (300, 100))
        with self.assertRaises(ValueError):
            branding.save(self.app, b"GIF89a" + bytes(100))               # not a PNG or JPEG: the browser converts those, the command line does not
