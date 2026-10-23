# Testing

This guide covers the project's automated suites, local test commands
and CI install verification. Run commands from the repository root.
The shell examples use Bash, including Git Bash on Windows.

For release tagging and Index updates, see
[MAINTAINING.md](MAINTAINING.md).

## Python unit tests

The [unit suite](tests/unit/) runs without FreeCAD or Qt. It checks
paging and replay, MCP request validation and result formatting,
preference defaults and controls, source-only distribution, the copy
installer, agreement among the three release-version files, and
the changelog section for that version. These
checks include fake clocks, output queues and a stand-in parameter
store where those replace the running application.

The tracked-file distribution check needs Git, and the version-bump
script test needs Bash with `python3` on its `PATH`. Use Python 3.11,
matching CI:

```bash
python -m unittest discover -s tests/unit -t . -v
```

[tests.yml](.github/workflows/tests.yml) runs this as **Unit (Python)**
on Ubuntu for every push and pull request. The job runs
[list_requirements.py](scripts/list_requirements.py), which fails if
any acceptance test lacks a docstring, then runs the suite under
coverage. It writes the requirement list and the addon line-coverage
report to the job summary.

## Stdio shim tests

The [Node test suite](tests/shim/shim.test.js) tests the shim in
[mcp-stdio-shim/](mcp-stdio-shim/). It launches the shim as a child
process against a stand-in HTTP bridge or a closed loopback port.
Among the checks are input framing, forwarding and protocol headers,
JSON and single-event SSE replies, notification silence, and the
fallback responses when FreeCAD is unreachable.

Use Node 20 to match CI; no FreeCAD or third-party Node packages are
needed:

```bash
node --test tests/shim/*.test.js
```

[tests.yml](.github/workflows/tests.yml) runs **Shim (Node)** on Ubuntu
for every push and pull request, with Node's experimental test coverage
enabled. These tests use the checkout's shim and a stand-in bridge;
they do not install a packed `.mcpb` or run a Claude Desktop chat.
For manual Desktop setup, see
[DEVELOPMENT.md — Claude Desktop shim](DEVELOPMENT.md#2-claude-desktop-shim-mcp-stdio-shim).

## Acceptance tests against FreeCAD

The [acceptance suite](tests/acceptance/) runs in an external Python
process against the bridge in a running FreeCAD GUI. Among the
behaviors it checks are execution and file execution, stdout/stderr and
tracebacks, output paging and replay, retention preferences, execution
order, GUI event handling, the two supported MCP protocol versions,
HTTP and Origin validation, loopback binding, port changes and stopping
the bridge with running or queued jobs. When `BUNDLE_MCPB`
names a packed `.mcpb`, `test_bundle_mcp.py` calls that shim
over stdio, in both protocol versions, and then expects it
to exit. With no bundle named, that test skips;
`verify_bundle: true`, as release and pull-request runs
set it, always names one.

Use a disposable FreeCAD session and test profile. The suite creates
documents and temporary files, changes preferences and toggles the
bridge, including restarting it on another port. Tests restore
preferences and bridge state on their normal paths; a failed test can
leave that work unfinished.

Install the addon version under test, open a fresh FreeCAD GUI session
and turn the bridge on with its toolbar button. Keep the GUI running
while the suite runs. With Python 3.11 in a second terminal, set the
URL explicitly to that session's port:

```bash
export BRIDGE_URL=http://127.0.0.1:39280/mcp
python -m unittest discover -s tests/acceptance -t . -v
```

The tests exercise the installed addon; the checkout supplies the
tests and shared protocol helpers, so use a matching checkout.
Without `BRIDGE_URL`, the client helper refuses to run.

The acceptance test docstrings state the application requirements.
Print their opening lines without importing the tests or connecting
to FreeCAD:

```bash
python scripts/list_requirements.py
```

In CI, [install-verify.yml](.github/workflows/install-verify.yml) starts
a third FreeCAD process with
[acceptance_serve.py](scripts/acceptance_serve.py). That process turns
the installed bridge on through its toolbar action and writes its port
to `ACCEPTANCE_READY_FILE`. The runner's Python runs the suite, then
creates `ACCEPTANCE_STOP_FILE` so FreeCAD exits. The helper also checks
a serving deadline on the GUI event loop. Any outer process timeout is
set by the launcher in [ci_run_freecad.sh](scripts/ci_run_freecad.sh).

Acceptance runs on Linux, Windows and macOS when the caller enables
the workflow's `acceptance` input. The push-to-main, same-repository PR
and release callers enable it. A hand-started install-verify run
defaults to it but can turn it off. Index mode skips the suite.

## Manual bridge testing

For code-string and script-file probes with `send_cmd.py`, use
[DEVELOPMENT.md — Direct CLI testing utility](DEVELOPMENT.md#1-direct-cli-testing-utility-send_cmdpy).
Those instructions remain there; the automated suites above provide
repeatable checks.

## Install verification

The install-verify workflow installs the addon through FreeCAD's Addon
Manager: `main` as a release candidate, a tagged release, a named
branch, the release branch's archive or the live Index entry. A
hand-started run can instead copy the checkout into FreeCAD's Mod
folder. It restarts FreeCAD and checks addon startup and an HTTP
execution round-trip. The optional acceptance phase then checks the
installed bridge more broadly.

[install-verify.yml](.github/workflows/install-verify.yml) runs on three
platforms in parallel:

| Platform | FreeCAD install | Display / GUI |
|----------|-----------------|---------------|
| Linux | official AppImage, extracted | `xvfb-run` |
| macOS | official arm64 `.dmg` | native |
| Windows | `winget install FreeCAD.FreeCAD` | native (Git Bash launcher) |

The FreeCAD version is set once, as `FREECAD_VERSION` in the workflow. The AppImage and `.dmg` downloads are checked against FreeCAD's published SHA256 files and cached per version.

Each matrix job launches **two separate FreeCAD processes** for install
and restart verification:

1. **Install** — [scripts/test_install.py](scripts/test_install.py) via `scripts/ci_run_freecad.sh install`
2. **Verify** — [scripts/test_verify.py](scripts/test_verify.py) via `scripts/ci_run_freecad.sh verify` (fresh process, same profile; isolation enabled on Linux and Windows,
   not on macOS)

When acceptance is enabled, a third process serves the installed bridge
as described in [Acceptance tests against FreeCAD](#acceptance-tests-against-freecad).
Shared helpers live in
[`test_install_common.py`](scripts/test_install_common.py).

---

## Workflow triggers

| Trigger | Tag / mode | Typical use |
|---------|------------|-------------|
| `workflow_call` from **[release.yml](.github/workflows/release.yml)** | `install_mode: main` or `tag`; `install_tag` from prepare; `acceptance: true`; pre-tag also `verify_bundle: true` | Pre-tag gate (`main`, including the packed shim) and post-tag sanity check (`tag` path) |
| `workflow_call` from **[index-after-release.yml](.github/workflows/index-after-release.yml)**, on a schedule or `workflow_dispatch` | `install_mode: index`; `install_tag` from `release`'s `package.xml` | What Index users receive, once the catalog serves the release |
| `workflow_call` from **[acceptance-main.yml](.github/workflows/acceptance-main.yml)**, on every push to `main` | `install_mode: main`; `install_tag` from the pushed `package.xml`; `acceptance: true` | Acceptance tests on each change to `main` |
| `workflow_call` from **[acceptance-pr.yml](.github/workflows/acceptance-pr.yml)**, on every pull request into `main` (opened, synchronize, reopened) | `install_mode: branch`; `install_branch` the PR's head branch; `install_tag` from the PR head's `package.xml`; `acceptance: true`; `coverage: true`; `verify_bundle: true` | Packs that branch's `.mcpb` and runs acceptance, including the packed shim, before the change merges into `main`; skipped, with a notice, for PRs from forks |
| `workflow_dispatch` | Inputs: `tag` (e.g. `v0.1.15`), `mode` (`tag`, `index_zip`, `main`, `branch`, `index`, or `copy`), `branch` (branch mode only), `coverage`, `acceptance` | Ad-hoc CI run; iterate on scripts; prove a branch before it merges into `main`. `copy` runs only when started by hand |

For a hand-started run, set `tag` to the version expected from the
chosen source; the form's default does not follow that source. Index
mode ignores the tag input.

[`install-verify.yml`](.github/workflows/install-verify.yml) sets the
install-source and timing variables, and the scripts (`test_install.py`,
`test_verify.py`) hold the local defaults. The variables listed in each
script's module docstring (overridable when debugging) fall into three
groups:

- **Install source** — which tag/repo/mode/Mod-folder-name to install.
- **Timing** — how long to wait for GUI startup, toolbar injection, the HTTP exec round-trip, and overall install completion.
- **Expected strings** (verify phase only) — the probe code sent over HTTP, the expected output substring, and the expected Report-view message after stopping the bridge.

### Coverage reports

[tests.yml](.github/workflows/tests.yml) publishes the Python unit
coverage report in its job summary and Node's shim coverage in the
test output. The PR acceptance caller also enables the install
workflow's coverage trial: it measures the installed addon in FreeCAD,
adds per-OS summaries and uploads `coverage-data-{os}` artifacts.
Coverage setup and reporting for that trial are best-effort steps;
they do not make a failing acceptance test pass or impose a minimum
coverage percentage.

### Required checks for pull requests

Every change to `main` goes through a pull request. Require exactly these checks in the ruleset for `main`, and no others:

| Check | Workflow |
|-------|----------|
| `Unit (Python)` | [`tests.yml`](.github/workflows/tests.yml) |
| `Shim (Node)` | [`tests.yml`](.github/workflows/tests.yml) |
| `Acceptance (PR) / Verify - linux` | [`acceptance-pr.yml`](.github/workflows/acceptance-pr.yml) calling `install-verify.yml` |
| `Acceptance (PR) / Verify - windows` | [`acceptance-pr.yml`](.github/workflows/acceptance-pr.yml) calling `install-verify.yml` |
| `Acceptance (PR) / Verify - macos` | [`acceptance-pr.yml`](.github/workflows/acceptance-pr.yml) calling `install-verify.yml` |

Branch mode installs from this repository's URL, so the acceptance checks never report for a PR from a fork; push its branch to this repository to run them.

---

## Install modes

### `tag` (default)

Addon Manager installs from the Git repository URL with `branch` set to the release tag. This matches how users add a custom repository and pick a tag in Preferences.

### `index_zip`

Addon Manager installs from the GitHub archive ZIP URL:

`{REPO}/archive/refs/heads/release.zip`

Use this mode to validate the archive the FreeCAD Addon Index serves via `zip_url`. `RELEASE_INSTALL_TAG` names the version `release` is expected to hold. After install, `flatten_install_dir()` still normalizes nested zip layouts to `Mod/freecad-mcp-bridge/`.

### `main`

Addon Manager installs from the repository URL with `branch=main`. Used by the **[release.yml](.github/workflows/release.yml) verify gate**: install-verify installs `main` as it stands when the leg runs, **before** the release tag is created, and publish then refuses to tag unless `main` is still the commit prepare recorded — the last chance to catch a broken install before an irreversible tag gets cut (tags are permanent; see [MAINTAINING.md — Why these rules exist](MAINTAINING.md#why-these-rules-exist)). `RELEASE_INSTALL_TAG` still supplies the expected `package.xml` version for on-disk checks.

### `branch`

Addon Manager installs from the repository URL with `branch` set to a named branch, as a user's custom repository does. The branch is the workflow's `branch` input (`install_branch` under `workflow_call`), or, when that is empty, the branch the run is on, so a branch can be installed and verified before anything from it is merged into `main`. With `branch` set to `release`, this is the README's custom-repository route. The branch must be pushed: Addon Manager fetches it from the repository, not from the run's checkout. `RELEASE_INSTALL_TAG` names the version the branch's `package.xml` is expected to hold.

### `index`

Addon Manager loads the live Addons Index catalog (`CreateAddonListWorker.get_remote_cache`, `AddonCatalog`), takes the `freecad-mcp-bridge` entry a clean install would, and installs it with default preferences, logging the entry, its ZIP URL and the Addon Manager's own console lines. The version is whatever the Index serves, checked against the version the catalog states; `RELEASE_INSTALL_TAG` is not checked, and the acceptance tests do not run.

### `copy`

`copy` copies this run's checkout into FreeCAD's user Mod folder
as `freecad-mcp-bridge`, the way a developer places the addon,
then runs the same on-disk check, restart verify, and acceptance
phases as the other modes. The checkout is the repo root, which
holds `package.xml` and `freecad/` (`init_gui.py` is
`freecad/mcp_bridge/init_gui.py`). The copy leaves out the
checkout's `.git` and `.github` directories. On FreeCAD 1.1
the Mod folder is the versioned `v1-1` directory (on Windows,
`%APPDATA%\FreeCAD\v1-1\Mod`). `RELEASE_INSTALL_TAG` names the
version that `package.xml` is expected to hold.

A hand-started `workflow_dispatch` run selects `copy` from its
mode list. [acceptance-pr.yml](.github/workflows/acceptance-pr.yml), [acceptance-main.yml](.github/workflows/acceptance-main.yml),
[release.yml](.github/workflows/release.yml) and [index-after-release.yml](.github/workflows/index-after-release.yml) do not pass
`copy`.

This mode checks installation by copying into Mod; it does not run a
manual clone operation.

---

## Release gates

In `prepare`, the [release orchestrator](.github/workflows/release.yml)
checks that the shim's bundled tool list matches the addon's
([`check_shim_tools.py`](scripts/check_shim_tools.py)). The workflow
then calls [install-verify](.github/workflows/install-verify.yml)
twice:

1. **Pre-tag** (`install_mode: main`) — hard gate before tag creation.
   The release workflow fails if any OS fails.
2. **Tag path** (`install_mode: tag`) — after tag and release notes;
   final sanity check that install works from the tag ref. The release
   workflow fails before `release` moves if any OS fails.

Runs queue rather than overlap, so a second release run waits for the
first.

For the release job order, see
[MAINTAINING.md — release job order](MAINTAINING.md#the-release-orchestrator-workflow-releaseyml).

---

## Index installation after release

The [after-release Index workflow](.github/workflows/index-after-release.yml) checks what Index users receive. Nothing waits on it. Every three hours, and on `workflow_dispatch`, it compares the version `release`'s `package.xml` names with the version the live catalog states for our entry ([`index_catalog_version.py`](scripts/index_catalog_version.py)):

- **Catalog version differs** — exits quietly, or fails once the release
  has been published for 7 days.
- **Catalog caught up** — calls **[install-verify.yml](.github/workflows/install-verify.yml)** with `install_mode: index` on all three OSes, then uploads an artifact named `index-checked-{version}`. That artifact marks the version as checked, and later runs skip it; the `force` input repeats the install when the catalog matches, even if a marker exists.

---

## Phase 1: Install

Run [test_install.py](scripts/test_install.py) inside the first FreeCAD
GUI process.

**Steps:**

1. Remove stale installs named `freecad-mcp-bridge` or containing
   `mcp-bridge` under the profile's `Mod/` directory.
2. In copy mode, copy the checkout into the target directory. In the
   other modes, locate Addon Manager and install with
   `AddonInstaller.run(InstallationMethod.ANY)`, using the selected
   descriptor or live Index entry.
3. For Addon Manager installs, wait for `package.xml` and flatten nested
   ZIP layouts to `Mod/freecad-mcp-bridge/` for startup loading.
4. Check the installed core files and expected package version. The
   required-file list is in `verify_install_tree()` in
   [test_install_common.py](scripts/test_install_common.py). Index mode
   uses the catalog's version when supplied, rather than the tag input.

On success, the script exits via `quit_freecad(0)` and CI launches verify in a new FreeCAD process.

---

## Phase 2: Verify after restart

Run [test_verify.py](scripts/test_verify.py) inside the **second**
FreeCAD process after restart. Does **not** call `App.MCPBridgeInjectUi()` — it relies on the addon's own `init_gui.py` startup hooks.

**Checklist:**

| Step | Proves |
|------|--------|
| Auto-registration | The addon's command registers on startup with no manual injection — `init_gui.py`'s own startup hooks ran |
| Install dir | The installed addon directory exists |
| Preferences page | The installed `preferences.ui` contains the line telling the user how to start or stop the bridge |
| Toolbar injection | The **MCP Bridge On/Off** toolbar action is found without manual injection |
| Off at startup | The bridge is not running, its button is unchecked and nothing answers on its port before the first click |
| Status-bar button | The permanent status-bar split button sits on the right by default, has no icon, has a `Preferences` menu item, and shows `MCP Off` at startup and after stop, and `MCP On` while running, with matching tooltip and toolbar state |
| Move status-bar button | Setting **Position** (`StatusButtonPosition`) to `1` (**Left of FreeCAD's items**) moves the status-bar button to the left of FreeCAD's permanent status-bar items without a restart, and removing the setting, so the default applies, puts the button back on the right |
| Hide status-bar button | Setting **Show the On/Off button** (`ShowStatusButton`) to `False` removes the status-bar button without a restart, leaving the toolbar button in place, and removing the setting, so the default applies, puts the button back |
| Start bridge | First toolbar click starts the HTTP server |
| Listening status | The status bar shows the loopback address and configured port |
| HTTP round-trip | A client-shaped request — `tools/call` → `execute_python` over HTTP, sent from a worker thread while the main thread pumps the Qt event loop, paginating via `get_output_page` if the response is chunked — returns the expected probe output. Same code path a real MCP client uses. |
| Stop bridge | Clicking the status-bar button's `MCP On` text, not its arrow, stops the HTTP server |
| Report view | Shows the expected "stopped" message (`RELEASE_INSTALL_STOP_MESSAGE`, defaults to the bridge's own stop log line) |
| Status bar | Shows `MCP Bridge: Offline` |

On success, exits with `quit_freecad(0)`.

---

## FreeCAD launcher

The workflows use [ci_run_freecad.sh](scripts/ci_run_freecad.sh) as their
Bash entry point on all three platforms:

```bash
bash scripts/ci_run_freecad.sh install   # phase 1
bash scripts/ci_run_freecad.sh verify    # phase 2
bash scripts/ci_run_freecad.sh acceptance # optional third process
```

**Responsibilities:**

- Resolve FreeCAD binary (`FREECAD_BIN` from the workflow on Linux/macOS; common install paths after winget on Windows; `PATH` for local runs).
- Optionally isolate the profile via `ISOLATE_HOME=true`, setting
  `HOME` and `USERPROFILE` to `FC_CI_HOME`.
- Set `RELEASE_INSTALL_CI_LOG` to a phase-specific file under
  `RUNNER_TEMP`.
- Run FreeCAD with the test script under a **15-minute** timeout (`xvfb-run` on Linux, `perl alarm` on macOS, on Windows `timeout`, else `perl alarm`, else no timeout).
- Invoke [publish_ci_log.sh](scripts/publish_ci_log.sh) after FreeCAD exits.

---

## CI log file and GitHub annotations

FreeCAD's Python stdout is unreliable for GitHub workflow commands on Unix runners. Tests therefore write structured lines to a **file**, and the shell publishes them after exit.

### Log file locations

| Phase | Path |
|-------|------|
| Install | `${RUNNER_TEMP}/freecad-ci-install.log` |
| Verify | `${RUNNER_TEMP}/freecad-ci-verify.log` |
| Acceptance server | `${RUNNER_TEMP}/freecad-ci-acceptance.log` |

Set explicitly via `RELEASE_INSTALL_CI_LOG` (done automatically by [ci_run_freecad.sh](scripts/ci_run_freecad.sh)).

### Line format

Tests append lines via `test_install_common._write_ci_log()`:

| Prefix | Meaning | GitHub mapping ([publish_ci_log.sh](scripts/publish_ci_log.sh)) |
|--------|---------|--------------------------------------|
| `GROUP` | Phase section markers (`=== title ===`) | Echoed inside `::group::` |
| `INFO` | Informational | Echoed as plain log lines |
| `PASS` | Checkpoint passed | `::notice::` annotation |
| `FAIL` | Assertion failed | `::error::` annotation; step fails |

[publish_ci_log.sh](scripts/publish_ci_log.sh) reads the log file, prints a collapsible `::group::` block with all lines, emits notices/errors for `PASS`/`FAIL` lines, and exits non-zero if any `FAIL` line was seen.

**Do not** emit `::notice::` / `::error::` from inside FreeCAD Python — use the file + shell wrapper instead.

---

## Process exit

CI test scripts save active coverage data and call `os._exit(code)`
after a short Qt event drain. This avoids flaky GUI teardown on headless runners (segfaults, access violations, multi-minute hangs). There is no opt-out environment variable.

---

## Running install verification locally

Prerequisites: FreeCAD 1.1 with GUI, Git Bash (Windows) or bash (Unix).

Running the scripts directly uses your real FreeCAD profile: [test_install.py](scripts/test_install.py) removes any existing `freecad-mcp-bridge` install from your `Mod` folder before installing, so a dev install there is replaced.

```bash
export RELEASE_INSTALL_TAG=v0.1.22   # set to the tested source's version
export RELEASE_INSTALL_MODE=main
export RELEASE_INSTALL_REPO=https://github.com/CREATeNG/freecad-mcp-bridge

# Phase 1 — install (replace with your FreeCAD binary)
freecad scripts/test_install.py

# Phase 2 — verify (new FreeCAD process)
freecad scripts/test_verify.py
```

Set `RELEASE_INSTALL_BRANCH` as well for branch mode. Select
`RELEASE_INSTALL_MODE=copy` to install this checkout instead of fetching
a remote branch, and set `RELEASE_INSTALL_TAG` to the version this
checkout's `package.xml` names. Use the same profile for install and
verify, and start a separate process for each.

For the acceptance phase locally, open the installed addon in a fresh
FreeCAD session and follow
[Acceptance tests against FreeCAD](#acceptance-tests-against-freecad).
If using `ci_run_freecad.sh acceptance` instead, set the ready and stop
file paths that
[acceptance_serve.py](scripts/acceptance_serve.py) requires; the workflow
shows the external runner and stop-file coordination.

The launcher expects `RUNNER_TEMP` for its logs. With
`ISOLATE_HOME=true`, or with `RUNNER_OS=Windows`, it also requires
`FC_CI_HOME`. Set `FREECAD_BIN` when FreeCAD is not on `PATH`. See the
launcher's header for its workflow environment.

To preview GitHub-style output from a captured log:

```bash
bash scripts/publish_ci_log.sh /tmp/freecad-ci-verify.log "Verify addon after restart"
```

---

## Related files

| File | Role |
|------|------|
| [.github/workflows/tests.yml](.github/workflows/tests.yml) | Unit and shim suites on pushes and pull requests |
| [tests/unit/](tests/unit/) | Python checks without FreeCAD |
| [tests/shim/](tests/shim/) | Node tests of the stdio shim |
| [tests/acceptance/](tests/acceptance/) | HTTP client tests against FreeCAD |
| [.github/workflows/install-verify.yml](.github/workflows/install-verify.yml) | Reusable install, restart and optional acceptance phases, or a hand-started run |
| [.github/workflows/acceptance-main.yml](.github/workflows/acceptance-main.yml) | Install verify with acceptance on each push to `main` |
| [.github/workflows/acceptance-pr.yml](.github/workflows/acceptance-pr.yml) | Install verify with acceptance on each pull request into `main`, from the PR's branch |
| [.github/workflows/index-after-release.yml](.github/workflows/index-after-release.yml) | Install verify from the Index once its catalog serves the release |
| [scripts/index_catalog_version.py](scripts/index_catalog_version.py) | Prints the version the Index catalog states for the addon |
| [scripts/ci_run_freecad.sh](scripts/ci_run_freecad.sh) | FreeCAD launcher + timeout + log path |
| [scripts/publish_ci_log.sh](scripts/publish_ci_log.sh) | Log file → GitHub annotations |
| [scripts/test_install.py](scripts/test_install.py) | Install phase: Addon Manager, or a copy into the Mod folder |
| [scripts/test_verify.py](scripts/test_verify.py) | Post-restart UI + HTTP phase |
| [scripts/test_install_common.py](scripts/test_install_common.py) | Shared helpers, CI log, `quit_freecad()` |
| [scripts/acceptance_serve.py](scripts/acceptance_serve.py) | FreeCAD host for the external acceptance runner |
| [scripts/list_requirements.py](scripts/list_requirements.py) | Requirement lines from the acceptance tests |
| [scripts/check_shim_tools.py](scripts/check_shim_tools.py) | Checks that the shim's bundled tool list matches the addon's |