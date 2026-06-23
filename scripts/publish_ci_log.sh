#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -lt 2 ]; then
  echo "Usage: publish_ci_log.sh <log-file> <group-title>" >&2
  exit 2
fi

log_file="$1"
group_title="$2"

escape_github_message() {
  local value="$1"
  value="${value//'%'/'%25'}"
  value="${value//$'\r'/'%0D'}"
  value="${value//$'\n'/'%0A'}"
  printf '%s' "$value"
}

if [ ! -f "$log_file" ]; then
  echo "::error title=CI log missing::No CI test log at ${log_file}"
  exit 1
fi

echo "::group::${group_title}"
failed=0
while IFS= read -r line || [ -n "$line" ]; do
  echo "$line"
  case "$line" in
    PASS\ *)
      msg="$(escape_github_message "${line#PASS }")"
      echo "::notice title=${group_title}::${msg}"
      ;;
    FAIL\ *)
      msg="$(escape_github_message "${line#FAIL }")"
      echo "::error title=${group_title}::${msg}"
      failed=1
      ;;
  esac
done < "$log_file"
echo "::endgroup::"

exit "$failed"