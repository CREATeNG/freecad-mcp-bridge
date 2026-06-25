#!/usr/bin/env bash
# Increment package.xml patch (z) and <date> — INTERNAL to GitHub Actions only.
# Triggered by [bump version] in a push commit message (bump-package-version-on-push.yml), or post-release via release-publish-orchestrator.sh.

set -euo pipefail

if [[ "${BUMP_PACKAGE_Z_AUTHORIZED:-}" != "true" ]]; then
  echo "bump-package-z.sh is internal to GitHub Actions." >&2
  exit 2
fi

PACKAGE_XML="${PACKAGE_XML:-package.xml}"

read_version() {
  sed -n 's/.*<version>\([^<]*\)<\/version>.*/\1/p' "$PACKAGE_XML" | head -1
}

version=$(read_version)
if [[ -z "$version" ]]; then
  echo "Missing <version> in $PACKAGE_XML" >&2
  exit 1
fi
if [[ ! "$version" =~ ^([0-9]+)\.([0-9]+)\.([0-9]+)$ ]]; then
  echo "package.xml must use x.y.z (found: ${version})" >&2
  exit 1
fi

x="${BASH_REMATCH[1]}"
y="${BASH_REMATCH[2]}"
z="${BASH_REMATCH[3]}"
next_z=$((z + 1))
next_version="${x}.${y}.${next_z}"
today=$(date -u +%Y-%m-%d)

python3 - <<'PY' "$next_version" "$today" "$PACKAGE_XML"
import re
import sys
from pathlib import Path

version, date, package_xml = sys.argv[1], sys.argv[2], sys.argv[3]
path = Path(package_xml)
text = path.read_text(encoding="utf-8")
text, n1 = re.subn(r"(<version>)[^<]*(</version>)", rf"\1{version}\2", text, count=1)
text, n2 = re.subn(r"(<date>)[^<]*(</date>)", rf"\1{date}\2", text, count=1)
if n1 != 1 or n2 != 1:
    raise SystemExit("Failed to update version/date in package.xml")
path.write_text(text, encoding="utf-8")
PY

bash "$(dirname "${BASH_SOURCE[0]}")/sync-cargo-version.sh" "$next_version"

if git diff --quiet "$PACKAGE_XML" rust_mcp_server/Cargo.toml; then
  echo "package.xml and Cargo.toml unchanged"
  exit 0
fi

git add "$PACKAGE_XML" rust_mcp_server/Cargo.toml
git commit -m "chore: bump version to ${next_version}"
git push origin HEAD

echo "Bumped package.xml and Cargo.toml to ${next_version}"