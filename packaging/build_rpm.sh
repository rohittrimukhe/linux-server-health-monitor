#!/usr/bin/env bash
# Copyright (c) 2026 LRS Services Pvt Ltd, Mumbai. All rights reserved.
# Build the RPM:   ./packaging/build_rpm.sh        (needs rpm-build; result in ./dist)
set -euo pipefail
cd "$(dirname "$0")/.."
VER="$(sed -n 's/^VERSION = "\(.*\)"/\1/p' lshme/__init__.py | tr -d '\r')"
[ -n "$VER" ] || { echo "cannot read the version from lshme/__init__.py" >&2; exit 1; }
command -v rpmbuild >/dev/null 2>&1 || { echo "rpmbuild is missing: dnf install rpm-build" >&2; exit 1; }
TOP="$(mktemp -d)"
trap 'rm -rf "$TOP"' EXIT
mkdir -p "$TOP"/{SOURCES,SPECS,BUILD,RPMS,SRPMS} "$TOP/src/lrs-vitals-$VER"
# only what the package needs
cp -a lshme healthcheck config docs packaging README.md CHANGELOG.md LICENSE "$TOP/src/lrs-vitals-$VER/"
find "$TOP/src" -name '__pycache__' -prune -exec rm -rf {} +
find "$TOP/src" -type f \( -name '*.py' -o -name '*.conf' -o -name '*.md' -o -name '*.js' -o -name '*.css' -o -name '*.html' -o -name '*.json' -o -name healthcheck \) -exec sed -i 's/\r$//' {} +
chmod +x "$TOP/src/lrs-vitals-$VER/healthcheck"
tar -C "$TOP/src" -czf "$TOP/SOURCES/lrs-vitals-$VER.tar.gz" "lrs-vitals-$VER"
sed "s/^Version:.*/Version:        $VER/" packaging/lrs-vitals.spec > "$TOP/SPECS/lrs-vitals.spec"
rpmbuild --define "_topdir $TOP" -bb "$TOP/SPECS/lrs-vitals.spec"
mkdir -p dist
cp "$TOP"/RPMS/noarch/*.rpm dist/
ls -l dist/*.rpm
