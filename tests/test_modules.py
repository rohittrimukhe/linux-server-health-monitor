import os
import subprocess
import tempfile
import time
import unittest

from fakes import FakeRunner, codes, fixture, run_module
from lshme.modules import (backup, certificates, cluster, cockpit, containers, cpu_memory, custom, hardware, kvm, logs,
                           network, security, services, system)
from lshme.modules.database import mongodb, mysql, postgresql, sqlserver
from lshme.core.model import ModuleResult
from fakes import make_ctx


class ServicesTest(unittest.TestCase):
    def test_critical_failed_ports_processes(self):
        units = ("sshd.service loaded active running OpenSSH server\n"
                 "crond.service loaded failed failed Command Scheduler\n"
                 "myapp.service loaded failed failed My app\n"
                 "ignored.service loaded failed failed Old thing\n")
        r = FakeRunner({
            "systemctl list-units": (0, units),
            "systemctl list-unit-files": (0, "sshd.service enabled\ncrond.service enabled\nmyapp.service disabled\n"),
            "systemctl show sshd.service": (0, "ActiveState=active\nSubState=running\nUnitFileState=enabled\nNRestarts=5\nMemoryCurrent=4194304\n"),
            "systemctl show crond.service": (0, "ActiveState=failed\nSubState=failed\nUnitFileState=enabled\nResult=exit-code\n"),
            "ss -H -lntup": (0, 'tcp LISTEN 0 128 0.0.0.0:22 0.0.0.0:* users:(("sshd",pid=1,fd=3))\n'),
            "ps -eo pid,args": (0, "PID COMMAND\n1 /usr/sbin/sshd -D\n"),
        }, dirs=["/run/systemd/system"])
        res = run_module(services, r, {"services": "[critical]\nservices = sshd, crond\n[ports]\nlistener = 1521\nssh = 22\n"
                                                   "[processes]\npmon = ora_pmon_\n[ignore]\nfailed_units = ignored.service\n"})
        c = codes(res)
        self.assertEqual(c["CRITICAL_SERVICE_DOWN"], "CRITICAL")
        self.assertEqual(c["SERVICE_RESTARTING"], "WARNING")
        self.assertEqual(c["SERVICE_FAILED"], "WARNING")
        failed = [f.component for f in res.findings if f.code == "SERVICE_FAILED"]
        self.assertEqual(failed, ["myapp.service"])  # crond reported as critical, ignored one skipped
        self.assertEqual([f.component for f in res.findings if f.code == "PORT_NOT_LISTENING"], ["listener:1521"])
        self.assertEqual(c["PROCESS_MISSING"], "CRITICAL")


class NetworkTest(unittest.TestCase):
    def test_bond_and_routes(self):
        files = {"/sys/class/net/eth0/operstate": "up", "/sys/class/net/eth0/device": "", "/sys/class/net/eth0/duplex": "half",
                 "/sys/class/net/eth0/statistics/rx_packets": "1000", "/sys/class/net/eth0/statistics/rx_errors": "50",
                 "/sys/class/net/eth1/operstate": "down", "/sys/class/net/eth1/device": "",
                 "/proc/net/bonding/bond0": "Bonding Mode: fault-tolerance (active-backup)\nMII Status: up\n\n"
                                            "Slave Interface: eth0\nMII Status: up\n\nSlave Interface: eth1\nMII Status: down\n",
                 "/etc/resolv.conf": "nameserver 10.0.0.2\n"}
        r = FakeRunner({"ip -o addr show": (0, "2: eth1    inet 10.0.0.5/24 brd x scope global eth1\n"),
                        "ip route show": (0, "10.0.0.0/24 dev eth1\n"),
                        "getent hosts": (2, "")}, files=files, tools=["ping"])
        res = run_module(network, r, {"main": "[network]\ndns_test_names = db.example.com\n"})
        c = codes(res)
        self.assertEqual(c["NO_DEFAULT_ROUTE"], "CRITICAL")
        self.assertEqual(c["BOND_SLAVE_DOWN"], "WARNING")
        self.assertEqual(c["INTERFACE_DOWN"], "WARNING")
        self.assertEqual(c["INTERFACE_ERRORS"], "WARNING")
        self.assertEqual(c["HALF_DUPLEX"], "WARNING")
        self.assertEqual(c["DNS_FAILURE"], "WARNING")


class KvmTest(unittest.TestCase):
    def test_vms_pools_capacity(self):
        stats = fixture("virsh_domstats.txt")
        stats2 = stats.replace("cpu.time=1000000000000", "cpu.time=1007200000000")  # +7.2s over 2s on 4 vCPU = 90%
        calls = {"n": 0}

        def domstats(a, i):
            calls["n"] += 1
            return (0, stats if calls["n"] == 1 else stats2)
        snap_old = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(time.time() - 20 * 86400))
        U = "virsh -c qemu:///system "
        r = FakeRunner({
            U + "uri": (0, "qemu:///system"), U + "nodeinfo": (0, "CPU(s):              4\nMemory size:         8388608 KiB\n"),
            U + "version": (0, "Running hypervisor: QEMU 6.2.0"), U + "domstats --raw": domstats,
            U + "list --all --name": (0, "oradb01\napp01\nweb01\n"),
            U + "dominfo oradb01": (0, "Autostart:      enable\n"), U + "dominfo app01": (0, "Autostart:      disable\n"),
            U + "dominfo web01": (0, "Autostart:      enable\n"),
            U + "snapshot-list oradb01": (0, " Name     Creation Time               State\n--------------------------------------------\n pre-upg  %s +0000   running\n" % snap_old),
            U + "snapshot-list app01": (0, ""), U + "snapshot-list web01": (0, ""),
            U + "pool-list --all --name": (0, "default\n"),
            U + "pool-info default": (0, "Name: default\nState: running\nAutostart: yes\nCapacity: 100.00 GiB\nAllocation: 95.00 GiB\nAvailable: 5.00 GiB\n"),
            U + "net-list --all": (0, " Name      State      Autostart   Persistent\n----------------------------------------------\n default   inactive   yes         yes\n"),
        })
        res = run_module(kvm, r, {"main": "[kvm]\nsample_seconds = 2\n"})
        c = codes(res)
        self.assertEqual(c["VM_CRASHED"], "CRITICAL")
        self.assertEqual(c["VM_AUTOSTART_DOWN"], "WARNING")
        self.assertEqual(c["KVM_POOL_FULL"], "CRITICAL")
        self.assertEqual(c["KVM_NETWORK_INACTIVE"], "WARNING")
        self.assertEqual(c["VM_OLD_SNAPSHOT"], "WARNING")
        self.assertEqual(c["KVM_MEMORY_OVERCOMMIT"], "WARNING")  # 16 GiB VM on an 8 GiB host
        vm = [v for v in res.data["vms"] if v["name"] == "oradb01"][0]
        self.assertGreater(vm["cpu_percent"], 50)
        self.assertEqual(res.metrics["kvm.vms_total"], 3)

    def test_not_kvm_host(self):
        res = run_module(kvm, FakeRunner())
        self.assertEqual(res.status, "SKIPPED")

    def test_libvirt_down(self):
        res = run_module(kvm, FakeRunner({"virsh -c qemu:///system uri": (1, "", "failed to connect to the hypervisor")}))
        self.assertEqual(codes(res)["LIBVIRT_UNREACHABLE"], "CRITICAL")


class CockpitTest(unittest.TestCase):
    def test_inactive_and_unencrypted(self):
        r = FakeRunner({"rpm -qa": (0, "cockpit 264-1.el9\ncockpit-machines 264-1.el9\n"),
                        "systemctl show cockpit.socket": (0, "ActiveState=inactive\nSubState=dead\nUnitFileState=disabled\nListen=[::]:9090 (Stream)\n"),
                        "systemctl show cockpit.service": (0, "ActiveState=inactive\n"), "ss -H -lntup": (0, "")},
                       files={"/etc/cockpit/cockpit.conf": "[WebService]\nAllowUnencrypted = true\n"}, dirs=["/usr/share/cockpit"])
        c = codes(run_module(cockpit, r, {"main": "[cockpit]\nrequired = yes\n"}))
        self.assertEqual(c["COCKPIT_INACTIVE"], "CRITICAL")
        self.assertEqual(c["COCKPIT_UNENCRYPTED"], "WARNING")

    def test_not_installed(self):
        self.assertEqual(run_module(cockpit, FakeRunner({"rpm -qa": (0, "")})).status, "SKIPPED")


class LogsTest(unittest.TestCase):
    def test_classification(self):
        lines = ["2026-11-12T01:00:00+0000 h kernel: Out of memory: Killed process 4242 (java)",
                 "2026-11-12T01:00:01+0000 h kernel: blk_update_request: I/O error, dev sdb, sector 1",
                 "2026-11-12T01:00:02+0000 h kernel: XFS (dm-3): metadata I/O error in xfs_trans_read_buf",
                 "2026-11-12T01:00:03+0000 h systemd[1]: Failed to start Oracle listener.",
                 "2026-11-12T01:00:04+0000 h audit: avc:  denied { read } for pid=1 comm=\"httpd\"",
                 "2026-11-12T01:00:05+0000 h sudo[9]:   alice : TTY=pts/0 ; PWD=/ ; USER=root ; COMMAND=/bin/ls"]
        lines += ["2026-11-12T02:00:%02d+0000 h sshd[1]: Failed password for root from 203.0.113.9 port 22 ssh2" % i for i in range(25)]
        r = FakeRunner({"journalctl": (0, "\n".join(lines)), "last": (0, "")}, dirs=["/run/systemd/system"])
        res = run_module(logs, r)
        c = codes(res)
        self.assertEqual(c["LOG_OOM"], "CRITICAL")
        self.assertEqual(c["LOG_IO_ERROR"], "CRITICAL")
        self.assertEqual(c["LOG_FS_ERROR"], "CRITICAL")
        self.assertEqual(c["LOG_SERVICE_FAIL"], "WARNING")
        self.assertEqual(c["LOG_SELINUX"], "WARNING")
        self.assertEqual(c["LOG_SUDO"], "INFO")
        ssh = [f for f in res.findings if f.code == "LOG_SSH_FAILURES"][0]
        self.assertEqual(ssh.severity, "WARNING")
        self.assertIn("203.0.113.9 (25)", ssh.message)

    def test_syslog_files_window(self):
        old = time.strftime("%b %d %H:%M:%S", time.localtime(time.time() - 5 * 86400))
        new = time.strftime("%b %d %H:%M:%S", time.localtime(time.time() - 600))
        text = "%s h kernel: Out of memory: old event\n%s h kernel: Out of memory: Killed process 1\n" % (old, new)
        res = run_module(logs, FakeRunner({"last": (0, "")}, files={"/var/log/messages": text}))
        self.assertEqual(res.data["counts"].get("oom"), 1)


class SecurityTest(unittest.TestCase):
    def test_policy(self):
        r = FakeRunner({"getenforce": (0, "Permissive"), "sshd -T": (0, "permitrootlogin yes\npasswordauthentication yes\npermitemptypasswords no\n"),
                        "firewall-cmd --state": (252, "not running"), "systemctl is-active auditd": (3, "inactive"),
                        "dnf -q -C check-update": (100, "openssl.x86_64  1:3.0.7-25.el9  baseos\nkernel.x86_64  5.14.0-400.el9  baseos\n"),
                        "dnf -q -C updateinfo list security": (0, "RHSA-2026:1 Important/Sec. openssl-3.0.7-25.el9.x86_64\n"),
                        "rpm -qa --last": (0, "kernel-5.14.0-300.el9.x86_64       Mon 01 Sep 2026 10:00:00 AM UTC\n")},
                       files={"/etc/passwd": "root:x:0:0::/root:/bin/bash\ntoor:x:0:0::/root:/bin/bash\nbob:x:1000:1000::/home/bob:/bin/bash\n",
                              "/etc/shadow": "root:$6$x:1::::::\nbob::1::::::\n"})
        c = codes(run_module(security, r, {"main": "[security]\nrequire_auditd = yes\npassword_authentication_allowed = no\n"}))
        self.assertEqual(c["SELINUX_MODE"], "WARNING")
        self.assertEqual(c["FIREWALL_INACTIVE"], "WARNING")
        self.assertEqual(c["SSH_ROOT_LOGIN"], "WARNING")
        self.assertEqual(c["SSH_PASSWORD_AUTH"], "WARNING")
        self.assertEqual(c["EXTRA_UID0"], "CRITICAL")
        self.assertEqual(c["EMPTY_PASSWORD"], "CRITICAL")
        self.assertEqual(c["AUDITD_INACTIVE"], "WARNING")
        self.assertEqual(c["SECURITY_UPDATES"], "WARNING")


class BackupTest(unittest.TestCase):
    def test_netbackup_master_and_dirs(self):
        now = int(time.time())
        jobs = "\n".join(["%d,0,3,%d,POL_ORA,full,db01,media1,%d,600,%d" % (i, st, now - 3600, now - 3000)
                          for i, st in ((1, 0), (2, 0), (3, 1), (4, 196), (5, 0))])
        tmp = tempfile.mkdtemp()
        oldf = os.path.join(tmp, "exp_old.dmp")
        with open(oldf, "w") as f:
            f.write("x")
        os.utime(oldf, (now - 50 * 3600, now - 50 * 3600))
        r = FakeRunner({"/usr/openv/netbackup/bin/admincmd/bpdbjobs": (0, jobs),
                        "/usr/openv/netbackup/bin/bpclimagelist": (0, "Backed Up         Expires       Files       KB  C  Sched Type     Policy\n"
                                                                      "----------------  ---------- -------- -------- - --------------- ------------\n"
                                                                      "%s  12/31/2026       10     1000 N Full Backup     POL_ORA\n" % time.strftime("%m/%d/%Y %H:%M", time.localtime(now - 40 * 3600))),
                        "ps -eo": (0, "")},
                       files={"/usr/openv/netbackup/bin/admincmd/bpdbjobs": "", "/usr/openv/netbackup/bin/bpclimagelist": ""},
                       dirs=["/usr/openv/netbackup/bin"])
        # real filesystem for the directory job
        r.isdir = lambda p: p == tmp or p == "/usr/openv/netbackup/bin"
        r.mtime = os.path.getmtime
        r.size = os.path.getsize
        res = run_module(backup, r, {"backup": "[job:exports]\ntype = directory\npath = %s\npattern = *.dmp\nmax_age_hours = 26\n"
                                               "[job:missing]\npath = /nonexistent/backup\n" % tmp})
        c = codes(res)
        self.assertEqual(c["NBU_JOBS_FAILED"], "WARNING")
        self.assertEqual(c["NBU_JOBS_PARTIAL"], "WARNING")
        self.assertEqual(c["NBU_BACKUP_OLD"], "CRITICAL")
        self.assertEqual(c["BACKUP_STALE"], "CRITICAL")
        self.assertEqual(c["BACKUP_TARGET_MISSING"], "CRITICAL")


class HardwareClusterContainersTest(unittest.TestCase):
    def test_megacli_and_edac(self):
        r = FakeRunner({"MegaCli64 -LDInfo": (0, "Virtual Drive: 0 (Target Id: 0)\nRAID Level : Primary-1\nState               : Degraded\n")},
                       files={"/sys/devices/system/edac/mc/mc0/ue_count": "2", "/sys/devices/system/edac/mc/mc0/ce_count": "0"},
                       dirs=["/sys/devices/system/edac/mc/mc0"])
        c = codes(run_module(hardware, r))
        self.assertEqual(c["HW_RAID_DEGRADED"], "CRITICAL")
        self.assertEqual(c["MEMORY_UNCORRECTABLE"], "CRITICAL")

    def test_pacemaker(self):
        out = ("Node List:\n  * Online: [ node1 ]\n  * OFFLINE: [ node2 ]\n\nFull List of Resources:\n"
               "  * vip (ocf:heartbeat:IPaddr2): Stopped\n\nFailed Resource Actions:\n  * db_monitor_0 on node1 'error' (1)\n")
        c = codes(run_module(cluster, FakeRunner({"crm_mon -1": (0, out)})))
        self.assertEqual(c["CLUSTER_NODE_OFFLINE"], "CRITICAL")
        self.assertEqual(c["CLUSTER_RESOURCE_STOPPED"], "WARNING")
        self.assertEqual(c["CLUSTER_FAILED_ACTIONS"], "WARNING")

    def test_containers(self):
        out = "web|nginx|Up 2 hours (unhealthy)|running\njob|busybox|Exited (1) 3 hours ago|exited\nok|busybox|Exited (0) 1 hour ago|exited\n"
        c = codes(run_module(containers, FakeRunner({"docker ps -a": (0, out)})))
        self.assertEqual(c["CONTAINER_UNHEALTHY"], "WARNING")
        self.assertEqual(c["CONTAINER_EXITED"], "WARNING")


class CustomAndCertTest(unittest.TestCase):
    def test_custom_checks_real_scripts(self):
        from lshme.core.runner import Runner
        d = tempfile.mkdtemp()
        script = os.path.join(d, "check.sh")
        with open(script, "w") as f:
            f.write("#!/bin/sh\necho 'queue depth 900 | depth=900;500;800'\nexit 2\n")
        os.chmod(script, 0o755)
        bad = os.path.join(d, "bad.sh")
        with open(bad, "w") as f:
            f.write("#!/bin/sh\nexit 0\n")
        os.chmod(bad, 0o777)
        res = run_module(custom, Runner(), {"custom": "[check:queue]\ncommand = %s\n[check:bad]\ncommand = %s\n" % (script, bad)})
        c = codes(res)
        self.assertEqual(c["CUSTOM_CHECK"], "CRITICAL")
        self.assertEqual(c["CUSTOM_CHECK_REFUSED"], "WARNING")  # world-writable
        self.assertEqual(res.metrics["custom.queue.depth"], 900.0)

    @unittest.skipUnless(subprocess.call(["sh", "-c", "command -v openssl >/dev/null"]) == 0, "openssl missing")
    def test_certificate_expiry_real_openssl(self):
        from lshme.core.runner import Runner
        d = tempfile.mkdtemp()
        crt = os.path.join(d, "short.crt")
        subprocess.check_call(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", os.path.join(d, "k.pem"),
                               "-out", crt, "-days", "3", "-subj", "/CN=test.example"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        res = run_module(certificates, Runner(), {"certificates": "[certificates]\nfiles = %s, /nonexistent.crt\n" % crt})
        c = codes(res)
        self.assertEqual(c["CERT_EXPIRING"], "CRITICAL")
        self.assertEqual(c["CERT_UNREADABLE"], "WARNING")


class SystemCpuTest(unittest.TestCase):
    def test_time_and_reboot(self):
        r = FakeRunner({"timedatectl show": (0, "Timezone=UTC\nNTPSynchronized=no\n"), "needs-restarting -r": (1, "Reboot is required")},
                       files={"/etc/os-release": 'PRETTY_NAME="Oracle Linux Server 7.9"\nID="ol"\nVERSION_ID="7.9"\n', "/proc/uptime": "3600.0 1"})
        c = codes(run_module(system, r))
        self.assertEqual(c["TIME_NOT_SYNCED"], "WARNING")
        self.assertEqual(c["REBOOT_REQUIRED"], "WARNING")
        self.assertEqual(c["RECENT_REBOOT"], "INFO")

    def test_memory_and_load(self):
        stats = iter(["cpu  100 0 100 800 0 0 0 0 0 0", "cpu  190 0 105 805 0 0 0 0 0 0"])

        class R(FakeRunner):
            def read(self, path, default=""):
                if path == "/proc/stat":
                    return next(stats)
                return FakeRunner.read(self, path, default)
        r = R({"ps": (0, "PID USER %CPU %MEM RSS ELAPSED COMMAND\n")},
              files={"/proc/cpuinfo": "processor : 0\nmodel name : Xeon\nprocessor : 1\n", "/proc/loadavg": "9.0 8.0 7.0 1/100 1",
                     "/proc/meminfo": "MemTotal: 1000 kB\nMemAvailable: 30 kB\nSwapTotal: 100 kB\nSwapFree: 10 kB\n"})
        c = codes(run_module(cpu_memory, r))
        self.assertEqual(c["CPU_HIGH"], "CRITICAL")   # 95% busy
        self.assertEqual(c["LOAD_HIGH"], "CRITICAL")  # 8.0 on 2 CPUs
        self.assertEqual(c["MEMORY_HIGH"], "CRITICAL")
        self.assertEqual(c["SWAP_HIGH"], "CRITICAL")


class OtherDatabasesTest(unittest.TestCase):
    def check(self, adapter, runner, inst, configs=None):
        ctx = make_ctx(runner, configs)
        res = ModuleResult("database", "Databases", 15)
        adapter.check(ctx, res, inst)
        ctx._home.cleanup()
        return res

    def test_mysql_replication(self):
        def mysql_cmd(a, i):
            sql = a[-1]
            if sql.startswith("SELECT VERSION"):
                return (0, "8.0.36\tdb01\t3306\t0")
            if sql == "SHOW GLOBAL STATUS":
                return (0, "Threads_connected\t950\nUptime\t864000\nSlow_queries\t5\nInnodb_buffer_pool_reads\t10\nInnodb_buffer_pool_read_requests\t1000\n")
            if sql == "SHOW GLOBAL VARIABLES":
                return (0, "max_connections\t1000\n")
            if "REPLICA STATUS" in sql:
                return (0, "Replica_IO_Running: Yes\nReplica_SQL_Running: No\nLast_SQL_Error: Duplicate entry\nSeconds_Behind_Source: NULL\n")
            return (0, "app\t1073741824\t20\n")
        c = codes(self.check(mysql, FakeRunner({"mysql": mysql_cmd}), {"name": "main"}))
        self.assertEqual(c["MYSQL_CONNECTIONS"], "CRITICAL")
        self.assertEqual(c["MYSQL_REPLICATION_STOPPED"], "CRITICAL")

    def test_mongodb(self):
        payload = '{"version":"6.0.5","uptime":100,"conn":{"current":95,"available":5},"rs":{"set":"rs0","members":[{"name":"a:27017","state":"PRIMARY","health":1},{"name":"b:27017","state":"(not reachable/healthy)","health":0}]},"dbs":[["app",1000]]}'
        c = codes(self.check(mongodb, FakeRunner({"mongosh": (0, payload)}), {"name": "main"}))
        self.assertEqual(c["MONGODB_CONNECTIONS"], "CRITICAL")
        self.assertEqual(c["MONGODB_MEMBER_UNHEALTHY"], "CRITICAL")

    def test_sqlserver(self):
        def q(a, i):
            sql = a[-1]
            if "SERVERPROPERTY" in sql:
                return (0, "16.0.4105.2|Developer Edition (64-bit)|86400")
            if "master_files" in sql:
                return (0, "master|ONLINE|SIMPLE|10000\napp|SUSPECT|FULL|99999\n")
            if "backupset" in sql:
                return (0, "master|2.0\napp|never\n")
            return (0, "")
        c = codes(self.check(sqlserver, FakeRunner({"sqlcmd": q}), {"name": "main", "username": "mon"}))
        self.assertEqual(c["SQLSERVER_DB_STATE"], "CRITICAL")
        self.assertEqual(c["SQLSERVER_NO_BACKUP"], "WARNING")

    def test_postgres_unreachable(self):
        c = codes(self.check(postgresql, FakeRunner({"psql": (2, "", "could not connect to server")}), {"name": "main"}))
        self.assertEqual(c["POSTGRES_UNAVAILABLE"], "CRITICAL")


if __name__ == "__main__":
    unittest.main()
