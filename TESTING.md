# Automated install verification (CI)

This document describes the GitHub Actions workflow that installs a tagged release through FreeCAD's Addon Manager, restarts FreeCAD, and verifies that the addon auto-initializes and the bridge works end-to-end over HTTP.

For release tagging, Index updates, and workflows, see [MAINTAINING.md](MAINTAINING.md).

---

## Overview

The **install-verify workflow** ([`install-verify.yml`](.github/workflows/install-verify.yml)) runs on three platforms in parallel:

| Platform | FreeCAD install | Display / GUI |
|----------|-----------------|---------------|
| Linux | conda-forge `freecad=1.1.0` | `xvfb-run` |
| macOS | conda-forge `freecad=1.1.0` | `QT_QPA_PLATFORM=offscreen` |
| Windows | `winget install FreeCAD.FreeCAD --version 1.1.0` | native (Git Bash launcher) |

Each matrix job launches **two separate FreeCAD processes**:

1. **Install** — `scripts/test_install.py` via `scripts/ci_run_freecad.sh install`
2. **Verify** — `scripts/test_verify.py` via `scripts/ci_run_freecad.sh verify` (fresh process, same isolated profile)

Shared helpers live in `scripts/test_install_common.py`.

---

## Workflow triggers

| Trigger | Tag / mode | Typical use |
|---------|------------|-------------|
| `workflow_call` from **`release.yml`** | `install_mode: main` or `tag`; `install_tag` from prepare | Pre-tag gate (`main`) and post-tag sanity check (`tag` path) |
| `workflow_dispatch` | Inputs: `tag` (e.g. `v0.1.11`), `mode` (`tag`, `index_zip`, or `main`) | Ad-hoc CI run; iterate on scripts |

Environment variables set by the workflow (overridable locally when debugging) fall into three groups. Exact names and defaults live in [`install-verify.yml`](.github/workflows/install-verify.yml) and each script's own module docstring (`test_install.py`, `test_verify.py`) — not repeated here to avoid the two copies drifting apart:

- **Install source** — which tag/repo/mode/Mod-folder-name to install.
- **Timing** — how long to wait for GUI startup, toolbar injection, the HTTP exec round-trip, and overall install completion.
- **Expected strings** (verify phase only) — the probe code sent over HTTP, the expected output substring, and the expected Report-view message after stopping the bridge.

---

## Install modes

### `tag` (default)

Addon Manager installs from the Git repository URL with `branch` set to the release tag. This matches how users add a custom repository and pick a tag in Preferences.

### `index_zip`

Addon Manager installs from the GitHub archive ZIP URL:

`{REPO}/archive/refs/tags/{TAG}.zip`

Use this mode to validate the same artifact shape the FreeCAD Addon Index serves via `zip_url`. After install, `flatten_install_dir()` still normalizes nested zip layouts to `Mod/freecad-mcp-bridge/`.

### `main`

Addon Manager installs from the repository URL with `branch=main`. Used by the **`release.yml` verify gate**: after `bin/` is pushed to `main` but **before** the release tag is created, install-verify runs against the release candidate on `main`. `RELEASE_INSTALL_TAG` still supplies the expected `package.xml` version for on-disk checks.

---

## `release.yml` and install-verify

**`release.yml`** calls **`install-verify.yml`** twice:

1. **Pre-tag** (`install_mode: main`) — hard gate before tag creation. Fails **`release.yml`** if any OS fails.
2. **Tag path** (`install_mode: tag`) — after tag and release notes; final sanity check that install works from the tag ref. Fails **`release.yml`** before the Index PR dispatch and patch bump if any OS fails.

Full **`release.yml`** job order (build, publish, Index dispatch on [`CREATeNG/FreeCAD-Addons`](https://github.com/CREATeNG/FreeCAD-Addons), bump): see [MAINTAINING.md — `release.yml` job order](MAINTAINING.md#the-release-orchestrator-workflow-releaseyml).

---

## Phase 1: `test_install.py`

Run inside the first FreeCAD GUI process.

**Steps:**

1. Locate Addon Manager (`Addon` + `AddonInstaller` from user or installation Mod paths).
2. Remove any stale `freecad-mcp-bridge` (or `*mcp-bridge*`) install under `Mod/`.
3. Install via Addon Manager (`InstallationMethod.ANY`) using `build_addon_descriptor()` for the chosen mode.
4. Poll until `package.xml` with the expected version appears (handles nested GitHub zip directories).
5. **Flatten** nested install paths to `Mod/freecad-mcp-bridge/` so FreeCAD autoloads the addon on restart.
6. **Verify on-disk tree** — confirms `package.xml`'s version matches the tag and the addon's core Python files are present. Exact required-file list lives in `verify_install_tree()` in [`test_install_common.py`](scripts/test_install_common.py).

On success, the script exits via `quit_freecad(0)` and CI launches verify in a new FreeCAD process.

---

## Phase 2: `test_verify.py`

Run inside the **second** FreeCAD process after restart. Does **not** call `App.MCPBridgeInjectUi()` — it relies on the addon's own `init_gui.py` startup hooks.

**Checklist:**

| Step | Proves |
|------|--------|
| Auto-registration | The addon's command registers on startup with no manual injection — `init_gui.py`'s own startup hooks ran |
| Install dir | The installed addon directory exists |
| Toolbar injection | The **MCP Bridge On/Off** toolbar action appears with no manual injection |
| Start bridge | First toolbar click actually starts the HTTP server |
| HTTP round-trip | A client-shaped request — `tools/call` → `execute_python` over HTTP, sent from a worker thread while the main thread pumps the Qt event loop, paginating via `get_output` if the response is chunked — returns the expected probe output. Same code path a real MCP client uses. |
| Stop bridge | Second toolbar click actually stops the HTTP server |
| Report view | Shows the expected "stopped" message (`RELEASE_INSTALL_STOP_MESSAGE`, defaults to the bridge's own stop log line) |
| Status bar | Shows `MCP Bridge: Offline` |

On success, exits with `quit_freecad(0)`.

---

## Launcher: `ci_run_freecad.sh`

Unified bash entry point for all platforms:

```bash
bash scripts/ci_run_freecad.sh install   # phase 1
bash scripts/ci_run_freecad.sh verify    # phase 2
```

**Responsibilities:**

- Resolve FreeCAD binary (conda `PATH` on Linux/macOS; common install paths after winget on Windows).
- Optionally isolate profile via `ISOLATE_HOME=true` → `HOME` / `USERPROFILE` = `FC_CI_HOME`.
- Set `RELEASE_INSTALL_CI_LOG` to a phase-specific file under `RUNNER_TEMP`.
- Run FreeCAD with the test script under a **15-minute** timeout (`xvfb-run` on Linux, `perl alarm` on macOS, `timeout` on Windows when available).
- Invoke `publish_ci_log.sh` after FreeCAD exits.

---

## CI log file and GitHub annotations

FreeCAD's Python stdout is unreliable for GitHub workflow commands on Unix runners. Tests therefore write structured lines to a **file**, and the shell publishes them after exit.

### Log file locations

| Phase | Path |
|-------|------|
| Install | `${RUNNER_TEMP}/freecad-ci-install.log` |
| Verify | `${RUNNER_TEMP}/freecad-ci-verify.log` |

Set explicitly via `RELEASE_INSTALL_CI_LOG` (done automatically by `ci_run_freecad.sh`).

### Line format

Tests append lines via `test_install_common._write_ci_log()`:

| Prefix | Meaning | GitHub mapping (`publish_ci_log.sh`) |
|--------|---------|--------------------------------------|
| `GROUP` | Phase section markers (`=== title ===`) | Echoed inside `::group::` |
| `INFO` | Informational | Echoed as plain log lines |
| `PASS` | Checkpoint passed | `::notice::` annotation |
| `FAIL` | Assertion failed | `::error::` annotation; step fails |

`publish_ci_log.sh` reads the log file, prints a collapsible `::group::` block with all lines, emits notices/errors for `PASS`/`FAIL` lines, and exits non-zero if any `FAIL` line was seen.

**Do not** emit `::notice::` / `::error::` from inside FreeCAD Python — use the file + shell wrapper instead.

---

## Process exit: `quit_freecad()`

CI test scripts call `os._exit(code)` unconditionally after a short Qt event drain. This avoids flaky GUI teardown on headless runners (segfaults, access violations, multi-minute hangs). There is no opt-out environment variable.

---

## Running locally

Prerequisites: FreeCAD 1.1 with GUI, Git Bash (Windows) or bash (Unix).

```bash
# Optional: isolated profile (mirrors CI on Linux/Windows)
export FC_CI_HOME=/tmp/freecad-ci-home
export ISOLATE_HOME=true
mkdir -p "$FC_CI_HOME"

export RELEASE_INSTALL_TAG=v0.1.11
export RELEASE_INSTALL_MODE=tag
export RELEASE_INSTALL_REPO=https://github.com/CREATeNG/freecad-mcp-bridge

# Phase 1 — install (replace with your FreeCAD binary)
freecad scripts/test_install.py

# Phase 2 — verify (new FreeCAD process, same profile if isolated)
freecad scripts/test_verify.py
```

To preview GitHub-style output from a captured log:

```bash
bash scripts/publish_ci_log.sh /tmp/freecad-ci-verify.log "Verify addon after restart"
```

---

## Related files

| File | Role |
|------|------|
| `.github/workflows/install-verify.yml` | Pre-tag and tag-path checks (`workflow_call` from `release.yml`), or `workflow_dispatch` |
| `scripts/ci_run_freecad.sh` | FreeCAD launcher + timeout + log path |
| `scripts/publish_ci_log.sh` | Log file → GitHub annotations |
| `scripts/test_install.py` | Addon Manager install phase |
| `scripts/test_verify.py` | Post-restart UI + socket phase |
| `scripts/test_install_common.py` | Shared helpers, CI log, `quit_freecad()` |