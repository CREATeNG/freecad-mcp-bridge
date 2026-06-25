#!/usr/bin/env bash
# Release publish orchestrator — INTERNAL to .github/workflows/release.yml only.
#
# Runs only after install-verify has passed. Atomic responsibilities:
#   A. Create and push the release tag on the verified commit (tag === package.xml version)
#   B. Bump package.xml z on main for the next development cycle
#
# Does not create GitHub Releases (workflow steps handle that). No standalone entry point.

set -euo pipefail

if [[ "${RELEASE_PUBLISH_AUTHORIZED:-}" != "true" ]]; then
  echo "release-publish-orchestrator.sh is internal to release.yml (Release Orchestrator)." >&2
  exit 2
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

git tag -a "$RELEASE_TAG" -m "Release ${RELEASE_TAG}"
git push origin "$RELEASE_TAG"

echo "Created and pushed tag ${RELEASE_TAG} at ${VERIFIED_SHA}"

git switch main
git pull --ff-only origin main

export BUMP_PACKAGE_Z_AUTHORIZED=true
bash "$(dirname "${BASH_SOURCE[0]}")/bump-package-z.sh"

if [[ -n "${GITHUB_OUTPUT:-}" ]]; then
  printf 'release_tag=%s\n' "$RELEASE_TAG" >> "$GITHUB_OUTPUT"
fi

echo "Publish orchestrator complete for ${RELEASE_TAG}"