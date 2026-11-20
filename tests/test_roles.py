# Linux Server Health Monitor Enterprise
# Copyright (c) 2026 LRS Services Pvt Ltd, Mumbai. All rights reserved.
"""Server roles, automatically watched critical services, and the default report title."""
import unittest

from fakes import FakeRunner
from lshme.core.model import RunResult
from lshme.ops import roles

FILES = ("sshd.service enabled\ncrond.service enabled\nlibvirtd.service enabled\npostgresql-15.service enabled\n"
         "mariadb.service disabled\nmongod.service disabled\npostgresql@.service static\nhttpd.service enabled\n")
RUNNING = "sshd.service loaded active running OpenSSH\nlibvirtd.service loaded active running libvirt\nmysqld.service loaded active running MySQL\n"


def runner(ps=""):
    return FakeRunner({"systemctl list-unit-files": (0, FILES), "systemctl list-units": (0, RUNNING), "ps": (0, ps)})


class RolesTest(unittest.TestCase):
    def test_kvm_and_database_services_become_critical(self):
        found = roles.auto_critical(runner())
        self.assertEqual(found, ["libvirtd", "mysqld", "postgresql-15"])     # enabled or running; disabled, template and web are left out

    def test_roles_in_a_stable_order(self):
        r = roles.detect(runner("/u01/app/oracle/bin/tnslsnr LISTENER -inherit\nora_pmon_ORCL\n"))
        self.assertEqual(r[0], "Oracle database server")
        self.assertIn("KVM host", r)
        self.assertIn("PostgreSQL server", r)

    def test_no_systemd_means_no_roles_and_no_critical_additions(self):
        r = FakeRunner({})
        self.assertEqual(roles.auto_critical(r), [])
        self.assertEqual(roles.detect(r), [])

    def test_title_brackets_include_only_what_is_known(self):
        run = RunResult("db01", 1, "daily")
        run.fqdn, run.ip = "db01.example.com", "10.0.0.5"
        self.assertEqual(roles.default_title("Daily Server Checklist", run), "Daily Server Checklist (db01.example.com, 10.0.0.5)")
        run.meta = {"roles": ["Oracle database server", "KVM host", "Web server"]}
        self.assertEqual(roles.default_title("Daily Server Checklist", run),
                         "Daily Server Checklist (db01.example.com, 10.0.0.5, Oracle database server + KVM host)")
        run.ip = ""
        self.assertEqual(roles.title_suffix("h", "", []), " (h)")
        self.assertEqual(roles.title_suffix("", "", []), "")


if __name__ == "__main__":
    unittest.main()
