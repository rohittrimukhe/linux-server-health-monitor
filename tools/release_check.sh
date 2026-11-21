#!/usr/bin/env bash
# Linux Server Health Monitor Enterprise
# Copyright (c) 2026 LRS Services Pvt Ltd, Mumbai. All rights reserved.
#
# Release gate: run from the repository root on a Linux test server before building a release.
#   bash tools/release_check.sh            syntax, tests, secret scan, version consistency
#   bash tools/release_check.sh --quick    the same without the full test suite
# Exit code 0 only when every check passes.
set -uo pipefail
cd "$(dirname "$0")/.." || exit 2
QUICK=0; [ "${1:-}" = "--quick" ] && QUICK=1
FAIL=0
step() { printf "\n== %s\n" "$1"; }
ok() { echo "   OK  $1"; }
bad() { echo "   FAIL $1"; FAIL=$((FAIL + 1)); }

step "Python syntax (every file compiles with $(python3 --version 2>&1))"
if python3 -m compileall -q lshme tests tools >/dev/null; then ok "python"; else bad "python does not compile"; fi
find lshme tests tools -name __pycache__ -type d -prune -exec rm -rf {} + 2>/dev/null

step "Shell scripts"
for f in tools/*.sh install.sh uninstall.sh; do [ -f "$f" ] && { bash -n "$f" && ok "$f" || bad "$f"; }; done

step "JavaScript"
if command -v node >/dev/null 2>&1; then
  for f in lshme/web/static/app.js lshme/cockpit/lshme.js lshme/cockpit/charts.js; do node --check "$f" && ok "$f" || bad "$f"; done
else
  echo "   skip (node is not installed)"
fi

step "Secret scan (no keys, passwords or tokens in the code)"
HITS=$(grep -rInE "(BEGIN (RSA|OPENSSH|EC) PRIVATE KEY|AKIA[0-9A-Z]{16}|xox[baprs]-[0-9A-Za-z-]{10,}|password *= *[^ \$\"'{][^ ]{5,})" lshme config tools docs 2>/dev/null | grep -v "release_check.sh" | head -5)
if [ -z "$HITS" ]; then ok "nothing found"; else bad "possible secret:"; echo "$HITS" | sed 's/^/      /'; fi

step "Version consistency"
VER=$(sed -n 's/^VERSION *= *"\(.*\)".*/\1/p' lshme/__init__.py | head -1)
SPEC=$(sed -n 's/^Version: *//p' packaging/lrs-vitals.spec | head -1)
REL=$(sed -n 's/^Release: *//p' packaging/lrs-vitals.spec | head -1)
[ "$VER" = "$SPEC" ] && ok "code $VER = spec $SPEC" || bad "code $VER and spec $SPEC differ"
grep -q "lrs-vitals-$SPEC-$REL.noarch.rpm" docs/DEPLOYMENT.md && ok "DEPLOYMENT.md names $SPEC-$REL" || bad "DEPLOYMENT.md does not name lrs-vitals-$SPEC-$REL.noarch.rpm"
grep -q "^## $VER" CHANGELOG.md && ok "CHANGELOG has $VER" || bad "CHANGELOG has no section for $VER"

if [ "$QUICK" = 0 ]; then
  step "Test suite"
  if (cd tests && python3 -W ignore -m unittest discover 2>&1 | tail -4 | tee /tmp/lrs-release-tests.txt | grep -q "^OK"); then ok "all tests pass"; else bad "tests failed:"; cat /tmp/lrs-release-tests.txt; fi
fi

echo
[ "$FAIL" = 0 ] && echo "RELEASE CHECK PASSED" || echo "RELEASE CHECK FAILED: $FAIL problem(s)"
[ "$FAIL" = 0 ]
