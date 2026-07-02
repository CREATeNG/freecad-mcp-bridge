#!/usr/bin/env bash
# Increment package.xml patch (z) and <date>, and sync the shim's version fields —
# INTERNAL to GitHub Actions only.
# Triggered by bump-package-version.yml (workflow_dispatch), or post-release in release.yml publish job.

set -euo pipefail

if [[ "${BUMP_PACKAGE_Z_AUTHORIZED:-}" != "true" ]]; then
  echo "bump-package-z.sh is internal to GitHub Actions." >&2
  exit 2
fi

PACKAGE_XML="${PACKAGE_XML:-package.xml}"
SHIM_MANIFEST="${SHIM_MANIFEST:-mcp-stdio-shim/manifest.json}"
SHIM_PACKAGE_JSON="${SHIM_PACKAGE_JSON:-mcp-stdio-shim/package.json}"

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

python3 - <<'PY' "$next_version" "$today" "$PACKAGE_XML" "$SHIM_MANIFEST" "$SHIM_PACKAGE_JSON"
import re
import sys
from pathlib import Path

version, date, package_xml, shim_manifest, shim_package_json = sys.argv[1:6]

path = Path(package_xml)
text = path.read_text(encoding="utf-8")
text, n1 = re.subn(
    r"(<version>)[^<]*(</version>)", rf"\g<1>{version}\g<2>", text, count=1
)
text, n2 = re.subn(r"(<date>)[^<]*(</date>)", rf"\g<1>{date}\g<2>", text, count=1)
if n1 != 1 or n2 != 1:
    raise SystemExit("Failed to update version/date in package.xml")
path.write_text(text, encoding="utf-8")

for json_path in (shim_manifest, shim_package_json):
    p = Path(json_path)
    text = p.read_text(encoding="utf-8")
    text, n = re.subn(
        r'("version":\s*")[^"]*(")', rf"\g<1>{version}\g<2>", text, count=1
    )
    if n != 1:
        raise SystemExit(f"Failed to update version in {json_path}")
    p.write_text(text, encoding="utf-8")
PY

if git diff --quiet "$PACKAGE_XML" "$SHIM_MANIFEST" "$SHIM_PACKAGE_JSON"; then
  echo "Versions unchanged"
  exit 0
fi

git add "$PACKAGE_XML" "$SHIM_MANIFEST" "$SHIM_PACKAGE_JSON"
git commit -m "chore: bump version to ${next_version}"
git push origin HEAD

echo "Bumped package.xml, shim manifest, and shim package.json to ${next_version}"