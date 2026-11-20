# Linux Server Health Monitor Enterprise
# Copyright (c) 2026 Rohit Santosh Trimukhe. All rights reserved.
"""Live system statistics, computed from a fake /proc tree so every number can be checked."""
import os
import shutil
import tempfile
import unittest
from collections import namedtuple

from fakes import ROOT  # noqa: F401  (puts the package on sys.path)
from lshme.ops import sysstats

VFS = namedtuple("VFS", "f_blocks f_frsize f_bfree f_bavail f_files f_ffree")


def put(root, path, text):
    full = os.path.join(root, path.lstrip("/"))
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w", encoding="utf-8") as f:
        f.write(text)


STAT_1 = "cpu  100 0 50 800 50 0 0 0\ncpu0 60 0 30 400 10 0 0 0\ncpu1 40 0 20 400 40 0 0 0\n"
STAT_2 = "cpu  200 0 100 850 50 0 0 0\ncpu0 160 0 60 410 10 0 0 0\ncpu1 40 0 40 440 40 0 0 0\n"
NET_1 = "h1\nh2\n  eth0: 1000 10 0 0 0 0 0 0 500 5 0 0 0 0 0 0\n    lo: 99 1 0 0 0 0 0 0 99 1 0 0 0 0 0 0\n"
NET_2 = "h1\nh2\n  eth0: 3000 20 1 0 0 0 0 0 1500 9 0 0 0 0 0 0\n    lo: 199 2 0 0 0 0 0 0 199 2 0 0 0 0 0 0\n"
DISK_1 = "   8       0 sda 10 0 0 0 5 0 0 0 0 0 0\n   8       1 sda1 1 0 0 0 1 0 0 0 0 0 0\n"
DISK_2 = "   8       0 sda 10 0 2048 0 5 0 1024 0 0 1000 0\n   8       1 sda1 1 0 99 0 1 0 99 0 0 0 0\n"
PROC_1 = "1234 (my app) S 1 1 1 0 0 0 0 0 0 0 0 0 0 0 20 0 1 0 100 0 1000 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0\n"
PROC_2 = "1234 (my app) R 1 1 1 0 0 0 0 0 0 0 150 50 0 0 20 0 1 0 100 0 1000 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0\n"


class SamplerTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="lshme-proc-")
        self.now = [1000.0]
        vfs = {"/": VFS(1000, 1024, 400, 300, 1000, 900), "/data": VFS(2000, 1024, 100, 100, 100, 90)}

        def statvfs(mp):
            if mp in vfs:
                return vfs[mp]
            raise OSError("gone")
        self.s = sysstats.Sampler(root=self.root, statvfs=statvfs, clk=100, pagesize=4096, clock=lambda: self.now[0])
        put(self.root, "/proc/stat", STAT_1)
        put(self.root, "/proc/meminfo", "MemTotal: 4194304 kB\nMemFree: 524288 kB\nMemAvailable: 1048576 kB\nBuffers: 1024 kB\nCached: 2048 kB\n"
                                        "SwapTotal: 2097152 kB\nSwapFree: 1572864 kB\n")
        put(self.root, "/proc/loadavg", "0.50 0.40 0.30 1/200 999\n")
        put(self.root, "/proc/uptime", "93784.12 5000.0\n")
        put(self.root, "/proc/mounts", "/dev/sda1 / xfs rw,relatime 0 0\nproc /proc proc rw 0 0\ntmpfs /run tmpfs rw 0 0\n"
                                       "/dev/sdb1 /data ext4 ro,relatime 0 0\n/dev/sdc1 /gone ext4 rw 0 0\n")
        put(self.root, "/proc/net/dev", NET_1)
        put(self.root, "/proc/diskstats", DISK_1)
        put(self.root, "/proc/sys/kernel/hostname", "testhost\n")
        put(self.root, "/sys/class/net/eth0/operstate", "up\n")
        put(self.root, "/proc/1234/stat", PROC_1)

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def second(self):
        self.now[0] += 2.0
        put(self.root, "/proc/stat", STAT_2)
        put(self.root, "/proc/net/dev", NET_2)
        put(self.root, "/proc/diskstats", DISK_2)
        put(self.root, "/proc/1234/stat", PROC_2)
        return self.s.sample()

    def test_first_sample_has_zero_rates_not_garbage(self):
        a = self.s.sample()
        self.assertEqual(a["cpu"]["total"], 0.0)
        self.assertEqual(a["net"][0]["rx_bps"], 0.0)
        self.assertEqual(a["disks"][0]["read_bps"], 0.0)
        self.assertEqual(a["hostname"], "testhost")

    def test_cpu_percentages_from_two_samples(self):
        self.s.sample()
        b = self.second()
        self.assertAlmostEqual(b["cpu"]["total"], 75.0)      # 150 busy ticks of 200
        self.assertAlmostEqual(b["cpu"]["user"], 50.0)
        self.assertAlmostEqual(b["cpu"]["system"], 25.0)
        self.assertEqual(b["cpu"]["cores"], 2)
        self.assertAlmostEqual(b["cpu"]["per_core"][0], 100.0 * 130 / 140, delta=0.01)  # cpu0: 130 busy ticks of 140
        self.assertLess(b["cpu"]["per_core"][1], 50)

    def test_memory_swap_load_uptime(self):
        a = self.s.sample()
        self.assertEqual(a["mem"]["total"], 4 * 1024 ** 3)
        self.assertAlmostEqual(a["mem"]["pct"], 75.0)          # used = total - available
        self.assertAlmostEqual(a["mem"]["swap_pct"], 25.0)
        self.assertEqual(a["load"], [0.5, 0.4, 0.3])
        self.assertEqual(sysstats.fmt_uptime(a["uptime"]), "1d 2h 3m")

    def test_mounts_skip_pseudo_filesystems_and_survive_errors(self):
        mounts = dict((m["mount"], m) for m in self.s.sample()["mounts"])
        self.assertEqual(sorted(mounts), ["/", "/data", "/gone"])
        self.assertAlmostEqual(mounts["/"]["pct"], 100.0 * 600 / 900)   # used 600 of used+avail 900, like df
        self.assertTrue(mounts["/data"]["readonly"])
        self.assertTrue(mounts["/gone"]["error"])
        self.assertAlmostEqual(mounts["/"]["inode_pct"], 10.0)

    def test_network_and_disk_throughput(self):
        self.s.sample()
        b = self.second()
        eth = [n for n in b["net"] if n["iface"] == "eth0"][0]
        self.assertAlmostEqual(eth["rx_bps"], 1000.0)          # 2000 bytes in 2 s
        self.assertAlmostEqual(eth["tx_bps"], 500.0)
        self.assertEqual(eth["state"], "up")
        self.assertEqual(eth["errors"], 1)
        self.assertEqual([d["dev"] for d in b["disks"]], ["sda"])  # partitions are not listed
        self.assertAlmostEqual(b["disks"][0]["read_bps"], 2048 * 512 / 2.0)
        self.assertAlmostEqual(b["disks"][0]["write_bps"], 1024 * 512 / 2.0)
        self.assertAlmostEqual(b["disks"][0]["util"], 50.0)    # 1000 ms busy in 2 s

    def test_top_processes(self):
        self.s.sample()
        b = self.second()
        top = b["procs"]["top_cpu"][0]
        self.assertEqual(top["name"], "my app")                 # a name with a space still parses
        self.assertAlmostEqual(top["cpu"], 100.0)               # 200 ticks at 100 Hz over 2 s
        self.assertEqual(top["rss"], 1000 * 4096)
        self.assertEqual(b["procs"]["running"], 1)

    def test_history_is_kept_for_sparklines_and_capped(self):
        for _ in range(sysstats.HISTORY + 20):
            self.now[0] += 1
            self.s.sample(processes=False)
        self.assertEqual(len(self.s.history["cpu"]), sysstats.HISTORY)

    def test_formatting_and_levels(self):
        self.assertEqual(sysstats.fmt_size(1536), "1.5 KB")
        self.assertEqual(sysstats.fmt_rate(0), "0 B/s")
        self.assertEqual(sysstats.level(50), "ok")
        self.assertEqual(sysstats.level(85), "warn")
        self.assertEqual(sysstats.level(95), "crit")

    def test_the_real_proc_of_this_machine_works(self):
        if not os.path.isdir("/proc/self"):
            self.skipTest("no /proc")
        real = sysstats.Sampler()
        real.sample()
        s = real.sample()
        self.assertGreater(s["mem"]["total"], 0)
        self.assertGreaterEqual(s["cpu"]["cores"], 1)
        self.assertTrue(any(m["mount"] == "/" for m in s["mounts"]))


if __name__ == "__main__":
    unittest.main()
