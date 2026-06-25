#!/usr/bin/env bash
# Dispatch Index PR workflow on CREATeNG/FreeCAD-Addons — INTERNAL to release.yml.
#
# Requires ADDONS_INDEX_DISPATCH_TOKEN (Actions: write on CREATeNG/FreeCAD-Addons).
# Waits for the fork workflow, then updates this repo's GitHub Release notes.

set -euo pipefail

if [[ "${ADDONS_INDEX_DISPATCH_AUTHORIZED:-}" != "true" ]]; then
  echo "trigger-addons-index-dispatch.sh is internal to release.yml (Release Orchestrator)." >&2
  exit 2
fi

RELEASE_TAG="${RELEASE_TAG:?RELEASE_TAG is required}"
RELEASE_VERSION="${RELEASE_VERSION:-${RELEASE_TAG#v}}"
ADDONS_INDEX_FORK_REPO="${ADDONS_INDEX_FORK_REPO:-CREATeNG/FreeCAD-Addons}"
ADDONS_INDEX_UPSTREAM_REPO="${ADDONS_INDEX_UPSTREAM_REPO:-FreeCAD/Addons}"
ADDONS_INDEX_ENTRY_ID="${ADDONS_INDEX_ENTRY_ID:-freecad-mcp-bridge}"
INDEX_WORKFLOW_FILE="${INDEX_WORKFLOW_FILE:-index-release.yml}"
BRANCH="index/${ADDONS_INDEX_ENTRY_ID}-${RELEASE_TAG}"
WORK_ROOT="${RUNNER_TEMP:-/tmp}/addons-index-dispatch"
NOTES_FILE="${WORK_ROOT}/release-notes.md"
POLL_ATTEMPTS="${POLL_ATTEMPTS:-30}"
POLL_SECONDS="${POLL_SECONDS:-20}"

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

find_open_pr() {
  local token="${1:-$RELEASE_GH_TOKEN}"
  GH_TOKEN="$token" gh pr list \
    --repo "$ADDONS_INDEX_UPSTREAM_REPO" \
    --head "${ADDONS_INDEX_FORK_REPO%%/*}:${BRANCH}" \
    --state open \
    --json url \
    --jq '.[0].url // empty'
}

open_upstream_pr() {
  local pr_body
  pr_body="$(cat <<EOF
Automated Index update from [CREATeNG/freecad-mcp-bridge](https://github.com/CREATeNG/freecad-mcp-bridge) release \`${RELEASE_TAG}\`.

- Entry: \`${ADDONS_INDEX_ENTRY_ID}\`
- Updates \`git_ref\`, \`branch_display_name\`, and \`zip_url\`

FreeCAD Addon Index maintainers review and merge.
EOF
)"
  GH_TOKEN="$ADDONS_INDEX_DISPATCH_TOKEN" gh pr create \
    --repo "$ADDONS_INDEX_UPSTREAM_REPO" \
    --head "${ADDONS_INDEX_FORK_REPO%%/*}:${BRANCH}" \
    --base main \
    --title "Index: ${ADDONS_INDEX_ENTRY_ID} ${RELEASE_TAG}" \
    --body "$pr_body"
}

if [[ -z "${ADDONS_INDEX_DISPATCH_TOKEN:-}" ]]; then
  write_release_notes "skipped" \
    "No automated Index PR — \`ADDONS_INDEX_DISPATCH_TOKEN\` is not configured on this repository."
  set_output index_pr_status skipped
  set_output index_pr_url ""
  exit 0
fi

existing_pr="$(find_open_pr "$RELEASE_GH_TOKEN" || true)"
if [[ -n "$existing_pr" ]]; then
  write_release_notes "existing" \
    "Index update PR already open: ${existing_pr}"
  set_output index_pr_status existing
  set_output index_pr_url "$existing_pr"
  exit 0
fi

GH_TOKEN="$ADDONS_INDEX_DISPATCH_TOKEN" gh workflow run "$INDEX_WORKFLOW_FILE" \
  --repo "$ADDONS_INDEX_FORK_REPO" \
  -f "tag=${RELEASE_TAG}" \
  -f "version=${RELEASE_VERSION}"

echo "Triggered ${INDEX_WORKFLOW_FILE} on ${ADDONS_INDEX_FORK_REPO} for ${RELEASE_TAG}"
sleep 5

run_id=""
for ((attempt = 1; attempt <= POLL_ATTEMPTS; attempt++)); do
  run_id="$(
    GH_TOKEN="$ADDONS_INDEX_DISPATCH_TOKEN" gh run list \
      --repo "$ADDONS_INDEX_FORK_REPO" \
      --workflow "$INDEX_WORKFLOW_FILE" \
      --limit 1 \
      --json databaseId,createdAt \
      --jq '.[0].databaseId // empty'
  )"
  if [[ -n "$run_id" ]]; then
    break
  fi
  sleep "$POLL_SECONDS"
done

if [[ -z "$run_id" ]]; then
  write_release_notes "pending" \
    "Dispatched Index workflow on [\`${ADDONS_INDEX_FORK_REPO}\`](https://github.com/${ADDONS_INDEX_FORK_REPO}/actions) — run not found yet; check Actions on the fork."
  set_output index_pr_status pending
  set_output index_pr_url ""
  exit 0
fi

fork_ok=true
if ! GH_TOKEN="$ADDONS_INDEX_DISPATCH_TOKEN" gh run watch "$run_id" --repo "$ADDONS_INDEX_FORK_REPO" --exit-status; then
  fork_ok=false
  echo "Fork index workflow failed (run ${run_id}); will still try opening upstream PR if branch exists." >&2
fi

pr_url="$(find_open_pr "$RELEASE_GH_TOKEN" || true)"
if [[ -n "$pr_url" ]]; then
  write_release_notes "existing" \
    "Index update PR already open: ${pr_url}"
  set_output index_pr_status existing
  set_output index_pr_url "$pr_url"
  exit 0
fi

encoded_branch="${BRANCH//\//%2F}"
if [[ "$fork_ok" == "true" ]] || GH_TOKEN="$ADDONS_INDEX_DISPATCH_TOKEN" gh api \
  "repos/${ADDONS_INDEX_FORK_REPO}/git/ref/heads/${encoded_branch}" >/dev/null 2>&1; then
  if pr_url="$(open_upstream_pr 2>/dev/null || true)" && [[ -n "$pr_url" ]]; then
    write_release_notes "opened" \
      "Opened Index update PR — FreeCAD Addon Index maintainers review and merge: ${pr_url}"
    set_output index_pr_status opened
    set_output index_pr_url "$pr_url"
    exit 0
  fi
fi

if [[ "$fork_ok" != "true" ]]; then
  write_release_notes "failed" \
    "Index branch workflow failed on [\`${ADDONS_INDEX_FORK_REPO}\`](https://github.com/${ADDONS_INDEX_FORK_REPO}/actions/runs/${run_id}); upstream PR not opened."
  set_output index_pr_status failed
  set_output index_pr_url ""
  exit 1
fi

write_release_notes "unchanged" \
  "Index entry already lists \`${RELEASE_TAG}\`; no upstream PR opened. Fork run: https://github.com/${ADDONS_INDEX_FORK_REPO}/actions/runs/${run_id}"
set_output index_pr_status unchanged
set_output index_pr_url ""