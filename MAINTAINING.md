# Maintainer guide — releases & CI

Instructions for **maintainers of this repository** who cut releases and manage versioning.

Developers should read **[DEVELOPMENT.md](DEVELOPMENT.md)**. End users should read **[README.md](README.md)**.

For install-verify CI details (scripts, logs, triggers), see **[TESTING.md](TESTING.md)**.

### Workflows

| Name | File | Role |
|------|------|------|
| **Release orchestrator workflow** | [`release.yml`](.github/workflows/release.yml) | Build → sync `bin/` → verify (`main`) → tag → **GitHub Release** (notes) → verify (tag path) → dispatch Index PR on fork + release-notes link → post-release patch bump. Actions UI: **Release Orchestrator** → Run workflow. |
| **Install-verify workflow** | [`install-verify.yml`](.github/workflows/install-verify.yml) | Confirms addon installation works on Linux, macOS, and Windows. Can be run at any time; `release.yml` calls it before tag (`main`) and after (`tag` path). Actions UI: **Install verify**. |
| **Version-bump workflow** | [`bump-package-version.yml`](.github/workflows/bump-package-version.yml) | Actions UI: **Bump package version** → Run workflow. |

---

## Versioning at a glance

Versions use **`x.y.z`** — e.g. **`0.1.12`** in `package.xml` and `rust_mcp_server/Cargo.toml` (kept in sync).

| Part | In `0.1.12` | Who changes it |
|------|-------------|----------------|
| **`x.y`** (major.minor line) | `0.1` | Repo maintainers, rarely |
| **`z`** (patch) | `12` | GitHub Actions only |

**Tag === version:** shipping `0.1.12` creates tag **`v0.1.12`**. On shipping a release, GitHub Actions automatically bumps the patch on `main` (e.g. to `0.1.13`) for the next dev cycle.

**Patch, `<date>`, and `Cargo.toml` `version` are GitHub Actions-managed** — the **release orchestrator** (and optionally the **version-bump workflow**) update them. Edit **`x.y`** manually only when starting a new line (e.g. `0.1` → `0.2`).

---

## Release tags

**Tagged releases for the FreeCAD Addon Index** are created by the **release orchestrator workflow** (`release.yml`). It produces a verified snapshot: `bin/` synced to `main`, install-verify green on three OSes, then tag (with release notes on GitHub). Manual tags are fine for experiments or other uses — only tags from **`release.yml`** should be used as Index `git_ref` values on [FreeCAD/Addons](https://github.com/FreeCAD/Addons).

**FreeCAD Addon Index Qualities:** A listed `git_ref` must point at a complete, installable snapshot per the [FreeCAD Addon Index Qualities](https://freecad.github.io/Addon-Academy/Topics/Addon-Index/Index/Qualities.html) — Python sources, `package.xml`, and prebuilt `bin/` clients in the repo tree (and in the tag zip). Prepare and verify run before the tag. Tags created before this process (before **v0.1.11**) pointed at commits missing synced binaries and are not suitable for listing.

**v0.1.11** is the first complete tag in this model.

---

## Branches, tags, and the FreeCAD Addon Index

* **`main`** is the development branch. It may be ahead of the latest shipped release.
* **Version tags** (`v0.1.12`, etc.) are the authoritative install snapshots.
* Listed on the [FreeCAD Addon Index](https://github.com/FreeCAD/Addons) as **`freecad-mcp-bridge`**, using **[Alternative 1: Tagged Releases](https://freecad.github.io/Addon-Academy/Guides/Publishing/Indexed)** — the listing pins a specific tag via `git_ref`, not rolling `main`.

---

## When `package.xml` updates

| Field | Trigger | Mechanism |
|-------|---------|-----------|
| **Patch (post-release)** | After **`release.yml`** ships | Bump job increments patch + `<date>` (and syncs `Cargo.toml`) |
| **Patch (opt-in)** | Run **version-bump workflow** on `main` | Increments patch + `<date>` (and syncs `Cargo.toml`) |

Ordinary pushes do not change `package.xml`. Run **Bump package version** from Actions only when you deliberately want `main` on the next patch before shipping again (uncommon).

FreeCAD Addon Index `git_ref` is the tag name (`v0.1.12`), matching `package.xml` by convention.

---

## The release orchestrator workflow (`release.yml`)

The **release orchestrator workflow** is what you run to ship a version. It owns build, verify gate, tagging, GitHub Release notes, dispatching an Index PR workflow on [`CREATeNG/FreeCAD-Addons`](https://github.com/CREATeNG/FreeCAD-Addons) (patch + upstream PR runs there), and the post-release patch bump on `main`. There is no standalone tag path.

This is **not** the local `cargo build` described in [DEVELOPMENT.md](DEVELOPMENT.md). The `build` job below is a cross-platform CI matrix that produces `bin/` artifacts.

```mermaid
flowchart TD
  R[release.yml]
  R --> build[CI: cross-platform Rust build]
  R --> prep[prepare — sync bin/ to main]
  R --> verify[install_verify pre-tag — main]
  verify -->|fail| stop[No tag / no GitHub Release]
  verify -->|pass| pub[publish — tag + notes]
  pub --> tag[tag e.g. v0.1.12 — points at synced bin/]
  tag --> gh[GitHub Release notes]
  gh --> postverify[install_verify tag path]
  postverify -->|fail| stop2[No Index PR / no patch bump]
  postverify -->|pass| indexpr[addons_index_pr — dispatch fork workflow]
  postverify -->|pass| zbump[post-release patch bump on main]
```

### End-to-end maintainer view

```mermaid
flowchart LR
  dev[Develop on main] --> dispatch[Run release.yml]
  dispatch --> shipped[release.yml complete]
  shipped --> index[Index maintainers merge PR on FreeCAD/Addons]
```

**`release.yml` job order:**

1. **CI build** — Rust MCP binaries (Linux, macOS, Windows) in parallel.
2. **Prepare** — download artifacts, install into `bin/`, read the version from `package.xml` (e.g. `0.1.12`), verify the tag does not already exist, commit `bin/` to `main` if changed, **push `main`**.
3. **Install-verify** (pre-tag) — [`install-verify.yml`](.github/workflows/install-verify.yml) with `install_mode: main`: full Addon Manager install + restart verify on all three OSes against `main`. **No tag if this fails.**
4. **Publish** — push the matching tag (e.g. `v0.1.12`) on the verified commit and create a **GitHub Release** for release notes (binaries stay in `bin/` on the tag — not uploaded separately). Uses [`release-publish-orchestrator.sh`](scripts/release-publish-orchestrator.sh) (`RELEASE_PUBLISH_AUTHORIZED=true`; not runnable standalone).
5. **Install-verify** (tag path) — same workflow with `install_mode: tag`: final sanity check that install works from the tag ref (how the FreeCAD Addon Index and custom-repo users install). **No Index PR or patch bump if this fails.**
6. **Addons Index PR** — [`trigger-addons-index-dispatch.sh`](scripts/trigger-addons-index-dispatch.sh) sends `repository_dispatch` to [`CREATeNG/FreeCAD-Addons`](https://github.com/CREATeNG/FreeCAD-Addons) ([`index-release.yml`](https://github.com/CREATeNG/FreeCAD-Addons/blob/main/.github/workflows/index-release.yml): sync upstream, patch [`Data/Index.json`](https://github.com/FreeCAD/Addons/blob/master/Data/Index.json), push branch, open upstream PR using the fork’s `GITHUB_TOKEN`). This job waits for that run and updates GitHub Release notes with the PR link. Secret **`ADDONS_INDEX_DISPATCH_TOKEN`** on this repo (Actions: write on the fork only). If unset, the job skips. **Non-blocking** (`continue-on-error`).
7. **Bump** — increment patch on `main` for the next dev cycle via [`bump-package-z.sh`](scripts/bump-package-z.sh). Runs in parallel with step 6.

**Next ship from `main`:** `0.1.12`. Re-running **`release.yml`** while `package.xml` still names a tag that already exists (e.g. `0.1.11`) will fail at **prepare**.

If Rust sources did not change, the prepare commit step may be a no-op, but verify and publish still run against current `main`.

---

## `bin/` layout (shipped with the addon)

| Path | Platform |
|------|----------|
| `bin/win32/freecad-mcp-bridge.exe` | Windows |
| `bin/linux/freecad-mcp-bridge` | Linux x86_64 |
| `bin/macos/freecad-mcp-bridge` | macOS x86_64 |

---

## Shipping a release (checklist)

1. Merge finished work into `main` (topic branches for larger changes).
2. Ensure `package.xml` on `main` is the version you intend to ship (e.g. `0.1.12`). Ordinary pushes do not advance the patch number; the **release orchestrator** bumps the patch after a successful ship.
3. GitHub → **Actions** → **Release Orchestrator** → **Run workflow** — runs **`release.yml`** (branch: **`main`** only).
4. Wait for **`release.yml`** to finish (tag-path install-verify, **Addons Index PR** dispatch, and patch bump).
5. Confirm the automated Index PR was opened (link on the GitHub Release). **FreeCAD Addon Index maintainers** review and merge it on [FreeCAD/Addons](https://github.com/FreeCAD/Addons) — you do not merge upstream yourself.

**Duplicate versions are blocked.** **`release.yml`** reads `package.xml`, checks that `v{x.y.z}` does not already exist (**prepare**), and **publish** checks again before tagging. If the tag is already on GitHub, **`release.yml`** fails — no second tag, no partial publish. After shipping one version, the post-release patch bump on `main` moves you to the next line (e.g. `0.1.13`); run **`release.yml`** again only when that is the version you intend to ship.

---

## FreeCAD Addon Index

How **this repo's maintainers** update the **`freecad-mcp-bridge`** listing on the FreeCAD Addon Index. **FreeCAD Addon Index maintainers** (the [FreeCAD/Addons](https://github.com/FreeCAD/Addons) team) review and merge changes to [`Data/Index.json`](https://github.com/FreeCAD/Addons/blob/master/Data/Index.json) on FreeCAD/Addons — not in this repository — a different maintainer role.

Guides: [Updating](https://freecad.github.io/Addon-Academy/Guides/Maintaining/Updating), [FreeCAD Addon Index Qualities](https://freecad.github.io/Addon-Academy/Topics/Addon-Index/Index/Qualities.html).

### After each release

1. Run **`release.yml`** → new `v{version}` tag.
2. Confirm **`release.yml`** completed successfully (tag-path install-verify and **Addons Index PR** job).
3. Confirm the automated PR on [FreeCAD/Addons](https://github.com/FreeCAD/Addons) (link on the GitHub Release notes). You can review it; **FreeCAD Addon Index maintainers** merge upstream — same as any external contributor PR. The PR updates `git_ref`, `branch_display_name`, and `zip_url` for the listed entry.

**Prerequisites** (already in place):

* Fork: [`CREATeNG/FreeCAD-Addons`](https://github.com/CREATeNG/FreeCAD-Addons) ([`index-release.yml`](https://github.com/CREATeNG/FreeCAD-Addons/blob/main/.github/workflows/index-release.yml) on fork `main`).
* Secret on **`CREATeNG/freecad-mcp-bridge`:** **`ADDONS_INDEX_DISPATCH_TOKEN`** — PAT with **Actions: read and write** on `CREATeNG/FreeCAD-Addons`.

If automation is skipped or fails, use the local helper (prints fields only; does not edit any file):

```bash
bash scripts/index-pr-fields.sh 0.1.12
```

FreeCAD Addon Index cache refresh can take up to **four hours** after the PR merges.

---

## Automated install verification (CI)

See **[TESTING.md](TESTING.md)** for the full CI architecture: triggers, install vs verify phases, `ci_run_freecad.sh`, CI log format, and GitHub annotation behavior.

Summary: **`install-verify.yml`** runs `test_install.py` and `test_verify.py` in **two separate FreeCAD processes** per OS (bash launcher on all platforms). See [TESTING.md](TESTING.md) for trigger and mode details.