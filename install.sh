#!/usr/bin/env bash
# Linux Server Health Monitor Enterprise - installer / upgrader
#
#   sudo ./install.sh                     install or upgrade to /opt/lshme
#   sudo ./install.sh --with-optional     also install PDF/XLSX/PPTX libraries from OS repos (or pip)
#   sudo ./install.sh --prefix /opt/x --config /etc/x --bin /usr/local/bin/healthcheck
#   sudo ./install.sh --setup             run the configuration wizard after installing
#   ./install.sh --check                  dependency check only
#
# Upgrades keep configuration, history and reports; the previous code is kept
# in <prefix>.previous for rollback. Nothing outside the listed paths is changed.
set -euo pipefail

PREFIX=/opt/lshme
CONFIG=/etc/lshme
BIN=/usr/local/bin/healthcheck
WITH_OPTIONAL=0
CHECK_ONLY=0
SETUP=0
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

while [ $# -gt 0 ]; do
  case "$1" in
    --prefix) PREFIX="$2"; shift 2 ;;
    --config) CONFIG="$2"; shift 2 ;;
    --bin) BIN="$2"; shift 2 ;;
    --with-optional) WITH_OPTIONAL=1; shift ;;
    --check) CHECK_ONLY=1; shift ;;
    --setup) SETUP=1; shift ;;
    -h|--help) sed -n '2,12p' "$0"; exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 1 ;;
  esac
done

say() { printf '%s\n' "$*"; }
ok() { printf '  [ OK ] %s\n' "$*"; }
warn() { printf '  [WARN] %s\n' "$*"; }
fail() { printf '  [FAIL] %s\n' "$*"; }

find_python() {
  for p in python3 python3.12 python3.11 python3.9 python3.8 python3.6 /usr/libexec/platform-python; do
    if command -v "$p" >/dev/null 2>&1 && "$p" -c 'import sys; sys.exit(0 if sys.version_info >= (3,6) else 1)' 2>/dev/null; then
      command -v "$p"; return 0
    fi
  done
  return 1
}

check_deps() {
  local rc=0
  say "Dependency check"
  if PY="$(find_python)"; then ok "Python: $("$PY" -V 2>&1) ($PY)"; else fail "Python 3.6+ not found (yum/dnf install python3)"; rc=1; fi
  for t in df ps lsblk ip ss systemctl journalctl openssl; do
    command -v "$t" >/dev/null 2>&1 && ok "$t" || warn "$t not found (some checks will be skipped)"
  done
  for t in lvs multipath virsh sqlplus smartctl sensors dmidecode; do
    command -v "$t" >/dev/null 2>&1 && ok "$t (optional)" || say "  [ -- ] $t not installed (optional)"
  done
  if [ -n "${PY:-}" ]; then
    for m in reportlab openpyxl pptx; do
      "$PY" -c "import $m" 2>/dev/null && ok "python module $m" || say "  [ -- ] python module $m not installed (built-in fallback will be used)"
    done
  fi
  return $rc
}

install_optional() {
  say "Installing optional report libraries"
  if command -v dnf >/dev/null 2>&1; then
    dnf -y install python3-reportlab python3-openpyxl 2>/dev/null || warn "some packages unavailable in enabled repos (EPEL provides them)"
  elif command -v yum >/dev/null 2>&1; then
    yum -y install python36-reportlab python36-openpyxl 2>/dev/null || warn "some packages unavailable (enable EPEL)"
  elif command -v apt-get >/dev/null 2>&1; then
    apt-get install -y python3-reportlab python3-openpyxl 2>/dev/null || warn "apt install failed"
  fi
  if ! "$PY" -c "import pptx" 2>/dev/null; then
    "$PY" -m pip install --quiet python-pptx 2>/dev/null && ok "python-pptx installed with pip" || warn "python-pptx not installed (text-only PPTX fallback will be used)"
  fi
}

# Same layout rules as the application (lshme/core/config.py Paths):
# config under /etc -> /var/lib/lshme + /var/log/lshme; otherwise siblings of the config directory.
case "$CONFIG" in
  /etc/*) DATA=/var/lib/lshme; LOGS=/var/log/lshme; REPORTS=/var/lib/lshme/reports ;;
  *) BASE="$(dirname "$CONFIG")"; DATA="$BASE/data"; LOGS="$BASE/logs"; REPORTS="$BASE/reports" ;;
esac

check_deps || { [ $CHECK_ONLY -eq 1 ] && exit 1; }
[ $CHECK_ONLY -eq 1 ] && exit 0
if [ "$(id -u)" -ne 0 ]; then echo "Run the installer as root (sudo ./install.sh)." >&2; exit 1; fi
PY="$(find_python)"

UPGRADE=0
[ -f "$PREFIX/lshme/__init__.py" ] && UPGRADE=1
NEWVER="$(sed -n 's/^VERSION = "\(.*\)"/\1/p' "$SRC/lshme/__init__.py")"
if [ $UPGRADE -eq 1 ]; then
  OLDVER="$(sed -n 's/^VERSION = "\(.*\)"/\1/p' "$PREFIX/lshme/__init__.py")"
  say "Upgrading LSHME $OLDVER -> $NEWVER in $PREFIX"
  rm -rf "$PREFIX.previous"
  cp -a "$PREFIX" "$PREFIX.previous"
else
  say "Installing LSHME $NEWVER into $PREFIX"
fi

mkdir -p "$PREFIX" "$CONFIG" "$DATA" "$REPORTS" "$LOGS" "$(dirname "$BIN")"
# code: replace, never touch config/data
rm -rf "$PREFIX/lshme"
cp -a "$SRC/lshme" "$PREFIX/"
for f in healthcheck install.sh uninstall.sh README.md; do
  [ -f "$SRC/$f" ] && cp -a "$SRC/$f" "$PREFIX/"
done
rm -rf "$PREFIX/docs" "$PREFIX/config-templates"
[ -d "$SRC/docs" ] && cp -a "$SRC/docs" "$PREFIX/docs"
cp -a "$SRC/config" "$PREFIX/config-templates"
find "$PREFIX/lshme" -name '__pycache__' -prune -exec rm -rf {} +
chmod 755 "$PREFIX" "$PREFIX/healthcheck" "$PREFIX/install.sh" "$PREFIX/uninstall.sh"
chown -R root:root "$PREFIX"
chmod -R go-w "$PREFIX"

# config: copy templates only where no file exists yet
NEWCONF=0
for t in "$SRC"/config/*.conf; do
  name="$(basename "$t")"
  if [ ! -e "$CONFIG/$name" ]; then
    cp "$t" "$CONFIG/$name"; NEWCONF=1
  fi
done
chmod 700 "$CONFIG"
chmod 600 "$CONFIG"/*.conf
chmod 750 "$DATA" "$REPORTS" "$LOGS"

ln -sf "$PREFIX/healthcheck" "$BIN"
ok "command installed: $BIN"

if [ $WITH_OPTIONAL -eq 1 ]; then install_optional; fi

# refresh schedules on upgrade so unit files point at the new code
if [ $UPGRADE -eq 1 ]; then
  if [ -e /etc/cron.d/lshme ] || ls /etc/systemd/system/lshme-*.timer >/dev/null 2>&1; then
    if "$BIN" --config-dir "$CONFIG" schedule install >/dev/null; then ok "schedules refreshed"; else warn "schedule refresh failed: run 'healthcheck schedule install'"; fi
  fi
fi

"$BIN" --config-dir "$CONFIG" doctor >/dev/null 2>&1 && ok "self-diagnostics passed" || warn "self-diagnostics reported problems: run 'healthcheck doctor'"

say ""
say "Done. Next steps:"
if [ $NEWCONF -eq 1 ] && [ $UPGRADE -eq 0 ]; then
  say "  healthcheck setup        # guided configuration (SMTP, schedules, databases, thresholds...)"
fi
say "  healthcheck              # interactive menu"
say "  healthcheck run --formats all"
say "  healthcheck schedule install"
[ $SETUP -eq 1 ] && exec "$BIN" --config-dir "$CONFIG" setup
exit 0
