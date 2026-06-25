#!/usr/bin/env bash
set -euo pipefail

VERSION="${1:?Usage: index-pr-fields.sh <version> (e.g. 0.1.11)}"
TAG="v${VERSION}"
REPO="https://github.com/CREATeNG/freecad-mcp-bridge"

cat <<EOF
FreeCAD Addons Index PR fields for ${TAG}

In https://github.com/FreeCAD/Addons Data/Index.json, update the
freecad-mcp-bridge entry:

  "git_ref": "${TAG}",
  "branch_display_name": "${TAG}",
  "zip_url": "${REPO}/archive/refs/tags/${TAG}.zip"

Then open a PR against https://github.com/FreeCAD/Addons
EOF