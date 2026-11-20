#!/usr/bin/env python3
# Linux Server Health Monitor Enterprise
# Copyright (c) 2026 Rohit Santosh Trimukhe. All rights reserved.
"""Make sample daily, weekly, monthly and database reports with made-up data (for the customer document pack).

Usage:  python3 tools/demo_reports.py OUTPUT_DIR
Runs one real health check on this server, then adds 30 days of invented history, an invented customer, an invented
/u01 volume and invented Oracle, PostgreSQL and MySQL databases, so every section of every report has something to show.
Nothing is written to the real data or report directories (a temporary home is used)."""
import json
import math
import os
import random
import shutil
import sys
import tempfile
import time

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, APP_DIR)
from lshme import reports  # noqa: E402
from lshme.app import App  # noqa: E402
from lshme.core.model import ModuleResult  # noqa: E402
from lshme.engine.history import History  # noqa: E402

GB = 1024 ** 3
out = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else "demo_reports")
home = tempfile.mkdtemp(prefix="lrs-demo-")
shutil.copytree(os.path.join(APP_DIR, "config"), os.path.join(home, "config"))
app = App(APP_DIR, home=home)
random.seed(11)
run = app.check(None, 1, "adhoc", save_history=False)
run.host, run.fqdn, run.ip = "db01", "db01.example.com", "10.0.0.21"
run.meta.update({"customer": "Example Industries", "company": "LRS Services", "environment": "Production", "location": "Mumbai data centre",
                 "prepared_by": "Infrastructure team", "roles": ["Oracle database server", "KVM host"]})
now, host = run.collected_at, run.host


def module(name, title, weight):
    m = run.module(name)
    if m is None:
        m = ModuleResult(name, title, weight)
        run.modules.append(m)
    if m.status == "SKIPPED":
        m.status = "OK"
    return m


# an invented data volume that is filling up faster than before
st = module("storage", "Storage & Filesystems", 20)
st.data.setdefault("filesystems", []).append({"device": "/dev/mapper/vg_data-lv_u01", "fstype": "xfs", "size": 200 * GB, "used": 168 * GB, "free": 32 * GB,
                                              "used_percent": 84.0, "mountpoint": "/u01", "options": []})
st.metrics.update({"fs.used_bytes:/u01": 168.0 * GB, "fs.size_bytes:/u01": 200.0 * GB, "fs.used_percent:/u01": 84.0, "fs.max_used_percent": 84.0})
db = module("database", "Database Health", 15)
db.metrics.update({"oracle.ORCL.datafile_bytes": 148.2 * GB, "oracle.ORCL.fra_used_percent": 41.0, "oracle.ORCL.ts_used_percent:USERS": 72.0,
                   "oracle.ORCL.ts_used_bytes:USERS": 61.0 * GB, "oracle.ORCL.ts_used_bytes:SYSAUX": 9.5 * GB, "oracle.ORCL.ts_used_bytes:DATA01": 52.0 * GB,
                   "postgresql.appdb.total_bytes": 15.5 * GB, "postgresql.appdb.db_bytes:app": 12.4 * GB, "postgresql.appdb.db_bytes:reports": 3.1 * GB,
                   "postgresql.appdb.table_bytes:app/public.orders": 5.2 * GB, "postgresql.appdb.table_bytes:app/public.events": 3.9 * GB,
                   "mysql.shop.total_bytes": 3.1 * GB, "mysql.shop.db_bytes:shop": 3.1 * GB, "mysql.shop.table_bytes:shop.order_items": 1.4 * GB})
fo = module("folders", "Folder Sizes", 0)
fo.metrics.update({"folder.bytes:/u01/oradata": 148 * GB, "folder.bytes:/u01/fra": 82 * GB, "folder.bytes:/var/lib/pgsql": 15.6 * GB})
bk = module("backup", "Backup & Recovery", 8)
bk.metrics.update({"backup.age_hours:nightly-rman": 6.0, "backup.size_bytes:nightly-rman": 24 * GB, "backup.age_hours:archivelogs": 3.0})
ce = module("certificates", "TLS Certificates", 4)
ce.metrics.update({"cert.days_left:/etc/pki/tls/certs/portal.crt": 41.0, "cert.days_left:/etc/pki/ca/internal-ca.crt": 12.0, "licence.days_left:Oracle support": 78.0})
lg = module("logs", "Logs & Events", 8)
lg.metrics.update({"logs.count:ssh_failed": 14, "logs.count:oom": 0, "logs.count:io_errors": 1})
cur = run.metrics()
growth = {"oracle.ORCL.datafile_bytes": 0.9 * GB, "oracle.ORCL.ts_used_bytes:USERS": 0.4 * GB, "oracle.ORCL.ts_used_bytes:DATA01": 0.5 * GB, "oracle.ORCL.ts_used_bytes:SYSAUX": 0.01 * GB,
          "postgresql.appdb.total_bytes": 0.1 * GB, "postgresql.appdb.db_bytes:app": 0.08 * GB, "postgresql.appdb.db_bytes:reports": 0.02 * GB,
          "postgresql.appdb.table_bytes:app/public.orders": 0.05 * GB, "postgresql.appdb.table_bytes:app/public.events": 0.03 * GB,
          "mysql.shop.total_bytes": 0.02 * GB, "mysql.shop.db_bytes:shop": 0.02 * GB, "mysql.shop.table_bytes:shop.order_items": 0.01 * GB,
          "folder.bytes:/u01/oradata": 0.9 * GB, "folder.bytes:/u01/fra": 0.3 * GB, "folder.bytes:/var/lib/pgsql": 0.1 * GB, "fs.used_bytes:/u01": 1.2 * GB}
for k in cur:
    if k.startswith("fs.used_bytes:") and k not in growth:
        growth[k] = 0.1 * GB
    elif k.startswith("folder.bytes:") and k not in growth:
        growth[k] = 0.02 * GB

h = History(app.paths.history_db)
for back in range(30, 0, -1):
    ts = now - back * 86400 + random.randint(-600, 600)
    c = h.con.cursor()
    c.execute("INSERT INTO runs(host, ts, schedule, score, status, critical, warning, unknown, duration, version, report_dir) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
              (host, ts, "daily", random.choice([100, 94, 90, 86, 86, 78]), "WARNING", 0, random.choice([1, 2, 2, 3]), 0, 9.0, "1.3.0", ""))
    rid = c.lastrowid
    rows = []
    for k, v in cur.items():
        if k in growth:
            speed = 1.5 if (k == "fs.used_bytes:/u01" and back < 8) else 1.0      # /u01 grew faster this week than before
            rows.append((rid, host, ts, k, max(v * 0.4, v - growth[k] * back * speed * random.uniform(0.8, 1.2))))
        elif k == "backup.size_bytes:nightly-rman":
            rows.append((rid, host, ts, k, v if back < 3 else 40 * GB))            # the nightly backup suddenly got smaller
        elif k.startswith("backup.age_hours"):
            rows.append((rid, host, ts, k, random.uniform(2, 9)))
        elif k.startswith("logs.count:"):
            rows.append((rid, host, ts, k, max(0, v + random.randint(-3, 6))))
        elif k.startswith(("cpu.", "memory.", "swap.", "fs.max", "services.", "security.", "system.")):
            rows.append((rid, host, ts, k, v * random.uniform(0.9, 1.1)))
    c.executemany("INSERT INTO metrics VALUES(?,?,?,?,?)", rows)
    c.executemany("INSERT INTO findings VALUES(?,?,?,?,?,?,?,?)", [(rid, host, ts, "WARNING", "SECURITY_UPDATES", "updates", "security", "Security updates are pending")])
h.con.commit()
IFACES = [n for n in sorted(os.listdir("/sys/class/net")) if n != "lo"][:2] or ["eth0"]


def netrate(hod, jitter):
    return (int(400000 + 900000 * (1 if 9 < hod < 18 else 0.3) + jitter * 200000), int(150000 + 5000000 * math.exp(-((hod - 2.2) ** 2) / 0.5) + jitter * 80000))


for hr in range(30 * 24, 0, -1):
    t = int((now - hr * 3600) // 3600 * 3600)
    hod = time.localtime(t).tm_hour
    burst = math.exp(-((hod - 2.2) ** 2) / 0.6) * 36
    cpu, mem = max(2, 9 + burst + random.random() * 6 + (4 if 9 < hod < 18 else 0)), 60 + math.exp(-((hod - 3.5) ** 2) / 1.2) * 9 + random.random() * 2
    swap, load = 3 + random.random() * 0.8, max(0.03, 0.15 + burst / 35 + random.random() * 0.1)
    for iface in IFACES:
        rx, tx = netrate(hod, random.random())
        h.con.execute("INSERT OR REPLACE INTO hourly_net VALUES(?,?,?,?,?,?,?,?)", (host, t, iface, 12, rx * 12, rx * 1.6, tx * 12, tx * 1.5))
    for core in range(2):
        cv = cpu * (1.3 if core == 0 else 0.7)
        h.con.execute("INSERT OR REPLACE INTO hourly_core VALUES(?,?,?,?,?,?)", (host, t, core, 12, cv * 12, cv + 6))
    h.con.execute("INSERT OR REPLACE INTO hourly VALUES(?,?,?,?,?,?,?,?,?,?,?)", (host, t, 12, cpu * 12, cpu + 8, mem * 12, mem + 3, swap * 12, swap + 1, load * 12, load + 0.4))
h.con.commit()
h.save(run)
h.close()
pts = []
for m in range(24 * 60, 0, -1):
    t = now - m * 60
    hod = time.localtime(t).tm_hour + time.localtime(t).tm_min / 60.0
    burst = math.exp(-((hod - 2.2) ** 2) / 0.25) * 36
    pts.append({"t": round(t), "cpu": max(2, 9 + burst + random.random() * 6), "mem": 60 + math.exp(-((hod - 3.5) ** 2) / 0.8) * 9 + random.random() * 2, "swap": 3 + random.random() * 0.8,
                "load": max(0.03, 0.15 + burst / 35 + random.random() * 0.12), "cores": [int(9 + burst * 1.3 + random.random() * 8), int(6 + burst * 0.6 + random.random() * 5)],
                "net": dict((i, list(netrate(hod, random.random()))) for i in IFACES), "disk": {}, "crit": 0, "warn": 0})
os.makedirs(app.paths.data_dir, exist_ok=True)
with open(os.path.join(app.paths.data_dir, "watch_metrics.json"), "w") as f:
    json.dump(pts, f)

app.cfg.set("schedule", "schedule:monthly", "to_database", "dba@example.com")
shutil.rmtree(out, ignore_errors=True)
os.makedirs(out)
for ptype in ("daily", "weekly", "monthly"):
    run.schedule = ptype
    doc, outdir, files, notes = reports.generate(run, app.cfg, app.paths, ["html", "pdf", "xlsx", "csv"], ptype, ptype)
    for f in files:
        shutil.copy(f, out)
    print(ptype, doc.status, len(files), "files", notes)
shutil.rmtree(home, ignore_errors=True)
print(out)
