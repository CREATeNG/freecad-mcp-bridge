#!/usr/bin/env bash
# Open (or update) a FreeCAD Addon Index PR from CREATeNG/FreeCAD-Addons — INTERNAL to release.yml.
#
# Requires ADDONS_INDEX_PR_TOKEN (push to fork + open PR against FreeCAD/Addons).
# Updates the GitHub Release notes on this repo with the PR link or skip reason.

set -euo pipefail

if [[ "${ADDONS_INDEX_PR_AUTHORIZED:-}" != "true" ]]; then
  echo "open-addons-index-pr.sh is internal to release.yml (Release Orchestrator)." >&2
  exit 2
fi

RELEASE_TAG="${RELEASE_TAG:?RELEASE_TAG is required}"
RELEASE_VERSION="${RELEASE_VERSION:-${RELEASE_TAG#v}}"
ADDONS_INDEX_FORK_REPO="${ADDONS_INDEX_FORK_REPO:-CREATeNG/FreeCAD-Addons}"
ADDONS_INDEX_UPSTREAM_REPO="${ADDONS_INDEX_UPSTREAM_REPO:-FreeCAD/Addons}"
ADDONS_INDEX_UPSTREAM_BRANCH="${ADDONS_INDEX_UPSTREAM_BRANCH:-main}"
ADDONS_INDEX_ENTRY_ID="${ADDONS_INDEX_ENTRY_ID:-freecad-mcp-bridge}"
ADDON_REPO_URL="${ADDON_REPO_URL:-https://github.com/CREATeNG/freecad-mcp-bridge}"
ADDONS_INDEX_ALLOW_ADD="${ADDONS_INDEX_ALLOW_ADD:-true}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORK_ROOT="${RUNNER_TEMP:-/tmp}/addons-index-pr"
FORK_DIR="${WORK_ROOT}/fork"
BRANCH="index/${ADDONS_INDEX_ENTRY_ID}-${RELEASE_TAG}"
NOTES_FILE="${WORK_ROOT}/release-notes.md"

RELEASE_GH_TOKEN="${GH_TOKEN:-}"

write_release_notes() {
  local status="$1"
  local detail="$2"

  mkdir -p "$WORK_ROOT"
  {
    printf '## %s\n\n' "$RELEASE_TAG"
    printf 'Tagged release with synced `bin/` clients (see repository tag zip).\n\n'
    printf '### FreeCAD Addon Index\n\n'
    printf '%s\n' "$detail"
  } >"$NOTES_FILE"

  if [[ -z "${RELEASE_GH_TOKEN}" ]]; then
    echo "GH_TOKEN not set; skipping gh release edit." >&2
    cat "$NOTES_FILE"
    return 0
  fi

  if GH_TOKEN="$RELEASE_GH_TOKEN" gh release view "$RELEASE_TAG" >/dev/null 2>&1; then
    GH_TOKEN="$RELEASE_GH_TOKEN" gh release edit "$RELEASE_TAG" --notes-file "$NOTES_FILE"
    echo "Updated GitHub Release notes for ${RELEASE_TAG} (${status})."
  else
    echo "Release ${RELEASE_TAG} not found; notes prepared but not published:" >&2
    cat "$NOTES_FILE"
  fi
}

set_output() {
  if [[ -n "${GITHUB_OUTPUT:-}" ]]; then
    printf '%s=%s\n' "$1" "$2" >>"$GITHUB_OUTPUT"
  fi
}

if [[ -z "${ADDONS_INDEX_PR_TOKEN:-}" ]]; then
  write_release_notes "skipped" \
    "No automated Index PR — \`ADDONS_INDEX_PR_TOKEN\` is not configured on this repository."
  set_output index_pr_status skipped
  set_output index_pr_url ""
  exit 0
fi

export GH_TOKEN="$ADDONS_INDEX_PR_TOKEN"

rm -rf "$FORK_DIR"
mkdir -p "$WORK_ROOT"
git clone "https://x-access-token:${ADDONS_INDEX_PR_TOKEN}@github.com/${ADDONS_INDEX_FORK_REPO}.git" "$FORK_DIR"

cd "$FORK_DIR"
git config user.name "github-actions[bot]"
git config user.email "41898282+github-actions[bot]@users.noreply.github.com"

if ! git remote get-url upstream >/dev/null 2>&1; then
  git remote add upstream "https://github.com/${ADDONS_INDEX_UPSTREAM_REPO}.git"
fi

git fetch upstream "$ADDONS_INDEX_UPSTREAM_BRANCH"
git checkout -B "$ADDONS_INDEX_UPSTREAM_BRANCH" "upstream/${ADDONS_INDEX_UPSTREAM_BRANCH}"

INDEX_PATH="Data/Index.json"
if [[ ! -f "$INDEX_PATH" ]]; then
  echo "Missing ${INDEX_PATH} in ${ADDONS_INDEX_UPSTREAM_REPO}." >&2
  exit 1
fi

before_hash=$(sha256sum "$INDEX_PATH" | awk '{print $1}')
python3 "${SCRIPT_DIR}/patch-addons-index.py" \
  "$INDEX_PATH" \
  "$ADDONS_INDEX_ENTRY_ID" \
  "$RELEASE_TAG" \
  "$ADDON_REPO_URL" \
  "$ADDONS_INDEX_ALLOW_ADD"
after_hash=$(sha256sum "$INDEX_PATH" | awk '{print $1}')

if [[ "$before_hash" == "$after_hash" ]]; then
  existing_pr="$(
    gh pr list \
      --repo "$ADDONS_INDEX_UPSTREAM_REPO" \
      --head "${ADDONS_INDEX_FORK_REPO%%/*}:${BRANCH}" \
      --state open \
      --json url \
      --jq '.[0].url // empty'
  )"
  if [[ -n "$existing_pr" ]]; then
    write_release_notes "existing" \
      "Index entry already at \`${RELEASE_TAG}\`. Open PR: ${existing_pr}"
    set_output index_pr_status existing
    set_output index_pr_url "$existing_pr"
    exit 0
  fi

  write_release_notes "unchanged" \
    "Index entry already lists \`${RELEASE_TAG}\`; no new PR opened."
  set_output index_pr_status unchanged
  set_output index_pr_url ""
  exit 0
fi

git checkout -B "$BRANCH"
git add "$INDEX_PATH"
git commit -m "Index: ${ADDONS_INDEX_ENTRY_ID} ${RELEASE_TAG}"

if git ls-remote --heads origin "$BRANCH" | grep -q .; then
  git push --force-with-lease origin "$BRANCH"
else
  git push -u origin "$BRANCH"
fi

pr_body="$(cat <<EOF
Automated Index update from [CREATeNG/freecad-mcp-bridge](${ADDON_REPO_URL}) release \`${RELEASE_TAG}\`.

- Entry: \`${ADDONS_INDEX_ENTRY_ID}\`
- Updates \`git_ref\`, \`branch_display_name\`, and \`zip_url\`

FreeCAD Addon Index maintainers review and merge.
EOF
)"

pr_url="$(
  gh pr create \
    --repo "$ADDONS_INDEX_UPSTREAM_REPO" \
    --head "${ADDONS_INDEX_FORK_REPO%%/*}:${BRANCH}" \
    --base "$ADDONS_INDEX_UPSTREAM_BRANCH" \
    --title "Index: ${ADDONS_INDEX_ENTRY_ID} ${RELEASE_TAG}" \
    --body "$pr_body"
)"

write_release_notes "opened" \
  "Opened Index update PR (human review required): ${pr_url}"

set_output index_pr_status opened
set_output index_pr_url "$pr_url"

echo "Opened FreeCAD Addon Index PR: ${pr_url}"