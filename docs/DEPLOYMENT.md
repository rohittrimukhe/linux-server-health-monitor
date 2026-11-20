# Deploying LRS Vitals on several servers with one central dashboard

Example: two KVM hosts (Oracle Linux 10) and two Oracle database servers (Oracle Linux 7.9, Oracle 11.2). One KVM host is the
**central** server: it shows every server on one web dashboard. Every server (including the central one) sends its own daily, weekly
and monthly reports by email with the same settings.

```
                 +------------------- KVM1 (central, OL10) --------------------+
 you  --https--> | web dashboard  | asks the others every 10 min over SSH keys |
                 +-------------------------------------------------------------+
                         |  ssh (key)             |  ssh (key)           |  ssh (key)
                      KVM2 (OL10)             DB1 (OL7.9)            DB2 (OL7.9)
                 each one: its own reports by email + live watcher + alerts
```

## 0. Before you start

- Root access on all four servers, and the package `lrs-vitals-1.4.0-5.noarch.rpm` (from `releases/` on GitHub).
- The package is a plain gzip RPM that also installs on Oracle Linux 7 (needs `python3` 3.6 or newer and `openssl`; `yum` installs them).
- Servers reach the mail server, and KVM1 can reach the other three on SSH (port 22).
- Oracle servers: you know the Oracle owner (usually `oracle`) and `/etc/oratab` lists the instance.

## 1. Set up the central server (KVM1) first

```bash
sudo dnf install ./lrs-vitals-1.4.0-5.noarch.rpm
sudo vitals
```

In the setup pages enter: company, customer name, environment, email (SMTP host, port, security, user, sender, recipients), alert
levels and schedules (defaults: daily Mon-Sat 09:00, weekly Sunday 12:00, monthly day 1 at 10:00). Put the SMTP password in
`/etc/lshme/credentials.conf` (section `[smtp]`, `password = ...`, mode 600). Check it:

```bash
sudo vitals run --report-type daily --email
```

Everything you set here is copied to the other servers in step 3.

## 2. Let KVM1 log in to the others with an SSH key

```bash
sudo ssh-keygen -t ed25519 -N '' -f /root/.ssh/id_ed25519      # skip if root already has a key
sudo ssh-copy-id root@kvm2.example.com
sudo ssh-copy-id root@db1.example.com
sudo ssh-copy-id root@db2.example.com
```

(If root SSH login is disabled by policy, allow key-only root login for KVM1's address, or use a user with `sudo` and set `user =` and a
`remote_command` with sudo in `servers.conf`.)

## 3. Roll out to the other three, same settings

```bash
sudo tools/deploy_fleet.sh --rpm lrs-vitals-1.4.0-5.noarch.rpm --dry-run \
     kvm2.example.com:Production db1.example.com:Production db2.example.com:Production
```

Read the dry run, then run it again without `--dry-run` (add `--test-email` to send each server's first daily report by email). For each server it installs the
package, copies the shared settings (`main smtp credentials schedule reports retention thresholds services exclusions`), scans the server
so only what exists there is checked (KVM on the KVM hosts, Oracle on the DB servers), installs the daily/weekly/monthly schedules and the live
watcher, and runs a first check. The old `/etc/lshme` is kept as `/etc/lshme.pre-deploy.<time>`.

## 4. Oracle 11.2 servers

The checks run as the Oracle owner with operating-system login (`sqlplus / as sysdba`), read-only. On each DB server:

```bash
sudo vitals scan                     # should list the Oracle instance
sudo vitals run --modules database   # should show tablespaces, FRA, RMAN, sessions, listener ...
```

If the instance is not found, add it in `/etc/lshme/database.conf` (the sample file shows the keys: `sid`, `oracle_home`, `os_user`). To use a
dedicated monitoring login instead of operating-system login, have the DBA run `CREATE USER lrs_mon IDENTIFIED BY ...; GRANT CREATE SESSION, SELECT_CATALOG_ROLE TO lrs_mon;`
and put the password in `credentials.conf` under `[oracle:SID]`.

## 5. Central collection and the web dashboard (KVM1 only)

`/etc/lshme/servers.conf` on KVM1:

```
[defaults]
user = root
remote_command = healthcheck agent --days {days}

[server:kvm2]
host = kvm2.example.com
group = KVM
environment = Production

[server:db1]
host = db1.example.com
group = Oracle
environment = Production

[server:db2]
host = db2.example.com
group = Oracle
environment = Production
```

Switch the central collection on (asks every server every 10 minutes, writes no report files) and check it:

```bash
sudo vitals config set main fleet enabled yes
sudo vitals schedule install
sudo vitals fleet run --formats none
```

Web dashboard (use the server's own Linux user name and password; `root` and `wheel` members are administrators, others read-only):

```bash
sudo vitals config set web web bind 0.0.0.0
sudo vitals config set web web enabled yes
sudo firewall-cmd --permanent --add-port=8443/tcp && sudo firewall-cmd --reload
sudo vitals web install
```

Open `https://kvm1.example.com:8443/`. The browser warns once about the self-signed certificate (see section 20 of the user guide for a Let's Encrypt
certificate). **Servers** shows all four servers with status, roles and CPU/memory/disk; **Dashboard** shows the central server live and its alerts.

## 6. Checks

| Check | Command |
|---|---|
| Timers (daily, weekly, monthly, scan, fleet on the central one) | `systemctl list-timers 'lshme-*'` |
| Live watcher on every server | `systemctl is-active lshme-watch` |
| Central sees all servers | `vitals fleet list` then the Servers page |
| Reports and email | `vitals run --report-type daily --email` on any server |
| Health of the tool itself | Dashboard > Operations > Health of LRS Vitals |

## Things to know

- Upgrading a server that already ran an older version: run `sudo vitals raw backfill` once, so what was recorded before is also in the daily raw files.

- Each server emails its own reports (so a DB server's report covers that server). The Servers page on KVM1 gives the combined live view.
- The package and these steps have not yet been run on Oracle Linux 7.9 or 10 or on Oracle 11.2.0.1.0 by the author; run the checks above on the first server before rolling out to all.
- On Oracle Linux 7 (systemd 219) memory limits in the service files (`MemoryMax`) are ignored; CPU limits and low priority still apply.
- Keep `credentials.conf` mode 600. It is copied to all servers because they share the same SMTP login.
