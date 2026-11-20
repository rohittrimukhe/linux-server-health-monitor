#!/usr/bin/env bash
# Linux Server Health Monitor Enterprise
# Copyright (c) 2026 LRS Services Pvt Ltd, Mumbai. All rights reserved.
#
# Roll LRS Vitals out to other servers from the central server, with the same settings everywhere.
#
#   sudo tools/deploy_fleet.sh --rpm lrs-vitals-1.4.0-5.noarch.rpm [--config-from /etc/lshme] [--dry-run] [--test-email] \
#        db1.example.com:Production db2.example.com:Production kvm2.example.com:Production
#
# Run it as root on the central server, after the central server itself is installed and set up (email, schedules, ...).
# Each target is HOST or HOST:ENVIRONMENT. Needs SSH key login as root to every target (ssh-copy-id root@HOST).
# For each target it: copies the package, installs it, copies the shared settings (email, schedules, reports, retention,
# thresholds, alert settings, credentials), scans the server so only what is present is checked, installs the schedules and the
# live watcher, and prints the result. Nothing on the targets is changed except LRS Vitals' own files; the old /etc/lshme is
# kept as /etc/lshme.pre-deploy.<time>. Web and fleet settings stay on the central server only.
set -euo pipefail

DRY=0
RPM=""
SRC="/etc/lshme"
MAILTEST=0
SHARED="main.conf smtp.conf credentials.conf schedule.conf reports.conf retention.conf thresholds.conf services.conf exclusions.conf"

usage() { sed -n '2,16p' "$0" | sed 's/^# \{0,1\}//'; }
die() { echo "error: $*" >&2; exit 2; }

while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run) DRY=1 ;;
    --test-email) MAILTEST=1 ;;
    --rpm) RPM="${2:-}"; shift ;;
    --config-from) SRC="${2:-}"; shift ;;
    -h|--help) usage; exit 0 ;;
    --) shift; break ;;
    -*) die "unknown option $1" ;;
    *) break ;;
  esac
  shift
done

[ -n "$RPM" ] && [ -f "$RPM" ] || die "give the package with --rpm FILE"
[ -d "$SRC" ] || die "$SRC does not exist: set the central server up first"
[ $# -ge 1 ] || die "give at least one target server (HOST or HOST:ENVIRONMENT)"

SSH="ssh -o BatchMode=yes -o ConnectTimeout=15 -o StrictHostKeyChecking=accept-new"
SCP="scp -o BatchMode=yes -o ConnectTimeout=15 -o StrictHostKeyChecking=accept-new"
PKG="$(basename "$RPM")"
FILES=""
for f in $SHARED; do [ -f "$SRC/$f" ] && FILES="$FILES $f"; done

run() { if [ "$DRY" = 1 ]; then echo "  + $*"; else "$@"; fi; }
ok=0
bad=0
for target in "$@"; do
  host="${target%%:*}"
  envname="${target#*:}"
  [ "$envname" = "$target" ] && envname=""
  echo "== $host ${envname:+($envname)}"
  if [ "$DRY" = 0 ] && ! $SSH "root@$host" true 2>/dev/null; then
    echo "  cannot log in as root with a key: run  ssh-copy-id root@$host  and try again"; bad=$((bad + 1)); continue
  fi
  run $SCP "$RPM" "root@$host:/root/$PKG"
  run $SSH "root@$host" "set -e; if command -v dnf >/dev/null 2>&1; then dnf -y install /root/$PKG; else yum -y localinstall /root/$PKG; fi"
  run $SSH "root@$host" "cp -a /etc/lshme /etc/lshme.pre-deploy.\$(date +%s)"
  if [ "$DRY" = 1 ]; then
    echo "  + tar -C $SRC -cf - $FILES | $SSH root@$host 'tar -C /etc/lshme -xf -'"
  else
    tar -C "$SRC" -cf - $FILES | $SSH "root@$host" "tar -C /etc/lshme -xf - && chmod 600 /etc/lshme/credentials.conf 2>/dev/null; true"
  fi
  [ -n "$envname" ] && run $SSH "root@$host" "vitals config set main general environment '$envname'"
  run $SSH "root@$host" "vitals config set main fleet enabled no && vitals scan --apply --quiet && vitals schedule install && vitals watch install"
  if [ "$MAILTEST" = 1 ]; then
    run $SSH "root@$host" "vitals run --report-type daily --email --quiet"
  else
    run $SSH "root@$host" "vitals run --report-type daily --formats html --quiet"
  fi
  run $SSH "root@$host" "vitals version | head -1; systemctl list-timers 'lshme-*' --no-pager | grep -c lshme- | sed 's/^/  timers installed: /'; systemctl is-active lshme-watch.service | sed 's/^/  live watcher: /'"
  ok=$((ok + 1))
done
echo
echo "done: $ok server(s) installed, $bad skipped"
[ "$DRY" = 1 ] && echo "(dry run: nothing was changed)"
[ "$bad" = 0 ]
