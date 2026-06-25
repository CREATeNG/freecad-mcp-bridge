#!/usr/bin/env bash
# Update package.xml <date> only — INTERNAL to GitHub Actions only.
# Triggered on every push to main (update-package-xml-on-push.yml).

set -euo pipefail

if [[ "${BUMP_PACKAGE_DATE_AUTHORIZED:-}" != "true" ]]; then
  echo "bump-package-date.sh is internal to GitHub Actions." >&2
  exit 2
fi

PACKAGE_XML="${PACKAGE_XML:-package.xml}"
today=$(date -u +%Y-%m-%d)

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

if git diff --quiet "$PACKAGE_XML"; then
  echo "package.xml date already ${today}"
  exit 0
fi

git add "$PACKAGE_XML"
git commit -m "chore: update package.xml date to ${today}"
git push origin HEAD

echo "Updated package.xml date to ${today}"