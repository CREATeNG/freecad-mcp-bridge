# Automated install verification (CI)

This document describes the GitHub Actions workflow that installs a tagged release through FreeCAD's Addon Manager, restarts FreeCAD, and verifies that the addon auto-initializes and the local socket bridge works end-to-end.

For release tagging, Index updates, and maintainer terms, see [MAINTAINING.md](MAINTAINING.md).

---

## Overview

The **install-verify workflow** ([`release-install-verify.yml`](.github/workflows/release-install-verify.yml)) runs on three platforms in parallel:

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
| `workflow_dispatch` | Inputs: `tag` (e.g. `v0.1.11`), `mode` (`tag` or `index_zip`) | Test CI against a tag before or after release; iterate on scripts on `main` |
| `push` of `v*` tag | Tag = pushed ref; mode = `tag` | Automatic check after **`release.yml`** creates a tag |

Environment variables set by the workflow (overridable locally when debugging):

| Variable | Default (workflow) | Purpose |
|----------|----------------------|---------|
| `RELEASE_INSTALL_TAG` | dispatch input or `github.ref_name` | Git tag to install |
| `RELEASE_INSTALL_MODE` | `tag` (or dispatch `index_zip`) | Addon Manager install source |
| `RELEASE_INSTALL_REPO` | `https://github.com/CREATeNG/freecad-mcp-bridge` | Repository URL |
| `RELEASE_INSTALL_NAME` | `freecad-mcp-bridge` | Installed Mod folder name |
| `RELEASE_INSTALL_GUI_DELAY_MS` | `5000` | Delay before test logic starts |
| `RELEASE_INSTALL_TOOLBAR_TIMEOUT_MS` | `30000` | Wait for auto-injected toolbar |
| `RELEASE_INSTALL_SOCKET_TIMEOUT_MS` | `30000` | Socket response timeout |
| `RELEASE_INSTALL_WAIT_MS` | `180000` | Install completion timeout |

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

## `release.yml` verify gate

Install-verify is a **hard prerequisite** for tag creation. In **`release.yml`**:

1. **Build** Rust binaries (matrix).
2. **Prepare** — sync `bin/` to `main`, commit if needed, **push `main`** (no tag yet).
3. **Install verify** — reusable job with `install_mode: main` and `fail_fast: true`. If any OS fails, **`release.yml`** stops here.
4. **Publish script** — only after verify passes: `release-publish-orchestrator.sh` creates and pushes the tag, bumps patch on `main`; **`release.yml`** then publishes the **GitHub Release** + assets.

A tag push still triggers **`release-install-verify.yml`** separately as a post-ship check (`install_mode: tag`).

---

## Phase 1: `test_install.py`

Run inside the first FreeCAD GUI process.

**Steps:**

1. Locate Addon Manager (`Addon` + `AddonInstaller` from user or installation Mod paths).
2. Remove any stale `freecad-mcp-bridge` (or `*mcp-bridge*`) install under `Mod/`.
3. Install via Addon Manager (`InstallationMethod.ANY`) using `build_addon_descriptor()` for the chosen mode.
4. Poll until `package.xml` with the expected version appears (handles nested GitHub zip directories).
5. **Flatten** nested install paths to `Mod/freecad-mcp-bridge/` so FreeCAD autoloads the addon on restart.
6. **Verify on-disk tree:**
   - `package.xml` (version matches tag)
   - `freecad/mcp_bridge/{__init__.py,init_gui.py,bridge.py}`
   - Platform binary under `bin/{win32,linux,macos}/`

On success, the script exits via `quit_freecad(0)` and CI launches verify in a new FreeCAD process.

---

## Phase 2: `test_verify.py`

Run inside the **second** FreeCAD process after restart. Does **not** call `App.MCPBridgeInjectUi()` — it relies on the addon's own `init_gui.py` startup hooks.

**Checklist:**

| Step | Assertion |
|------|-----------|
| Auto-registration | `MCP_Bridge_Toggle` in `Gui.listCommands()` |
| Install dir | `Mod/freecad-mcp-bridge/` exists |
| Toolbar injection | Find **MCP Bridge On/Off** on the MCP Bridge toolbar (timeout) |
| Start listener | First toolbar click → bridge instance `isListening()` |
| Socket round-trip | `QLocalSocket` connect to `freecad_mcp_bridge_socket`, send probe Python, expect `test_verify_ok` in response |
| Stop listener | Second toolbar click → listener stopped |
| Report view | Contains `[MCP Bridge] Stopped socket listener.` (or `RELEASE_INSTALL_STOP_MESSAGE`) |
| Status bar | Contains `MCP Bridge: Offline` |

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
| `.github/workflows/install-verify-reusable.yml` | Reusable verify job (called by `release.yml` and install-verify workflow) |
| `.github/workflows/release-install-verify.yml` | Standalone dispatch / post-tag verify |
| `scripts/ci_run_freecad.sh` | FreeCAD launcher + timeout + log path |
| `scripts/publish_ci_log.sh` | Log file → GitHub annotations |
| `scripts/test_install.py` | Addon Manager install phase |
| `scripts/test_verify.py` | Post-restart UI + socket phase |
| `scripts/test_install_common.py` | Shared helpers, CI log, `quit_freecad()` |