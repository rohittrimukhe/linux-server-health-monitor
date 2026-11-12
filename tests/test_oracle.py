import time
import unittest

from fakes import FakeRunner, codes, fixture, make_ctx
from lshme.core.model import ModuleResult
from lshme.modules import database
from lshme.modules.database import oracle

ALERT = "/u01/app/oracle/diag/rdbms/orcl/ORCL/trace/alert_ORCL.log"


def alert_text():
    old = time.strftime("%a %b %d %H:%M:%S %Y", time.localtime(time.time() - 10 * 86400))
    new = time.strftime("%a %b %d %H:%M:%S %Y", time.localtime(time.time() - 3600))
    iso = time.strftime("%Y-%m-%dT%H:%M:%S.000000+00:00", time.localtime(time.time() - 1800))
    return "\n".join([old, "ORA-00600: internal error code (old, outside window)", new,
                      "ORA-00600: internal error code, arguments: [kdsgrp1]", "ORA-01555: snapshot too old", iso,
                      "ORA-03136: inbound connection timed out"])


def make_runner(sqlplus_out, lsnr_rc=0):
    lsnr = ('Service "ORCL" has 1 instance(s).\n  Instance "ORCL", status READY, has 1 handler(s)\n'
            'The command completed successfully') if lsnr_rc == 0 else "TNS-12541: TNS:no listener"
    return FakeRunner({
        "ps -eo user,pid,args": (0, "USER PID COMMAND\noracle 1234 ora_pmon_ORCL\noracle 1300 /u01/bin/tnslsnr LISTENER\n"),
        "ps -eo user,args": (0, "USER COMMAND\noracle ora_pmon_ORCL\noracle ora_pmon_+ASM\n"),
        "runuser -u oracle": lambda a, i: (0, sqlplus_out) if "sqlplus" in " ".join(a) else (lsnr_rc, lsnr),
    }, files={"/etc/oratab": "ORCL:/u01/app/oracle/product/11.2.0/dbhome_1:Y\n+ASM:/u01/grid:N\n", ALERT: alert_text()},
        tools=["runuser", "sqlplus", "lsnrctl"])


class OracleTest(unittest.TestCase):
    def run_db(self, runner, configs=None):
        ctx = make_ctx(runner, configs)
        res = ModuleResult("database", "Databases", 15)
        database.run(ctx, res)
        ctx._home.cleanup()
        return res

    def test_primary_full_diagnostics(self):
        r = make_runner(fixture("sqlplus_primary.txt"))
        res = self.run_db(r)
        c = codes(res)
        self.assertEqual(c["ORACLE_TABLESPACE"], "CRITICAL")
        ts = [f.component for f in res.findings if f.code == "ORACLE_TABLESPACE"]
        self.assertIn("oracle:ORCL USERS", ts)
        self.assertIn("oracle:ORCL SYSAUX", ts)          # warning at 88%
        self.assertNotIn("oracle:ORCL UNDOTBS1", ts)     # UNDO excluded by default
        self.assertEqual(c["ORACLE_FRA_FULL"], "WARNING")  # (96.6-5.4)/107.4 GB = 85% effective
        self.assertEqual(c["ORACLE_ARCHIVE_DEST_ERROR"], "CRITICAL")
        self.assertEqual(c["ORACLE_REDO_MEMBER"], "CRITICAL")
        self.assertEqual(c["ORACLE_SINGLE_CONTROLFILE"], "WARNING")
        self.assertEqual(c["ORACLE_LIMIT"], "CRITICAL")   # processes 290/300
        self.assertEqual(c["ORACLE_BLOCKING"], "WARNING")
        self.assertEqual(c["ORACLE_RMAN_FAILED"], "WARNING")
        self.assertEqual(c["ORACLE_BACKUP_OLD"], "CRITICAL")
        self.assertEqual(c["ORACLE_ARCHLOG_BACKUP_OLD"], "WARNING")
        self.assertEqual(c["ORACLE_INVALID_OBJECTS"], "WARNING")
        self.assertEqual(c["ORACLE_JOBS_FAILED"], "WARNING")
        self.assertEqual(c["ORACLE_PGA_OVER_TARGET"], "WARNING")
        self.assertEqual(c["ORACLE_ALERT_CRITICAL"], "CRITICAL")
        alert = [f for f in res.findings if f.code == "ORACLE_ALERT_CRITICAL"][0]
        self.assertIn("ORA-00600 x1", alert.message)   # the 10-day-old one is outside the 1-day window
        self.assertEqual(c["ORACLE_ALERT_ERRORS"], "WARNING")  # ORA-01555
        self.assertNotIn("ORACLE_LISTENER_DOWN", c)
        # SQL is sent on stdin with OS authentication, never on the command line
        sql_inputs = [i for i in r.inputs if i]
        self.assertTrue(sql_inputs and "connect / as sysdba" in sql_inputs[0] and "dba_tablespace_usage_metrics" in sql_inputs[0])
        self.assertTrue(all("sysdba" not in c for c in r.calls))
        # ASM instance is not treated as a database
        self.assertNotIn("+ASM", " ".join(f.component for f in res.findings))

    def test_fra_percent(self):
        res = self.run_db(make_runner(fixture("sqlplus_primary.txt")))
        self.assertAlmostEqual(res.metrics["oracle.ORCL.fra_used_percent"], 85.0, places=0)

    def test_instance_down_and_listener_down(self):
        r = make_runner("ERROR:\nORA-01034: ORACLE not available\nORA-27101: shared memory realm does not exist\n", lsnr_rc=1)
        c = codes(self.run_db(r))
        self.assertEqual(c["ORACLE_UNAVAILABLE"], "CRITICAL")

    def test_standby_dataguard(self):
        out = ("DB|ORCL|ORCLSB|MOUNTED|PHYSICAL STANDBY|ARCHIVELOG|YES|NO|2019-03-01\n"
               "INST|ORCL|dbhost02|19.0.0.0.0|MOUNTED|2026-11-01 06:00:00|MOUNTED|STARTED|950400\n"
               "DG|transport lag|+00 00:00:05|11/12/2026 10:00:00\nDG|apply lag|+00 01:10:00|11/12/2026 10:00:00\n")
        c = codes(self.run_db(make_runner(out)))
        self.assertEqual(c["ORACLE_DG_LAG"], "CRITICAL")
        self.assertEqual(c["ORACLE_MRP_DOWN"], "CRITICAL")
        self.assertNotIn("ORACLE_NOT_OPEN", c)
        self.assertNotIn("ORACLE_NO_DB_BACKUP", c)  # backups are evaluated on the primary

    def test_password_auth_goes_over_stdin(self):
        conf = {"database": "[discovery]\noracle_autodiscover = no\n[oracle:PROD]\noracle_home = /u01/h\nusername = mon\nconnect_identifier = //db:1521/PROD\n",
                "credentials": "[oracle:PROD]\npassword = S3cret!\n"}
        r = FakeRunner({"/u01/h/bin/sqlplus": (0, fixture("sqlplus_primary.txt")), "ps": (0, "")},
                       tools=["/u01/h/bin/sqlplus", "/u01/h/bin/lsnrctl"], root=False)
        self.run_db(r, conf)
        self.assertTrue(any(i and 'connect mon/"S3cret!"@//db:1521/PROD' in i for i in r.inputs))
        self.assertFalse(any("S3cret" in c for c in r.calls))

    def test_parse_ignores_noise(self):
        rows, errors = oracle.parse("SQL> \nDB|A|B\nnoise line\nORA-00942: table or view does not exist\n")
        self.assertEqual(rows["DB"], [["A", "B"]])
        self.assertEqual(errors, ["ORA-00942: table or view does not exist"])


if __name__ == "__main__":
    unittest.main()
