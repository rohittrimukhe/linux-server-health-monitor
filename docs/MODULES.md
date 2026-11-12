# Module and finding reference

Generated from the source by `packaging/gen_docs.py`. Every finding has a stable code you can filter on in CSV/JSON/XLSX exports, Nagios output or the history database.

## Operating System — `system` (weight 8)

Operating system identity, uptime, time synchronisation and reboot state.

| Code | Severity |
|---|---|
| `TIME_NOT_SYNCED` | WARNING |
| `TIME_SYNC_UNKNOWN` | UNKNOWN |
| `REBOOT_REQUIRED` | WARNING |
| `RECENT_REBOOT` | INFO |

## CPU & Memory — `cpu_memory` (weight 12)

CPU utilisation, load, memory, swap and top resource consumers.

| Code | Severity |
|---|---|
| `CPU_HIGH` | WARNING / CRITICAL by threshold |
| `CPU_IOWAIT` | WARNING |
| `CPU_STEAL` | WARNING |
| `LOAD_HIGH` | WARNING / CRITICAL by threshold |
| `MEMORY_HIGH` | WARNING / CRITICAL by threshold |
| `SWAP_HIGH` | WARNING / CRITICAL by threshold |
| `NO_SWAP` | INFO |

## Storage & Filesystems — `storage` (weight 20)

Disks, filesystems, mount points, inodes, LVM, multipath, RAID, NFS and disk I/O.

| Code | Severity |
|---|---|
| `DF_FAILED` | UNKNOWN |
| `FS_READ_ONLY` | CRITICAL |
| `DISK_USAGE` | WARNING / CRITICAL by threshold |
| `INODE_USAGE` | WARNING / CRITICAL by threshold |
| `FSTAB_NOT_MOUNTED` | CRITICAL |
| `NETFS_UNRESPONSIVE` | CRITICAL |
| `LV_DATA_FULL` | WARNING / CRITICAL by threshold |
| `PV_MISSING` | CRITICAL |
| `MULTIPATH_ALL_FAILED` | CRITICAL |
| `MULTIPATH_PATH_FAILED` | WARNING |
| `MULTIPATH_SINGLE_PATH` | WARNING |
| `MDRAID_DEGRADED` | CRITICAL |
| `SMART_FAILING` | CRITICAL |
| `DISK_IO_SATURATED` | WARNING |

## Services & Processes — `services` (weight 15)

Service inventory, critical service policy, expected ports and processes.

| Code | Severity |
|---|---|
| `CRITICAL_SERVICE_MISSING` | CRITICAL |
| `CRITICAL_SERVICE_DOWN` | CRITICAL |
| `CRITICAL_SERVICE_NOT_ENABLED` | WARNING |
| `SERVICE_RESTARTING` | WARNING |
| `SERVICE_FAILED` | WARNING |
| `PORT_NOT_LISTENING` | CRITICAL |
| `PROCESS_MISSING` | CRITICAL |

## Network & Connectivity — `network` (weight 10)

Interfaces, routing, DNS, bonding, link state, errors, reachability and firewall.

| Code | Severity |
|---|---|
| `INTERFACE_ERRORS` | WARNING |
| `HALF_DUPLEX` | WARNING |
| `INTERFACE_MISSING` | CRITICAL |
| `NO_DEFAULT_ROUTE` | CRITICAL |
| `BOND_DOWN` | CRITICAL |
| `BOND_SLAVE_DOWN` | WARNING |
| `GATEWAY_UNREACHABLE` | CRITICAL |
| `PACKET_LOSS` | CRITICAL |
| `HIGH_LATENCY` | WARNING |
| `TCP_CHECK_FAILED` | CRITICAL |
| `DNS_FAILURE` | WARNING |
| `NO_NAMESERVER` | WARNING |

## Hardware & RAID — `hardware` (weight 8)

Hardware inventory, sensors, ECC memory errors, IPMI and hardware RAID controllers.

| Code | Severity |
|---|---|
| `TEMPERATURE_CRITICAL` | CRITICAL |
| `TEMPERATURE_HIGH` | WARNING |
| `MEMORY_UNCORRECTABLE` | CRITICAL |
| `MEMORY_CORRECTABLE` | WARNING |
| `IPMI_SENSOR` | WARNING |
| `HW_RAID_DEGRADED` | WARNING / CRITICAL by threshold |

## KVM Virtualisation — `kvm` (weight 10)

KVM / libvirt: hypervisor capacity, every VM's state and utilisation, snapshots, storage pools and virtual networks. Uses read-only virsh commands only.

| Code | Severity |
|---|---|
| `LIBVIRT_UNREACHABLE` | CRITICAL |
| `VM_CRASHED` | CRITICAL |
| `VM_PAUSED` | WARNING |
| `VM_AUTOSTART_DOWN` | WARNING |
| `VM_CPU_HIGH` | WARNING |
| `VM_OLD_SNAPSHOT` | WARNING |
| `KVM_POOL_FULL` | WARNING / CRITICAL by threshold |
| `KVM_POOL_INACTIVE` | WARNING |
| `KVM_NETWORK_INACTIVE` | WARNING |
| `KVM_VCPU_OVERCOMMIT` | WARNING |
| `KVM_MEMORY_OVERCOMMIT` | WARNING |

## Cockpit Web Console — `cockpit` (weight 4)

Cockpit web console: packages, socket/service, listener, HTTPS reachability, TLS certificate and security-relevant configuration.

| Code | Severity |
|---|---|
| `COCKPIT_NOT_INSTALLED` | CRITICAL |
| `COCKPIT_INACTIVE` | WARNING / CRITICAL by threshold |
| `COCKPIT_NOT_ENABLED` | WARNING |
| `COCKPIT_PORT_CLOSED` | WARNING / CRITICAL by threshold |
| `COCKPIT_HTTPS_FAILED` | WARNING |
| `COCKPIT_CERT_EXPIRED` | CRITICAL |
| `COCKPIT_CERT_EXPIRING` | CRITICAL |
| `COCKPIT_SELF_SIGNED` | INFO |
| `COCKPIT_UNENCRYPTED` | WARNING |
| `COCKPIT_ROOT_ALLOWED` | INFO |

## Databases — `database` (weight 15)

Database intelligence: discovers database engines on the host and runs the read-only adapter for each configured or auto-discovered instance.

| Code | Severity |
|---|---|
| `DB_ADAPTER_ERROR` | UNKNOWN |
| `DB_NOT_MONITORED` | INFO |
| `MONGODB_UNAVAILABLE` | CRITICAL |
| `MONGODB_CONNECTIONS` | WARNING / CRITICAL by threshold |
| `MONGODB_MEMBER_UNHEALTHY` | CRITICAL |
| `MONGODB_NO_PRIMARY` | CRITICAL |
| `MYSQL_UNAVAILABLE` | CRITICAL |
| `MYSQL_CONNECTIONS` | WARNING / CRITICAL by threshold |
| `MYSQL_REPLICATION_STOPPED` | CRITICAL |
| `MYSQL_REPLICATION_LAG` | WARNING / CRITICAL by threshold |
| `MYSQL_BUFFER_POOL` | INFO |
| `MYSQL_RECENT_START` | INFO |
| `ORACLE_SQLPLUS_MISSING` | UNKNOWN |
| `ORACLE_UNAVAILABLE` | CRITICAL |
| `ORACLE_NOT_OPEN` | CRITICAL |
| `ORACLE_STANDBY_MODE` | WARNING |
| `ORACLE_INSTANCE_STATUS` | CRITICAL |
| `ORACLE_NOARCHIVELOG` | WARNING |
| `ORACLE_ARCHIVER_STUCK` | CRITICAL |
| `ORACLE_LISTENER_DOWN` | CRITICAL |
| `ORACLE_RECENT_START` | INFO |
| `ORACLE_TABLESPACE` | WARNING / CRITICAL by threshold |
| `ORACLE_DATAFILE_OFFLINE` | CRITICAL |
| `ORACLE_DATAFILE_RECOVERY` | CRITICAL |
| `ORACLE_FRA_FULL` | WARNING / CRITICAL by threshold |
| `ORACLE_ARCHIVE_DEST_ERROR` | CRITICAL |
| `ORACLE_REDO_NOT_MULTIPLEXED` | INFO |
| `ORACLE_LOG_SWITCHES` | WARNING |
| `ORACLE_CONTROLFILE` | CRITICAL |
| `ORACLE_SINGLE_CONTROLFILE` | WARNING |
| `ORACLE_LIMIT` | WARNING / CRITICAL by threshold |
| `ORACLE_BLOCKING` | WARNING |
| `ORACLE_LONG_RUNNING` | INFO |
| `ORACLE_PGA_OVER_TARGET` | WARNING |
| `ORACLE_RMAN_FAILED` | WARNING |
| `ORACLE_NO_DB_BACKUP` | CRITICAL |
| `ORACLE_BACKUP_OLD` | CRITICAL |
| `ORACLE_ARCHLOG_BACKUP_OLD` | WARNING |
| `ORACLE_DG_DEST` | CRITICAL |
| `ORACLE_DG_LAG` | WARNING / CRITICAL by threshold |
| `ORACLE_MRP_DOWN` | CRITICAL |
| `ORACLE_INVALID_OBJECTS` | WARNING |
| `ORACLE_JOBS_FAILED` | WARNING |
| `ORACLE_JOB_BROKEN` | WARNING |
| `ORACLE_ACCOUNT_EXPIRING` | INFO |
| `ORACLE_ALERT_CRITICAL` | CRITICAL |
| `ORACLE_ALERT_ERRORS` | WARNING |
| `POSTGRES_UNAVAILABLE` | CRITICAL |
| `POSTGRES_CONNECTIONS` | WARNING / CRITICAL by threshold |
| `POSTGRES_XID_WRAPAROUND` | WARNING / CRITICAL by threshold |
| `POSTGRES_LONG_QUERIES` | WARNING |
| `POSTGRES_LOCK_WAITS` | WARNING |
| `POSTGRES_IDLE_IN_TX` | WARNING |
| `POSTGRES_REPLICATION_LAG` | WARNING / CRITICAL by threshold |
| `POSTGRES_REPLICA_STATE` | WARNING |
| `POSTGRES_WAL_RECEIVER` | CRITICAL |
| `POSTGRES_INACTIVE_SLOT` | WARNING |
| `POSTGRES_ARCHIVE_FAILING` | CRITICAL |
| `SQLSERVER_UNAVAILABLE` | CRITICAL |
| `SQLSERVER_DB_STATE` | CRITICAL |
| `SQLSERVER_NO_BACKUP` | WARNING |
| `SQLSERVER_BACKUP_OLD` | CRITICAL |
| `SQLSERVER_JOBS_FAILED` | WARNING |

## Backup & Recovery — `backup` (weight 10)

Backup monitoring: NetBackup (master and client), Backup Exec RALUS, Veeam Agent, generic backup directories/log files and backup cron inventory. Oracle RMAN backups are evaluated by the database module.

| Code | Severity |
|---|---|
| `NBU_JOBS_PARTIAL` | WARNING |
| `NBU_QUERY_FAILED` | UNKNOWN |
| `NBU_NO_IMAGE` | WARNING |
| `NBU_BACKUP_OLD` | CRITICAL |
| `NBU_MASTER_UNREACHABLE` | WARNING |
| `BE_AGENT_DOWN` | WARNING |
| `BE_AGENT_PORT` | WARNING |
| `VEEAM_NO_SUCCESS` | WARNING |
| `VEEAM_BACKUP_OLD` | CRITICAL |
| `VEEAM_LAST_FAILED` | WARNING |
| `BACKUP_TARGET_MISSING` | CRITICAL |
| `BACKUP_NONE_FOUND` | CRITICAL |
| `BACKUP_STALE` | CRITICAL |
| `BACKUP_TOO_SMALL` | WARNING |
| `BACKUP_LOG_ERROR` | WARNING |
| `BACKUP_NOT_CONFIGURED` | INFO |

## Logs & Events — `logs` (weight 10)

Log analysis over the configured window (journald or classic syslog files).  Lines are classified by pattern, counted, and turned into findings with sample evidence, rather than dumped raw.

| Code | Severity |
|---|---|
| `LOG_OOM` | CRITICAL |
| `LOG_FS_ERROR` | CRITICAL |
| `LOG_IO_ERROR` | CRITICAL |
| `LOG_HARDWARE` | CRITICAL |
| `LOG_MULTIPATH` | CRITICAL |
| `LOG_NFS` | WARNING |
| `LOG_KERNEL` | WARNING |
| `LOG_SEGFAULT` | WARNING |
| `LOG_SERVICE_FAIL` | WARNING |
| `LOG_ROOT_LOGIN` | INFO |
| `LOG_SUDO` | INFO |
| `LOG_SELINUX` | WARNING |
| `LOG_FIREWALL` | INFO |
| `LOG_CRON_FAIL` | WARNING |
| `LOG_NTP` | WARNING |
| `LOG_SSH_FAILURES` | WARNING / CRITICAL by count |
| `LOG_REBOOTS` | INFO |
| `LOGS_UNAVAILABLE` | UNKNOWN |

## Security & Patching — `security` (weight 8)

Security posture: SELinux, firewall, SSH policy, privileged accounts, audit, pending (security) updates and last patch date.

| Code | Severity |
|---|---|
| `SELINUX_MODE` | WARNING / CRITICAL by threshold |
| `FIREWALL_INACTIVE` | WARNING |
| `SSH_ROOT_LOGIN` | WARNING |
| `SSH_PASSWORD_AUTH` | WARNING |
| `SSH_EMPTY_PASSWORDS` | CRITICAL |
| `SSH_PROTOCOL1` | CRITICAL |
| `EXTRA_UID0` | CRITICAL |
| `EMPTY_PASSWORD` | CRITICAL |
| `AUDITD_INACTIVE` | WARNING |
| `SUDO_NOPASSWD` | INFO |
| `UPDATES_UNKNOWN` | UNKNOWN |
| `SECURITY_UPDATES` | WARNING |
| `UPDATES_PENDING` | INFO |

## TLS Certificates — `certificates` (weight 4)

TLS certificate expiry for configured files, directories and network endpoints.

| Code | Severity |
|---|---|
| `OPENSSL_MISSING` | UNKNOWN |
| `CERT_UNREADABLE` | WARNING |
| `CERT_EXPIRED` | CRITICAL |
| `CERT_EXPIRING` | CRITICAL |

## Containers — `containers` (weight 4)

Docker / Podman container health.

| Code | Severity |
|---|---|
| `CONTAINER_UNHEALTHY` | WARNING |
| `CONTAINER_RESTARTING` | WARNING |
| `CONTAINER_EXITED` | WARNING |

## High Availability Cluster — `cluster` (weight 8)

High availability: Pacemaker/Corosync cluster status and quorum.

| Code | Severity |
|---|---|
| `CLUSTER_NOT_RUNNING` | INFO |
| `CLUSTER_STATUS_FAILED` | UNKNOWN |
| `CLUSTER_NO_QUORUM` | CRITICAL |
| `CLUSTER_NODE_OFFLINE` | CRITICAL |
| `CLUSTER_RESOURCE_STOPPED` | WARNING |
| `CLUSTER_FAILED_ACTIONS` | WARNING |
| `CLUSTER_MAINTENANCE` | INFO |

## Applications & Mail — `applications` (weight 6)

Application endpoints (HTTP/HTTPS checks) and mail queue health.

| Code | Severity |
|---|---|
| `HTTP_CHECK_FAILED` | CRITICAL |
| `HTTP_SLOW` | WARNING |
| `MAIL_QUEUE` | WARNING / CRITICAL by threshold |

## Custom Checks — `custom` (weight 6)

Administrator-defined checks using the Nagios plugin convention.  Exit code 0=OK, 1=WARNING, 2=CRITICAL, 3=UNKNOWN; the first output line is the message. Checks come from custom.conf [check:NAME] sections and from an optional checks directory. Scripts must be owned by root and not writable by group/others, otherwise they are refused.

| Code | Severity |
|---|---|
| `CUSTOM_CHECK_REFUSED` | WARNING |
| `CUSTOM_CHECK` | WARNING / CRITICAL by threshold |

## Trends & Capacity Forecast — `trends`

Added automatically from the history database on full runs.

| Code | Severity |
|---|---|
| `FS_FORECAST_FULL` | WARNING / CRITICAL by threshold |
| `KVM_POOL_FORECAST_FULL` | WARNING |
| `RECURRING_CRITICAL` | WARNING |

## Configuration Drift — `drift`

Compares each full run with the saved baseline (`healthcheck baseline save`).

| Code | Severity |
|---|---|
| `DRIFT_UID0_ACCOUNTS` | CRITICAL |
| `DRIFT_LOGIN_ACCOUNTS` | WARNING |
| `DRIFT_LISTENING_PORTS` | WARNING |
| `DRIFT_ENABLED_SERVICES` | WARNING |
| `DRIFT_MOUNTS` | WARNING |
| `DRIFT_CONFIG_HASHES` | WARNING |
| `DRIFT_CRON_FILES` | WARNING |
| `DRIFT_PACKAGES` | INFO |
| `DRIFT_KERNEL` | INFO |

## Engine

| Code | Severity | Meaning |
|---|---|---|
| `MODULE_ERROR` | UNKNOWN | A module raised an exception; other modules still ran |
| `RUN_TIMEOUT` | UNKNOWN | The overall run time limit was reached before this module |

