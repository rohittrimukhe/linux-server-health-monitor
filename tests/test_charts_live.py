# Linux Server Health Monitor Enterprise
# Copyright (c) 2026 LRS Services Pvt Ltd, Mumbai. All rights reserved.
"""Charts (SVG, XLSX, PPTX), the watcher's metric history and the live page server."""
import json
import os
import threading
import unittest
import xml.dom.minidom
from urllib.error import HTTPError
from urllib.request import urlopen

from fakes import ROOT, TempHome, slurp
from lshme.ops import live, watch
from lshme.reports import charts


class SvgChartTest(unittest.TestCase):
    def test_donut_and_columns_are_valid_svg(self):
        for svg in (charts.donut([("A", 3, "#f00"), ("B", 1, "#0f0"), ("C", 0, "#00f")], "t"),
                    charts.donut([("Only", 5, "#f00")], "single"), charts.donut([], "empty"),
                    charts.columns([("/", 23.4), ("/u01", 95), ("/archive<x>", 81)], 100, "%", score_mode=False)):
            xml.dom.minidom.parseString(svg[:svg.rindex("</svg>") + 6])  # raises on malformed markup (columns() appends an HTML colour key)
        two = charts.donut([("A", 1, "#f00"), ("B", 1, "#0f0")], "t")
        self.assertEqual(two.count("<path"), 2)
        self.assertIn("&lt;x&gt;", charts.columns([("/archive<x>", 81)]))   # labels are escaped
        self.assertIn("#c62828", charts.columns([("/u01", 95)], score_mode=False))

    def test_html_report_contains_pies(self):
        from test_reports_mail import sample_run
        from lshme.reports import doc, html
        home = TempHome()
        d = doc.build(sample_run(), home.cfg, "technical")
        path = os.path.join(home.dir, "r.html")
        html.render(d, path)
        page = slurp(path)
        self.assertIn("Findings by severity", page)
        self.assertIn("Module status", page)
        self.assertIn("Filesystem usage", page)
        self.assertIn("worst first", page)
        home.cleanup()


class NativeChartTest(unittest.TestCase):
    def setUp(self):
        from test_reports_mail import sample_run
        from lshme import reports
        self.home = TempHome()
        _, _, self.files, self.notes = reports.generate(sample_run(), self.home.cfg, self.home.paths, ["xlsx", "pptx", "pdf"], "technical", "adhoc")

    def tearDown(self):
        self.home.cleanup()

    def test_excel_has_native_charts(self):
        try:
            import openpyxl
        except ImportError:
            self.skipTest("openpyxl not installed")
        wb = openpyxl.load_workbook([f for f in self.files if f.endswith(".xlsx")][0])
        self.assertIn("Charts", wb.sheetnames)
        kinds = sorted(type(c).__name__ for c in wb["Charts"]._charts)
        self.assertIn("DoughnutChart", kinds)
        self.assertIn("BarChart", kinds)

    def test_pptx_has_chart_slide(self):
        try:
            import pptx
        except ImportError:
            self.skipTest("python-pptx not installed")
        prs = pptx.Presentation([f for f in self.files if f.endswith(".pptx")][0])
        charts_found = [sh for s in prs.slides for sh in s.shapes if getattr(sh, "has_chart", False)]
        self.assertTrue(charts_found)


class MetricsTest(unittest.TestCase):
    def test_rolling_history(self):
        home = TempHome({"main": "[watch]\nconfirm = 1\nrotation_modules =\nsyslog = no\nalert_email = no\nmetrics_hours = 1\n",
                         "services": "[critical]\nservices =\n"})
        from lshme.app import App
        w = watch.Watcher(App(ROOT, home=home.dir))
        for i in range(5):
            w.tick(1000000.0 + i * 1200)  # 20 min apart; only the last hour is kept
        with open(w.metrics_path) as f:
            series = json.load(f)
        self.assertEqual([p["t"] for p in series], [1002400, 1003600, 1004800])
        self.assertIn("mem", series[-1])
        self.assertIsInstance(series[-1]["disk"], dict)
        self.assertLessEqual(len(series[-1]["disk"]), 8)
        home.cleanup()


class LiveServerTest(unittest.TestCase):
    def test_endpoints(self):
        home = TempHome()
        with open(os.path.join(home.paths.data_dir, "watch_metrics.json"), "w") as f:
            json.dump([{"t": 1, "cpu": 5}], f)
        httpd = live.make_server(home.paths.data_dir, "127.0.0.1", 0)
        port = httpd.server_address[1]
        th = threading.Thread(target=httpd.serve_forever)
        th.daemon = True
        th.start()
        base = "http://127.0.0.1:%d" % port
        try:
            r = urlopen(base + "/", timeout=5)
            self.assertIn("default-src 'self'", r.headers.get("Content-Security-Policy"))
            self.assertIn(b"charts.js", r.read())
            self.assertEqual(json.loads(urlopen(base + "/api/metrics", timeout=5).read().decode()), [{"t": 1, "cpu": 5}])
            self.assertEqual(urlopen(base + "/api/latest", timeout=5).read(), b"null")
            self.assertEqual(json.loads(urlopen(base + "/config.json", timeout=5).read().decode())["mode"], "http")
            for bad in ("/../config/smtp.conf", "/history.sqlite3", "/api/../data/latest.json", "/etc/passwd"):
                with self.assertRaises(HTTPError):
                    urlopen(base + bad, timeout=5)
        finally:
            httpd.shutdown()
            httpd.server_close()
        home.cleanup()

    def test_page_assets_csp_safe(self):
        js = slurp(os.path.join(ROOT, "lshme", "cockpit", "charts.js")) + slurp(os.path.join(ROOT, "lshme", "cockpit", "lshme.js"))
        self.assertNotIn('style="', js)        # inline style attributes are blocked by the page CSP
        self.assertNotIn("eval(", js)
        page = slurp(os.path.join(ROOT, "lshme", "cockpit", "index.html"))
        self.assertIn('src="charts.js"', page)
        self.assertNotIn("<script>", page)    # no inline scripts


if __name__ == "__main__":
    unittest.main()
