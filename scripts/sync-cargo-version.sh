#!/usr/bin/env bash
# Set rust_mcp_server/Cargo.toml [package].version — INTERNAL to bump scripts.

set -euo pipefail

VERSION="${1:?version argument required}"
CARGO_TOML="${CARGO_TOML:-rust_mcp_server/Cargo.toml}"

if [[ ! "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  echo "Version must be x.y.z (found: ${VERSION})" >&2
  exit 1
fi

python3 - <<'PY' "$VERSION" "$CARGO_TOML"
import re
import sys
from pathlib import Path

version, cargo_toml = sys.argv[1], sys.argv[2]
path = Path(cargo_toml)
text = path.read_text(encoding="utf-8")
text, n = re.subn(
    r'^(version\s*=\s*")[^"]*(")',
    rf'\g<1>{version}\2',
    text,
    count=1,
    flags=re.MULTILINE,
)
if n != 1:
    raise SystemExit(f"Failed to update version in {cargo_toml}")
path.write_text(text, encoding="utf-8")
PY

echo "Synced ${CARGO_TOML} version to ${VERSION}"