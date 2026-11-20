# Linux Server Health Monitor Enterprise
# Copyright (c) 2026 Rohit Santosh Trimukhe. All rights reserved.
"""The HTML report: plain-language summary, navigation, controls, and that absent modules are not reported."""
import os
import re
import unittest

from fakes import ROOT, FakeRunner, TempHome
from lshme.app import App
from lshme.core.context import Context
from lshme.core.model import ModuleResult, RunResult
from lshme.engine import pipeline, scoring
from lshme.reports import doc as docmod
from lshme.reports import html


def make_run(crit=1, warn=2):
    run = RunResult("web01")
    a = ModuleResult("storage", "Storage & Filesystems", 10)
    for i in range(crit):
        a.add("CRITICAL", "DISK_FULL", "/data%d" % i, "Filesystem /data%d is 95%% full" % i, recommendation="Free space on /data%d." % i)
    b = ModuleResult("services", "Services & Processes", 10)
    for i in range(warn):
        b.add("WARNING", "SVC", "svc%d" % i, "Service svc%d restarted 4 times" % i, recommendation="Check svc%d logs." % i)
    c = ModuleResult("network", "Network & Connectivity", 10)
    run.modules = [a, b, c]
    scoring.score_run(run)
    return run


class ReportUiTests(unittest.TestCase):
    def setUp(self):
        self.home = TempHome()
        self.app = App(ROOT, home=self.home.dir)

    def tearDown(self):
        self.home.cleanup()

    def render(self, run):
        d = docmod.build(run, self.app.cfg, "technical")
        path = os.path.join(self.home.dir, "r.html")
        html.render(d, path)
        with open(path, encoding="utf-8") as f:
            return f.read()

    def test_summary_names_the_problem_and_the_first_step(self):
        text = self.render(make_run())
        self.assertIn('id="summary"', text)
        self.assertIn("web01 needs attention: 1 critical and 2 warning finding(s).", text)
        self.assertIn("Filesystem /data0 is 95% full", text)
        self.assertIn("Do this first:", text)
        self.assertIn("3 areas checked", text)

    def test_a_healthy_server_says_so(self):
        text = self.render(make_run(0, 0))
        self.assertIn("web01 is healthy. There are no warnings or critical findings.", text)
        self.assertNotIn("Do this first", text)

    def test_warnings_only(self):
        self.assertIn("is working, with 2 warning(s) worth a look", self.render(make_run(0, 2)))

    def test_navigation_links_to_every_area_and_section(self):
        text = self.render(make_run())
        nav = re.search(r'<nav class="toc".*?</nav>', text, re.S).group(0)
        for target in ("#summary", "#issues", "#actions", "#areas", "#mod-storage-filesystems", "#mod-services-processes", "#mod-network-connectivity"):
            self.assertIn('href="%s"' % target, nav)
            self.assertIn('id="%s"' % target[1:], text)  # every link has somewhere to go
        self.assertIn('class="m-CRITICAL" href="#mod-storage-filesystems"', nav)  # colour tells the status
        self.assertIn('class="m-WARNING" href="#mod-services-processes"', nav)
        self.assertIn('class="m-OK" href="#mod-network-connectivity"', nav)

    def test_controls_and_script_are_inside_the_page(self):
        text = self.render(make_run(1, 5))
        for ident in ('id="exp"', 'id="col"', 'id="prt"', 'id="flt"'):
            self.assertIn(ident, text)
        self.assertLess(text.index("<script>"), text.index("</body>"))
        self.assertTrue(text.rstrip().endswith("</html>"))
        self.assertIn("beforeprint", text)  # every section opens when printing

    def test_the_filter_box_only_appears_when_there_is_much_to_filter(self):
        self.assertNotIn('id="flt"', self.render(make_run(1, 1)))

    def test_html_in_findings_is_escaped(self):
        run = make_run(0, 0)
        run.modules[0].add("CRITICAL", "X", "c", "<script>alert(1)</script>", recommendation="<b>bold</b>")
        scoring.score_run(run)
        text = self.render(run)
        self.assertNotIn("<script>alert(1)</script>", text)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", text)

    def test_areas_with_nothing_to_check_are_not_in_the_report(self):
        from lshme import modules as modreg
        ctx = Context(self.app.cfg, self.app.paths, FakeRunner())
        run = pipeline.run_modules(ctx, [modreg.load("kvm"), modreg.load("cluster"), modreg.load("cpu_memory")])
        scoring.score_run(run)
        text = self.render(run)
        self.assertNotIn("KVM Virtualisation", text)
        self.assertNotIn("High Availability Cluster", text)
        self.assertNotIn("SKIPPED", text)


if __name__ == "__main__":
    unittest.main()
