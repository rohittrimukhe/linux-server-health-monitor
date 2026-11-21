# SAN storage: adding and checking any make

LRS Vitals checks SAN storage arrays **read-only** over each make's own management API. Nothing is installed on the array and
nothing is changed on it. Each array is added by you, with its controller addresses and a read-only user; nothing is built in.

What is checked, for every supported make (as far as the make's API offers it):

| Area | What you get |
|---|---|
| Array | Overall status (optimal / needs attention), model, serial, firmware, active failures (recovery guidance) |
| Controllers | State of controller A and B (or nodes / storage processors); a controller whose management port does not answer |
| Drives | Every drive: failed, predicted to fail, SSD wear, hot spares |
| Pools / volume groups | Disk pools, RAID groups, aggregates, CPGs: size, used, free, % used, growth, degraded state |
| Volumes / LUNs | Size, state, which pool, mapped to hosts |
| Hardware | Fans, power supplies, batteries, temperature sensors |
| Events | The latest critical events from the array's own log |
| I/O | Read / write IOPS, MB/s and response time; average and peak over the report period |

Alerts: failures are found by the live watcher (rotation) and every health check, and sent like any other alert
(e-mail, Slack, Teams, webhook), with the same 6-hour reminders and dashboard mute.

Reports: **daily** shows health, controllers, failed drives, pools and I/O now; **weekly** and **monthly** add pool growth,
I/O and response time over the period and how often the array answered; the **comprehensive** report adds every drive,
every volume, controllers and hardware, recent critical events and charts. A server with no SAN storage added gets no SAN section.

## Adding an array

**Web dashboard:** Servers page > *SAN storage* > **Add SAN storage**: choose the make, enter controller A and B management IPs,
the read-only user and its password, press **Test connection**, then **Save**.

**Terminal:**

```
vitals san types                 # supported makes and how to connect each
vitals san add                   # asks for everything; the password is not shown while typing
vitals san list
vitals san test hhk-de           # connection test
vitals san show hhk-de           # controllers, pools, drives, volumes, I/O now
vitals san remove hhk-de
```

Or in one line (the password is asked separately):

```
vitals san add hhk-de --type santricity --controllers 172.16.101.55,172.16.101.56 --user monitor
```

Where it is stored: `/etc/lshme/main.conf` `[san:NAME]` (type, controllers, port, user) and the password only in
`/etc/lshme/credentials.conf` `[san:NAME] password` (mode 600). The web page never shows the password.

The server that runs the checks (usually the central server) must reach each controller's management IP on the port below.
Check it with:

```
curl -sk -o /dev/null -w '%{http_code}\n' https://CONTROLLER-IP:PORT/
```

Any HTTP code (200, 302, 401, 404) means the port answers; `000` means it does not.

## Per make

### Lenovo ThinkSystem DE series, NetApp E-Series (type `santricity`, port 8443)
1. ThinkSystem System Manager (or SANtricity System Manager): **Settings > Access Management > Local User Roles**.
2. Select the built-in user **monitor** > **Change Password**, set a password. (With LDAP, map a group to the *Monitor* role.)
3. Add the array with both controllers' management IPs (Settings > System, or Hardware > controller > View settings), user `monitor`.

### HPE MSA 1060/2060/2062, Dell PowerVault ME4/ME5, Lenovo ThinkSystem DS series (type `msa`, port 443)
1. Storage Management Utility / PowerVault Manager: **Settings > Users > Add user**.
2. Role **monitor**, interfaces **WBI** (HTTPS) enabled.
3. Add the array with both controllers' management IPs.

### NetApp ONTAP (AFF, FAS, ASA), Lenovo ThinkSystem DM series (type `ontap`, port 443)
1. System Manager: **Cluster > Settings > Users and Roles > Add**.
2. Application **http**, role **readonly**.
3. Controller A = cluster management IP; controller B = a node management IP (used if A does not answer).

### Dell Unity / Unity XT (type `unity`, port 443)
1. Unisphere: **Settings > Users and Groups > User Management > Add** a local user.
2. Role **Operator** (read-only).
3. Use the system management IP (and, if you like, the SP B service IP as controller B).

### Dell PowerStore (type `powerstore`, port 443)
1. PowerStore Manager: **Settings > Users > Add** a local user with role **Operator**.
2. Use the cluster management IP.

### HPE 3PAR / Primera / Alletra 9000 (type `primera`, port 8080, or 443 on newer releases)
1. On the array CLI: `showwsapi`; if it is off: `setwsapi -https enable` and `startwsapi`.
2. Add a user with the **browse** role: `createuser -c <password> vitals all browse`.
3. Use the system management IP. LRS Vitals logs out after each check (the array allows only a few sessions).

### Pure Storage FlashArray (type `pure`, port 443)
1. Purity GUI: **Settings > Users and Policies > Create User** with role **Read-Only**.
2. On that user: **Create API Token**.
3. In LRS Vitals leave the user empty and paste the **API token as the password**. Use the array management IP (CT0 / CT1 as A / B).

### IBM FlashSystem, Storwize, SAN Volume Controller (type `svc`, port 7443)
1. Management GUI: **Access > Users by Group > Monitor > Create User**.
2. Controller A = cluster management IP; controller B = the service IP of the second node.

### Huawei OceanStor / Dorado (type `oceanstor`, port 8088)
1. DeviceManager: **Settings > User and Security > Users and Roles > Create** a local user with a read-only (monitor) role.
2. Use both controllers' management IPs.

### Any other make (type `reach`, port of its management web page)
For a make without a supported API, LRS Vitals checks only that each controller's management port answers, and raises an alert
when one does not. Also turn on the array's own e-mail or SNMP alerts. Tell LRS Services the make and model: a full driver can be added.

## Troubleshooting

| Message | What to do |
|---|---|
| `cannot reach ...: timed out` | The checking server cannot reach the controller: firewall, VLAN or the IP. Test with curl (above). |
| `the storage refused the user name or password` | Wrong user or password, the user lacks the read-only role, or (Pure) the API token is wrong or expired. |
| `HTTP 404` / `not JSON (is the storage type right?)` | The make (type) or port is wrong, or the REST API is switched off on the array (3PAR: `startwsapi`). |
| One controller "does not answer" | The other controller answered and the check is complete; check that controller's management cable. |

The HTTPS certificate of the array is usually self-signed, so it is not checked by default. Tick *Check the storage's HTTPS
certificate* (or `verify_tls = yes`) when the array has a certificate your servers trust.
