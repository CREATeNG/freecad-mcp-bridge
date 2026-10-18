#!/usr/bin/env bash
# Write the next patch into package.xml, the shim's version fields,
# and a new CHANGELOG.md section. Run it on a branch. Fill in the
# notes, commit those files, and open a pull request.
# This script does not commit or push.

set -euo pipefail

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
today=$(date +%Y-%m-%d)  # the machine's local date

python3 - <<'PY' "$version" "$next_version" "$today" "$PACKAGE_XML" "$SHIM_MANIFEST" "$SHIM_PACKAGE_JSON"
import re
import sys
from pathlib import Path

current, version, date, package_xml, shim_manifest, shim_package_json = sys.argv[1:7]
changelog = "CHANGELOG.md"


def read_text(path):
    """The file's text, and the newline it already uses."""
    raw = Path(path).read_bytes()
    newline = "\r\n" if b"\r\n" in raw else "\n"
    text = raw.decode("utf-8")
    if newline != "\n":
        text = text.replace("\r\n", "\n")
    return text, newline


def write_text(path, text, newline):
    """Write text back with the newline the file already used."""
    if newline != "\n":
        text = text.replace("\n", "\r\n")
    Path(path).write_bytes(text.encode("utf-8"))


path = Path(package_xml)
text, newline = read_text(path)
text, n1 = re.subn(
    r"(<version>)[^<]*(</version>)", rf"\g<1>{version}\g<2>", text, count=1
)
text, n2 = re.subn(
    r"(<date>)[^<]*(</date>)", rf"\g<1>{date}\g<2>", text, count=1
)
if n1 != 1 or n2 != 1:
    raise SystemExit("Failed to update version/date in package.xml")
write_text(path, text, newline)

for json_path in (shim_manifest, shim_package_json):
    p = Path(json_path)
    text, newline = read_text(p)
    text, n = re.subn(
        r'("version":\s*")[^"]*(")', rf"\g<1>{version}\g<2>", text, count=1
    )
    if n != 1:
        raise SystemExit(f"Failed to update version in {json_path}")
    write_text(p, text, newline)

path = Path(changelog)
if not path.is_file():
    raise SystemExit(f"Missing {changelog}")
text, newline = read_text(path)
credit = next(
    (line for line in text.splitlines() if line.startswith("Built ")),
    "",
)
if not credit:
    raise SystemExit(f"No Built credit line in {changelog}")
placeholder = (
    f"<!-- Write the release notes for {version} as bullet points "
    "here and delete this comment. -->"
)
stub = (
    f"## {version}\n"
    "\n"
    f"**What's new since {current}**\n"
    "\n"
    f"{placeholder}\n"
    "\n"
    f"{credit}\n"
    "\n"
)
write_text(path, stub + text, newline)
PY

echo "Wrote ${next_version} into package.xml, the shim manifest, the shim package.json, and CHANGELOG.md."
echo "Replace the placeholder in CHANGELOG.md, then commit those files and open a pull request."
echo "This script does not commit or push."