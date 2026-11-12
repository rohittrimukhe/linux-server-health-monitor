#!/usr/bin/env bash
# Copyright (c) 2026 LRS Services Pvt Ltd, Mumbai. All rights reserved.
# Linux Server Health Monitor Enterprise - uninstaller
#   sudo ./uninstall.sh            remove program and schedules; keep config, history and reports
#   sudo ./uninstall.sh --purge    also delete /etc/lshme, /var/lib/lshme and /var/log/lshme
set -euo pipefail
PREFIX=/opt/lshme
CONFIG=/etc/lshme
PURGE=0
BIN=/usr/local/bin/healthcheck
while [ $# -gt 0 ]; do
  case "$1" in
    --prefix) PREFIX="$2"; shift 2 ;;
    --config) CONFIG="$2"; shift 2 ;;
    --bin) BIN="$2"; shift 2 ;;
    --purge) PURGE=1; shift ;;
    --yes) YES=1; shift ;;
    *) echo "unknown option: $1" >&2; exit 1 ;;
  esac
done
[ "$(id -u)" -eq 0 ] || { echo "Run as root." >&2; exit 1; }
if [ -z "${YES:-}" ]; then
  if [ $PURGE -eq 1 ]; then
    echo "This removes LSHME, its schedules, configuration, history, reports and logs."
  else
    echo "This removes LSHME and its schedules. Configuration, history and reports are kept."
  fi
  read -r -p "Type REMOVE to continue: " answer
  [ "$answer" = "REMOVE" ] || { echo "Cancelled."; exit 1; }
fi
if [ -x "$PREFIX/healthcheck" ]; then
  "$PREFIX/healthcheck" --config-dir "$CONFIG" schedule remove || true
  "$PREFIX/healthcheck" --config-dir "$CONFIG" watch remove || true
fi
rm -f /etc/cron.d/lshme /etc/cron.d/lshme-watch /etc/systemd/system/lshme-*.service /etc/systemd/system/lshme-*.timer
[ -f /usr/share/cockpit/lshme/lshme.js ] && rm -rf /usr/share/cockpit/lshme
command -v systemctl >/dev/null 2>&1 && systemctl daemon-reload 2>/dev/null || true
if [ "$(readlink -f "$BIN" 2>/dev/null)" = "$(readlink -f "$PREFIX/healthcheck" 2>/dev/null)" ]; then
  rm -f "$BIN"
fi
rm -rf "$PREFIX" "$PREFIX.previous"
case "$CONFIG" in
  /etc/*) DATA=/var/lib/lshme; LOGS=/var/log/lshme; REPORTS="" ;;
  *) BASE="$(dirname "$CONFIG")"; DATA="$BASE/data"; LOGS="$BASE/logs"; REPORTS="$BASE/reports" ;;
esac
if [ $PURGE -eq 1 ]; then
  rm -rf "$CONFIG" "$DATA" "$LOGS" ${REPORTS:+"$REPORTS"}
  echo "LSHME and all its data removed."
else
  echo "LSHME removed. Kept: $CONFIG, $DATA, $LOGS"
fi
