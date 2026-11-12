"""Live PostgreSQL integration test (runs only as root where PostgreSQL server binaries exist)."""
import glob
import os
import pwd
import shutil
import subprocess
import tempfile
import time
import unittest

from fakes import TempHome, codes
from lshme.core.model import ModuleResult
from lshme.core.runner import Runner
from lshme.core.context import Context
from lshme.modules import database

BINS = sorted(glob.glob("/usr/lib/postgresql/*/bin")) + sorted(glob.glob("/usr/pgsql-*/bin"))


def available():
    if not BINS or not hasattr(os, "geteuid") or os.geteuid() != 0 or not shutil.which("psql"):
        return False
    try:
        pwd.getpwnam("postgres")
        return True
    except KeyError:
        return False


@unittest.skipUnless(available(), "needs root, a 'postgres' user and PostgreSQL server binaries")
class LivePostgresTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.mkdtemp(prefix="lshme-pg-")
        os.chmod(cls.dir, 0o755)
        uid = pwd.getpwnam("postgres").pw_uid
        os.chown(cls.dir, uid, -1)
        b = BINS[-1]
        as_pg = ["runuser", "-u", "postgres", "--"]
        subprocess.check_call(as_pg + [b + "/initdb", "-D", cls.dir + "/data", "-A", "trust", "-U", "postgres"],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        cls.port = "55433"
        subprocess.check_call(as_pg + [b + "/pg_ctl", "-D", cls.dir + "/data", "-w", "-l", cls.dir + "/log",
                                       "-o", "-p %s -k %s -c listen_addresses=''" % (cls.port, cls.dir), "start"],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        cls.psql = ["psql", "-h", cls.dir, "-p", cls.port, "-U", "postgres", "-X", "-q"]
        subprocess.check_call(cls.psql + ["-c", "create table t(i int); select pg_create_physical_replication_slot('idle_slot');"],
                              stdout=subprocess.DEVNULL)

    @classmethod
    def tearDownClass(cls):
        subprocess.call(["runuser", "-u", "postgres", "--", BINS[-1] + "/pg_ctl", "-D", cls.dir + "/data", "stop", "-m", "immediate"],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        shutil.rmtree(cls.dir, ignore_errors=True)

    def test_adapter_against_real_server(self):
        holder = subprocess.Popen(self.psql + ["-c", "begin; lock table t in access exclusive mode; select pg_sleep(20); commit;"],
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(1)
        waiter = subprocess.Popen(self.psql + ["-c", "select * from t;"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(2)
        try:
            home = TempHome({"database": "[discovery]\npostgresql_autodiscover = no\n[postgresql:live]\nhost = %s\nport = %s\nuser = postgres\n" % (self.dir, self.port),
                             "thresholds": "[postgresql]\nlong_query_minutes = 0\n"})
            ctx = Context(home.cfg, home.paths, Runner(), 1)
            res = ModuleResult("database", "Databases", 15)
            database.run(ctx, res)
            c = codes(res)
            self.assertNotIn("POSTGRES_UNAVAILABLE", c, [f.evidence for f in res.findings])
            self.assertEqual(c.get("POSTGRES_LOCK_WAITS"), "WARNING")
            self.assertEqual(c.get("POSTGRES_INACTIVE_SLOT"), "WARNING")
            self.assertEqual(c.get("POSTGRES_LONG_QUERIES"), "WARNING")
            inst = res.data["instances"][0]
            self.assertEqual(inst["role"], "primary")
            self.assertIn("PostgreSQL", inst["version"])
            home.cleanup()
        finally:
            holder.kill()
            waiter.kill()
            holder.wait()
            waiter.wait()


if __name__ == "__main__":
    unittest.main()
