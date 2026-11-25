#!/usr/bin/env bash
# Linux Server Health Monitor Enterprise
# Copyright (c) 2026 LRS Services Pvt Ltd, Mumbai. All rights reserved.
#
# Upgrade an installed LRS Vitals to the newest code on GitHub, directly on the server (no RPM needed):
#
#   curl -fsSL https://raw.githubusercontent.com/rohittrimukhe/linux-server-health-monitor/main/tools/upgrade_from_github.sh | sudo bash
#   sudo bash upgrade_from_github.sh [--ref main] [--dry-run] [--app-dir /opt/lshme]
#
# It replaces only the program files (the lshme package, docs and tools). Your settings in /etc/lshme, the history, the reports and the raw
# data are not touched. The old program is kept as /opt/lshme.pre-upgrade.<time>. The live watcher and the web service are restarted
# when they are running. Run it on every server; a later RPM upgrade simply replaces these files again.
set -euo pipefail

REF="main"
DRY=0
APP="/opt/lshme"
REPO="rohittrimukhe/linux-server-health-monitor"
while [ $# -gt 0 ]; do
  case "$1" in
    --ref) REF="${2:-main}"; shift ;;
    --dry-run) DRY=1 ;;
    --app-dir) APP="${2:-/opt/lshme}"; shift ;;
    -h|--help) sed -n '2,13p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown option $1" >&2; exit 2 ;;
  esac
  shift
done

[ "$(id -u)" = 0 ] || { echo "run as root (sudo)" >&2; exit 2; }
[ -d "$APP/lshme" ] || { echo "LRS Vitals is not installed in $APP (install the RPM first)" >&2; exit 2; }
command -v curl >/dev/null 2>&1 || { echo "curl is needed" >&2; exit 2; }
command -v tar >/dev/null 2>&1 || { echo "tar is needed" >&2; exit 2; }
PY=python3
command -v "$PY" >/dev/null 2>&1 || { echo "python3 is needed" >&2; exit 2; }

TMP="$(mktemp -d /tmp/lrs-upgrade.XXXXXX)"
trap 'rm -rf "$TMP"' EXIT
URL="https://github.com/$REPO/archive/refs/heads/$REF.tar.gz"
echo "== downloading $URL"
curl -fsSL "$URL" -o "$TMP/src.tar.gz"
tar -xzf "$TMP/src.tar.gz" -C "$TMP"
SRC="$(find "$TMP" -maxdepth 1 -mindepth 1 -type d | head -1)"
[ -f "$SRC/lshme/__init__.py" ] || { echo "the download does not look like LRS Vitals" >&2; exit 2; }
NEWVER="$(sed -n 's/^VERSION *= *"\(.*\)".*/\1/p' "$SRC/lshme/__init__.py" | head -1)"
OLDVER="$(sed -n 's/^VERSION *= *"\(.*\)".*/\1/p' "$APP/lshme/__init__.py" | head -1)"
echo "== installed: ${OLDVER:-?}   new: ${NEWVER:-?}"

echo "== checking the new code compiles with $($PY --version 2>&1)"
$PY -m compileall -q "$SRC/lshme" >/dev/null || { echo "the new code does not compile with this Python: nothing was changed" >&2; exit 3; }
find "$SRC/lshme" -name __pycache__ -type d -prune -exec rm -rf {} +

if [ "$DRY" = 1 ]; then
  echo "(dry run) would replace $APP/lshme, $APP/docs and $APP/tools; keep a copy as $APP.pre-upgrade.<time>; restart lshme-watch and lshme-web"
  exit 0
fi

STAMP="$(date +%Y%m%d%H%M%S)"
cp -a "$APP" "$APP.pre-upgrade.$STAMP"
echo "== old program kept as $APP.pre-upgrade.$STAMP"
rm -rf "$APP/lshme.new" && cp -a "$SRC/lshme" "$APP/lshme.new"
rm -rf "$APP/lshme.old" && mv "$APP/lshme" "$APP/lshme.old" && mv "$APP/lshme.new" "$APP/lshme" && rm -rf "$APP/lshme.old"
for d in docs tools; do
  [ -d "$SRC/$d" ] && { rm -rf "$APP/$d"; cp -a "$SRC/$d" "$APP/$d"; }
done
for f in healthcheck CHANGELOG.md; do [ -f "$SRC/$f" ] && cp -a "$SRC/$f" "$APP/$f"; done
chmod +x "$APP/healthcheck" 2>/dev/null || true
SHA="$(curl -fsSL -H 'User-Agent: lrs-vitals-updater' "https://api.github.com/repos/$REPO/commits/$REF" 2>/dev/null | grep -o '"sha": *"[0-9a-f]\{40\}"' | head -1 | grep -o '[0-9a-f]\{40\}')"   # compact or pretty JSON
[ -n "$SHA" ] && echo "$SHA" > "$APP/.lrs_commit"          # the automatic update knows which version is installed
command -v restorecon >/dev/null 2>&1 && restorecon -R "$APP" >/dev/null 2>&1 || true

echo "== checking"
vitals version | head -1
vitals doctor >/dev/null 2>&1 && echo "doctor: ok" || echo "doctor: reported something, run: vitals doctor"
for u in lshme-watch lshme-web lshme-web-test; do
  if systemctl is-active --quiet "$u" 2>/dev/null; then systemctl restart "$u" && echo "restarted $u"; fi
done
echo "== done. Automatic updates: vitals update status (every day at 21:00 unless [update] auto = no). If anything is wrong, go back with:"
echo "   rm -rf $APP && mv $APP.pre-upgrade.$STAMP $APP && systemctl restart lshme-watch"
