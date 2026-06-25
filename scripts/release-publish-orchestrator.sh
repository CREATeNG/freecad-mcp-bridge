#!/usr/bin/env bash
# Release publish step — INTERNAL to .github/workflows/release.yml only.
#
# Runs only after install-verify has passed on main (bin/ already synced on the verified
# commit). Creates a GitHub Release with attachments copied from bin/ (--target). gh
# uploads attachments to a draft release, then publishes; the git tag is created when
# the release is published (after attachments attach).
#
# Does not bump package.xml (a later publish-job step handles that). No standalone entry point.

set -euo pipefail

if [[ "${RELEASE_PUBLISH_AUTHORIZED:-}" != "true" ]]; then
  echo "release-publish-orchestrator.sh is internal to release.yml (Release Orchestrator)." >&2
  exit 2
fi

if [[ -z "${GH_TOKEN:-}" ]]; then
  echo "GH_TOKEN is required for gh release create." >&2
  exit 1
fi

VERIFIED_SHA="${RELEASE_VERIFIED_SHA:?RELEASE_VERIFIED_SHA is required}"
RELEASE_TAG="${RELEASE_TAG:?RELEASE_TAG is required}"
PACKAGE_XML="${PACKAGE_XML:-package.xml}"

require_clean_tree() {
  if [[ -n "$(git status --porcelain)" ]]; then
    echo "Working tree must be clean." >&2
    git status --porcelain >&2
    exit 1
  fi
}

read_package_version() {
  sed -n 's/.*<version>\([^<]*\)<\/version>.*/\1/p' "$PACKAGE_XML" | head -1
}

require_clean_tree

current=$(git rev-parse HEAD)
if [[ "$current" != "$VERIFIED_SHA" ]]; then
  echo "HEAD (${current}) must match verified release candidate (${VERIFIED_SHA})." >&2
  exit 1
fi

version=$(read_package_version)
if [[ -z "$version" ]]; then
  echo "Missing <version> in ${PACKAGE_XML}" >&2
  exit 1
fi
if [[ ! "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  echo "package.xml must use x.y.z before publish (found: ${version})" >&2
  exit 1
fi
if [[ "$RELEASE_TAG" != "v${version}" ]]; then
  echo "RELEASE_TAG (${RELEASE_TAG}) must match package.xml version (v${version})." >&2
  exit 1
fi

if git rev-parse "$RELEASE_TAG" >/dev/null 2>&1; then
  echo "Tag ${RELEASE_TAG} already exists locally." >&2
  exit 1
fi
if [[ -n "$(git ls-remote --tags origin "${RELEASE_TAG}")" ]]; then
  echo "Tag ${RELEASE_TAG} already exists on origin." >&2
  exit 1
fi

for path in \
  bin/linux/freecad-mcp-bridge \
  bin/macos/freecad-mcp-bridge \
  bin/win32/freecad-mcp-bridge.exe
do
  if [[ ! -f "$path" ]]; then
    echo "Missing bin/ binary for release attachments: ${path}" >&2
    exit 1
  fi
done

asset_dir=$(mktemp -d)
trap 'rm -rf "$asset_dir"' EXIT
cp bin/linux/freecad-mcp-bridge "$asset_dir/freecad-mcp-bridge-linux"
cp bin/macos/freecad-mcp-bridge "$asset_dir/freecad-mcp-bridge-macos"
cp bin/win32/freecad-mcp-bridge.exe "$asset_dir/freecad-mcp-bridge-windows.exe"

gh release create "$RELEASE_TAG" \
  --target "$VERIFIED_SHA" \
  --title "$RELEASE_TAG" \
  --notes "Release $RELEASE_TAG" \
  "$asset_dir/freecad-mcp-bridge-linux" \
  "$asset_dir/freecad-mcp-bridge-macos" \
  "$asset_dir/freecad-mcp-bridge-windows.exe"

echo "Published GitHub Release ${RELEASE_TAG} with assets; tag created at ${VERIFIED_SHA}"

if [[ -n "${GITHUB_OUTPUT:-}" ]]; then
  printf 'release_tag=%s\n' "$RELEASE_TAG" >> "$GITHUB_OUTPUT"
fi