# Linux Server Health Monitor Enterprise
# Copyright (c) 2026 Rohit Santosh Trimukhe. All rights reserved.
"""What a server is used for, and which services deserve a critical watch.

Both come from two cheap read-only calls (systemctl and ps), so they can run on every health check:
* roles: "Oracle database server", "KVM host", ... used in report titles;
* auto-detected critical services: the KVM (libvirt) service and database services that are enabled or running,
  added to the critical list in services.conf so a stopped database or hypervisor raises a critical alert."""
import re

KVM_UNITS = re.compile(r"^(libvirtd|virtqemud)$")
DB_UNITS = re.compile(r"^(postgresql(-\d+(\.\d+)?)?(@.+)?|mysqld|mysql|mariadb|mongod|mongos|mssql-server|oracle-database(-.+)?|"
                      r"oracle-ohasd|oracle-rdbms-server-.+|dbora|redis|redis-server|cassandra|influxd|clickhouse-server)$")
ROLE_UNITS = (
    (re.compile(r"^(libvirtd|virtqemud)$"), "KVM host"),
    (re.compile(r"^postgresql"), "PostgreSQL server"),
    (re.compile(r"^(mysqld|mysql|mariadb)$"), "MySQL/MariaDB server"),
    (re.compile(r"^(mongod|mongos)$"), "MongoDB server"),
    (re.compile(r"^mssql-server$"), "SQL Server"),
    (re.compile(r"^(oracle-database|oracle-ohasd|oracle-rdbms|dbora)"), "Oracle database server"),
    (re.compile(r"^(httpd|nginx|apache2)$"), "Web server"),
    (re.compile(r"^(dockerd?|docker)$"), "Container host"),
    (re.compile(r"^(named|bind9)$"), "DNS server"),
    (re.compile(r"^postfix$"), "Mail server"),
    (re.compile(r"^(nfs-server|smb|smbd)$"), "File server"),
    (re.compile(r"^(pacemaker|corosync)$"), "Cluster node"),
    (re.compile(r"^haproxy$"), "Load balancer"),
    (re.compile(r"^(nbclient|bpcd|veeamservice|vmware-vpxa)"), "Backup client"),
)


def unit_lists(runner):
    """({unit: file state}, {running units}) without the .service suffix; empty when systemd is not there."""
    files, running = {}, set()
    if not runner.has("systemctl"):
        return files, running
    r = runner.run(["systemctl", "list-unit-files", "--type=service", "--no-legend", "--no-pager"], timeout=30)
    for line in (r.out or "").splitlines():
        p = line.split()
        if len(p) >= 2 and p[0].endswith(".service"):
            files[p[0][:-8]] = p[1]
    r = runner.run(["systemctl", "list-units", "--type=service", "--state=running", "--no-legend", "--no-pager"], timeout=30)
    for line in (r.out or "").splitlines():
        p = line.split()
        if p and p[0].endswith(".service"):
            running.add(p[0][:-8])
    return files, running


def auto_critical(runner):
    """Unit names of KVM and database services that are enabled or running, in a stable order."""
    files, running = unit_lists(runner)
    found = set()
    for name in set(files) | running:
        if not (KVM_UNITS.match(name) or DB_UNITS.match(name)):
            continue
        if name.endswith("@") or (name not in running and files.get(name) != "enabled"):
            continue                                   # a template, or installed but neither enabled nor running
        found.add(name)
    return sorted(found)


def detect(runner):
    """Roles of this server in a stable order, for example ['Oracle database server', 'KVM host']."""
    files, running = unit_lists(runner)
    roles = []
    names = sorted(running) + sorted(n for n, st in files.items() if st == "enabled" and n not in running)
    for name in names:
        for rx, role in ROLE_UNITS:
            if rx.search(name) and role not in roles:
                roles.append(role)
    try:
        ps = runner.run(["ps", "-eo", "args"], timeout=15).out
        if re.search(r"ora_pmon_|tnslsnr", ps) and "Oracle database server" not in roles:
            roles.insert(0, "Oracle database server")
    except Exception:
        pass
    return roles


def title_suffix(host, ip, roles):
    """' (host, ip, role + role)' with only the parts that are known."""
    parts = [p for p in (host, ip, " + ".join(roles[:2]) if roles else "") if p]
    return " (%s)" % ", ".join(parts) if parts else ""


def default_title(base, run):
    """The report title: the configured text or the default, followed by (server name, IP address, role)."""
    meta = getattr(run, "meta", None) or {}
    return base + title_suffix(getattr(run, "fqdn", "") or getattr(run, "host", ""), getattr(run, "ip", ""), meta.get("roles") or [])
