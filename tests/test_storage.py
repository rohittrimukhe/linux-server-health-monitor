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
        self.assertEqual(mp.get("mpathb"), "MULTIPATH_ALL_FAILED")
        self.assertNotIn("/dev/shm", [r[0] for r in res.tables[0].rows])
        self.assertAlmostEqual(res.metrics["fs.used_percent:/new_backup"], 96.0)

    def test_df_failure_is_unknown_not_crash(self):
        res = run_module(storage, runner({"df -P -T -B1": (124, "", "timed out")}))
        self.assertEqual(codes(res).get("DF_FAILED"), "UNKNOWN")


if __name__ == "__main__":
    unittest.main()
