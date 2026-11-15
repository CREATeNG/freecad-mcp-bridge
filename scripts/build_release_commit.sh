#!/usr/bin/env bash
# Build the release commit: a commit whose tree holds only what users
# install, taken from a verified main commit, with the previous release
# commit as its parent, so the release branch moves forward along its own
# line. Used by release-publish-orchestrator.sh, and by hand for a dry
# run.
#
# Usage: build_release_commit.sh <main-commit> <parent-commit> <version>
#
# Prints the new commit's SHA. Run from the repository root. It creates
# git objects only: it changes no branch, no tag, the working tree and
# the index.

set -euo pipefail

SOURCE="${1:?main commit required}"
PARENT="${2:?parent commit required}"
VERSION="${3:?version required}"

# What users install, exactly these paths and no others:
#   package.xml       the Addon Manager's metadata
#   LICENSE           the licence file package.xml names
#   README.md         the Addon Manager shows it (package.xml's readme url)
#   SECURITY.md       linked from the README
#   USER_GUIDE.md     linked from the README
#   CHANGELOG.md      the release notes
#   Resources         package.xml's icon, and the icons init_gui adds
#   freecad           the addon
#   mcp-stdio-shim    the Claude Desktop extension's source
# Tests, scripts, workflows, design documents, send_cmd.py and the
# developer guides stay on main.
SHIPPED=(
  package.xml
  LICENSE
  README.md
  SECURITY.md
  USER_GUIDE.md
  CHANGELOG.md
  Resources
  freecad
  mcp-stdio-shim
)

for path in "${SHIPPED[@]}"; do
  if ! git cat-file -e "${SOURCE}:${path}" 2>/dev/null; then
    echo "${SOURCE} has no ${path}; the release commit needs it." >&2
    exit 1
  fi
done

scratch="$(mktemp -d)"
trap 'rm -rf "${scratch}"' EXIT
export GIT_INDEX_FILE="${scratch}/index"
git read-tree --empty
git ls-tree -r "${SOURCE}" -- "${SHIPPED[@]}" | git update-index --index-info
tree="$(git write-tree)"

git commit-tree "${tree}" -p "${PARENT}" \
  -m "Release v${VERSION}" \
  -m "Built from main at ${SOURCE}. This commit holds only what users install; main also holds the tests, scripts, workflows and design documents."
