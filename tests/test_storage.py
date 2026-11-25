# Linux Server Health Monitor Enterprise
# Copyright (c) 2026 LRS Services Pvt Ltd, Mumbai. All rights reserved.
import unittest

from fakes import FakeRunner, codes, fixture, run_module
from lshme.modules import storage

DF = """Filesystem     Type 1-blocks       Used   Available Capacity Mounted on
/dev/mapper/ol-root xfs  107374182400 48318382080 59055800320 45% /
/dev/mapper/vg_ora-lv_ora xfs 214748364800 171798691840 42949672960 80% /oracle
/dev/mapper/vg_bk-lv_bk xfs 1099511627776 1044835697459 54675930317 96% /new_backup
/dev/mapper/vg_ar-lv_ar xfs 322122547200 300647710720 21474836480 94% /archive
tmpfs tmpfs 8388608 0 8388608 0% /dev/shm
nfs01:/exports/share nfs4 107374182400 10737418240 96636764160 10% /mnt/share
"""
DFI = """Filesystem Inodes IUsed IFree IUse% Mounted on
/dev/mapper/ol-root 52428800 50331648 2097152 96% /
/dev/mapper/vg_ora-lv_ora 104857600 1048576 103809024 1% /oracle
"""
MOUNTS = """/dev/mapper/ol-root / xfs rw,relatime 0 0
/dev/mapper/vg_ora-lv_ora /oracle xfs ro,relatime 0 0
/dev/mapper/vg_bk-lv_bk /new_backup xfs rw,relatime 0 0
/dev/mapper/vg_ar-lv_ar /archive xfs rw 0 0
nfs01:/exports/share /mnt/share nfs4 rw 0 0
"""
FSTAB = """# comment
/dev/mapper/ol-root / xfs defaults 0 0
/dev/mapper/vg_ora-lv_ora /oracle xfs defaults 0 0
/dev/mapper/vg_db-lv_data /u02 xfs defaults 0 0
/dev/mapper/ol-swap swap swap defaults 0 0
/dev/sr0 /media iso9660 noauto 0 0
"""
MDSTAT = """Personalities : [raid1]
md0 : active raid1 sdb1[1] sda1[0](F)
      1048512 blocks super 1.2 [2/1] [_U]

unused devices: <none>
"""


def runner(extra=None):
    cmds = {
        "df -P -T -B1": (0, DF),
        "df -P -i": (0, DFI),
        "multipath -ll": (0, fixture("multipath_ll.txt")),
        "pvs": (0, "  /dev/sdb|vg_ora|214748364800|0|a--\n  /dev/sdc|vg_x|1000|1000|a-m\n"),
        "vgs": (0, "  vg_ora|214748364800|0|1|1|wz--n-\n"),
        "lvs": (0, "  vg_ora|lv_ora|214748364800|-wi-ao----||||/dev/sdb(0)\n  vg_ora|snap1|1073741824|swi-a-s---|91.50|lv_ora|/dev/sdb(1)\n"),
        "lsblk": (0, 'NAME="sda" KNAME="sda" TYPE="disk" SIZE="1" FSTYPE="" MOUNTPOINT="" MODEL="X" ROTA="1"\n'),
        "stat -f -c %T /mnt/share": (124, "", "timed out"),
    }
    cmds.update(extra or {})
    return FakeRunner(cmds, files={"/proc/mounts": MOUNTS, "/etc/fstab": FSTAB, "/proc/mdstat": MDSTAT,
                                   "/proc/diskstats": ""})


class StorageTest(unittest.TestCase):
    def test_thresholds_overrides_and_failures(self):
        res = run_module(storage, runner(), {"thresholds": "[disk_overrides]\n/archive = 95,98\n"})
        found = [(f.code, f.component, f.severity) for f in res.findings]
        self.assertIn(("DISK_USAGE", "/new_backup", "CRITICAL"), found)
        self.assertIn(("DISK_USAGE", "/oracle", "WARNING"), found)
        # /archive at 94% is below its 95% override
        self.assertNotIn("/archive", [c for code, c, s in found if code == "DISK_USAGE"])
        self.assertIn(("INODE_USAGE", "/", "CRITICAL"), found)
        self.assertIn(("FS_READ_ONLY", "/oracle", "CRITICAL"), found)
        self.assertIn(("FSTAB_NOT_MOUNTED", "/u02", "CRITICAL"), found)
        self.assertNotIn("/media", [c for code, c, s in found])  # noauto ignored
        self.assertIn(("NETFS_UNRESPONSIVE", "/mnt/share", "CRITICAL"), found)
        self.assertIn(("MDRAID_DEGRADED", "md0", "CRITICAL"), found)
        self.assertIn(("PV_MISSING", "/dev/sdc", "CRITICAL"), found)
        self.assertIn(("LV_DATA_FULL", "vg_ora/snap1", "WARNING"), found)
        mp = dict((f.component, f.code) for f in res.findings if f.code.startswith("MULTIPATH"))
        self.assertEqual(mp.get("mpatha"), "MULTIPATH_PATH_FAILED")
        self.assertEqual(mp.get("mpathb"), "MULTIPATH_STALE")                 # dead and nothing uses it: a LUN removed on the SAN
        self.assertNotIn("/dev/shm", [r[0] for r in res.tables[0].rows])
        self.assertAlmostEqual(res.metrics["fs.used_percent:/new_backup"], 96.0)

    def test_dead_san_disk_in_use_lvm_iscsi_and_fc(self):
        df = DF + "/dev/mapper/mpathb xfs 268435456000 112742891520 155692564480 42% /VM-OS-DATA" + chr(10)
        mounts = MOUNTS + "/dev/mapper/mpathb /VM-OS-DATA xfs rw 0 0" + chr(10)
        iscsi = chr(10).join(["Target: iqn.2002-09.com.lenovo:de4200 (non-flash)", "	Current Portal: 10.1.1.55:3260,1",
                              "		iSCSI Session State: LOGGED_IN", "	Current Portal: 10.1.1.56:3260,2", "		iSCSI Session State: FAILED"])
        r = runner({"df -P -T -B1": (0, df), "vgs": (0, "  vg_ora|214748364800|0|1|1|wz-pn-" + chr(10)), "iscsiadm -m session": (0, iscsi)})
        r.files["/proc/mounts"] = mounts
        r.files.update({"/sys/class/fc_host/host1/port_state": "Linkdown", "/sys/class/fc_host/host2/port_state": "Linkdown"})
        r.dirs = list(getattr(r, "dirs", []) or []) + ["/sys/class/fc_host/host1", "/sys/class/fc_host/host2"]
        res = run_module(storage, r)
        f = dict((x.code + ":" + x.component, x) for x in res.findings)
        self.assertEqual(f["MULTIPATH_ALL_FAILED:mpathb"].severity, "CRITICAL")
        self.assertIn("/VM-OS-DATA (mounted)", f["MULTIPATH_ALL_FAILED:mpathb"].message)
        self.assertNotIn("multipath -f", f["MULTIPATH_ALL_FAILED:mpathb"].recommendation)          # advice only: it never removes anything
        self.assertEqual(f["VG_PARTIAL:vg_ora"].severity, "CRITICAL")
        self.assertEqual(f["ISCSI_SESSION_DOWN:de4200@10.1.1.56:3260"].severity, "WARNING")
        self.assertEqual(f["FC_ALL_DOWN:fibre channel"].severity, "CRITICAL")
        self.assertEqual(len(res.data["san_links"]["iscsi"]), 2)

    def test_multipathd_suspended_dm_offline_path_nvme_partial_lv(self):
        nvme = chr(10).join(["nvme-subsys0 - NQN=nqn.2014-08.org.nvmexpress:de4200", "\\", " +- nvme0 tcp traddr=10.1.1.55,trsvcid=4420 live",
                             " +- nvme1 tcp traddr=10.1.1.56,trsvcid=4420 connecting"])
        r = runner({"systemctl is-active multipathd": (3, "inactive"), "dmsetup info": (0, "mpatha|Active" + chr(10) + "vg_ora-lv_ora|Suspended" + chr(10)),
                    "nvme list-subsys": (0, nvme), "systemctl": (0, ""), "dmsetup": (0, ""),
                    "lvs": (0, "  vg_ora|lv_ora|214748364800|-wi-ao--p-||||/dev/sdb(0)" + chr(10))})
        r.files.update({"/sys/block/sdq/device/state": "offline", "/sys/block/sda/device/state": "running"})
        r.dirs = list(getattr(r, "dirs", []) or []) + ["/sys/block/sdq", "/sys/block/sda"]
        f = dict((x.code + ":" + x.component, x.severity) for x in run_module(storage, r).findings)
        self.assertEqual(f.get("MULTIPATHD_DOWN:multipathd"), "CRITICAL")
        self.assertEqual(f.get("DM_SUSPENDED:vg_ora-lv_ora"), "CRITICAL")
        self.assertEqual(f.get("SCSI_DEVICE_OFFLINE:sdq"), "WARNING")
        self.assertNotIn("SCSI_DEVICE_OFFLINE:sda", f)
        self.assertEqual(f.get("NVME_PATH_DOWN:nqn.2014-08.org.nvmexpress:de4200"), "WARNING")
        self.assertEqual(f.get("LV_PARTIAL:vg_ora/lv_ora"), "CRITICAL")

    def test_df_failure_is_unknown_not_crash(self):
        res = run_module(storage, runner({"df -P -T -B1": (124, "", "timed out")}))
        self.assertEqual(codes(res).get("DF_FAILED"), "UNKNOWN")


if __name__ == "__main__":
    unittest.main()
