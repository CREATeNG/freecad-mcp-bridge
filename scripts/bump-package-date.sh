#!/usr/bin/env bash
# Update package.xml <date> only — INTERNAL to GitHub Actions only.
# Triggered on every push to main (update-package-xml-on-push.yml).

set -euo pipefail

if [[ "${BUMP_PACKAGE_DATE_AUTHORIZED:-}" != "true" ]]; then
  echo "bump-package-date.sh is internal to GitHub Actions." >&2
  exit 2
fi

PACKAGE_XML="${PACKAGE_XML:-package.xml}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
today=$(date -u +%Y-%m-%d)

read_version() {
  sed -n 's/.*<version>\([^<]*\)<\/version>.*/\1/p' "$PACKAGE_XML" | head -1
}

python3 - <<'PY' "$today" "$PACKAGE_XML"
import re
import sys
from pathlib import Path

date, package_xml = sys.argv[1], sys.argv[2]
path = Path(package_xml)
text = path.read_text(encoding="utf-8")
text, n = re.subn(r"(<date>)[^<]*(</date>)", rf"\1{date}\2", text, count=1)
if n != 1:
    raise SystemExit("Failed to update date in package.xml")
path.write_text(text, encoding="utf-8")
PY

version=$(read_version)
if [[ -z "$version" ]]; then
  echo "Missing <version> in $PACKAGE_XML" >&2
  exit 1
fi
bash "${SCRIPT_DIR}/sync-cargo-version.sh" "$version"

if git diff --quiet "$PACKAGE_XML" rust_mcp_server/Cargo.toml; then
  echo "package.xml date already ${today}; Cargo.toml already ${version}"
  exit 0
fi

git add "$PACKAGE_XML" rust_mcp_server/Cargo.toml
git commit -m "chore: update package.xml date to ${today}"
git push origin HEAD

echo "Updated package.xml date to ${today}; Cargo.toml at ${version}"