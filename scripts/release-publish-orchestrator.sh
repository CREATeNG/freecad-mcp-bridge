#!/usr/bin/env bash
# Release publish orchestrator — INTERNAL to .github/workflows/release.yml only.
#
# Atomic sequence (install-verify must have already passed):
#   1. Stamp-only commit: package.xml <version> and <date> only → x.y.0
#   2. Annotated tag on stamp commit where tag === version
#   3. Reset-only commit on main: package.xml → x.(y+1) (two-part dev line)
#
# There is no stamp-only entry point. Do not run this script directly.

set -euo pipefail

if [[ "${RELEASE_PUBLISH_AUTHORIZED:-}" != "true" ]]; then
  echo "release-publish-orchestrator.sh is internal to the Release workflow." >&2
  exit 2
fi

VERIFIED_SHA="${RELEASE_VERIFIED_SHA:?RELEASE_VERIFIED_SHA is required}"
PACKAGE_XML="${RELEASE_PACKAGE_XML:-package.xml}"

require_clean_tree() {
  if [[ -n "$(git status --porcelain)" ]]; then
    echo "Working tree must be clean before publish orchestrator runs." >&2
    git status --porcelain >&2
    exit 1
  fi
}

read_line_version() {
  local raw
  raw=$(sed -n 's/.*<version>\([^<]*\)<\/version>.*/\1/p' "$PACKAGE_XML" | head -1)
  if [[ -z "$raw" ]]; then
    echo "Missing <version> in $PACKAGE_XML" >&2
    exit 1
  fi
  if [[ ! "$raw" =~ ^[0-9]+\.[0-9]+$ ]]; then
    echo "main must use two-part x.y before publish (found: ${raw})" >&2
    exit 1
  fi
  echo "$raw"
}

write_manifest_version_date() {
  local version="$1"
  local date="$2"
  python3 - <<'PY' "$version" "$date" "$PACKAGE_XML"
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
}

assert_only_package_xml_staged() {
  local staged
  staged=$(git diff --cached --name-only)
  if [[ "$staged" != "$PACKAGE_XML" ]]; then
    echo "Only ${PACKAGE_XML} may change in stamp/reset commits (staged: ${staged:-<none>})" >&2
    exit 1
  fi
}

today_utc() {
  date -u +%Y-%m-%d
}

require_clean_tree

current=$(git rev-parse HEAD)
if [[ "$current" != "$VERIFIED_SHA" ]]; then
  echo "HEAD (${current}) must match verified release candidate (${VERIFIED_SHA})" >&2
  exit 1
fi

line_version=$(read_line_version)
x="${line_version%%.*}"
y="${line_version#*.}"
stamp_version="${x}.${y}.0"
stamp_tag="v${stamp_version}"
stamp_date=$(today_utc)

if git rev-parse "$stamp_tag" >/dev/null 2>&1; then
  echo "Tag ${stamp_tag} already exists locally." >&2
  exit 1
fi
if [[ -n "$(git ls-remote --tags origin "${stamp_tag}")" ]]; then
  echo "Tag ${stamp_tag} already exists on origin." >&2
  exit 1
fi

write_manifest_version_date "$stamp_version" "$stamp_date"
git add "$PACKAGE_XML"
assert_only_package_xml_staged
git commit -m "Release stamp ${stamp_tag}"

git tag -a "$stamp_tag" -m "Release ${stamp_tag}"

next_y=$((y + 1))
reset_version="${x}.${next_y}"
write_manifest_version_date "$reset_version" "$(today_utc)"
git add "$PACKAGE_XML"
assert_only_package_xml_staged
git commit -m "Begin development line ${reset_version} after ${stamp_tag}"

git push origin "$stamp_tag"
git push origin HEAD:main

if [[ -n "${GITHUB_OUTPUT:-}" ]]; then
  printf 'release_tag=%s\n' "$stamp_tag" >> "$GITHUB_OUTPUT"
fi

echo "Published ${stamp_tag}; main reset to ${reset_version}"