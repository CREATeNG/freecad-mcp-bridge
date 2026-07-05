# Maintainer guide — releases & CI

Instructions for **maintainers of this repository** who cut releases and manage versioning.

Developers should read **[DEVELOPMENT.md](DEVELOPMENT.md)**. End users should read **[README.md](README.md)**.

For install-verify CI details (scripts, logs, triggers), see **[TESTING.md](TESTING.md)**.

---

## Versioning at a glance

The version number format is `x.y.z`; tags are named `v{x.y.z}` — e.g. shipping version `0.1.42` creates tag `v0.1.42`.

| Part | Meaning | Who changes it |
|------|---------|----------------|
| **`x.y`** (major.minor line) | e.g. `0.1` in `0.1.42` | Repo maintainers, manually, when starting a new line |
| **`z`** (patch) | e.g. `42` in `0.1.42` | GitHub Actions only, via `bump-package-z.sh` |

The version number is stored in `package.xml`, `manifest.json`, and `package.json` — kept in lockstep automatically by the bump process, which syncs the current version across all three whenever it runs, so none of them should ever need a manual edit just to match the others.

The bump runs immediately after each tag is cut, pushing `main` ahead of the last shipped version — this is what keeps tag collisions rare, not continuous policing of `main` (which is expected to be unstable between releases; see [Branches, tags, and the FreeCAD Addon Index](#branches-tags-and-the-freecad-addon-index) below).

---

## Why these rules exist

- **Tags are permanent.** The FreeCAD Addon Index pins a tag as a fixed install source — once `v{x.y.z}` exists, its content must never change, or every install/reference pointing at it silently breaks.
- **Every component in a release carries the same version.** Matching numbers across `package.xml`, `manifest.json`, and `package.json` are what make "version X" mean one coherent thing, not a mismatched patchwork.
- **Version numbers only increase.** A newer version must always sort higher than an older one, or "is this an update" becomes unanswerable — for Addon Manager, for users, for anyone comparing releases.
- **The release pipeline is maximally automated, gated to help ensure the above.** Automation removes the chance of human error at the moment it would matter most — shipping.

---

## Release tags

**Tagged releases for the FreeCAD Addon Index** are created by the **release orchestrator workflow** (`release.yml`). It produces a verified snapshot: install-verify green on three OSes, then tag (with release notes on GitHub). Manual tags are fine for experiments or other uses — only tags from **`release.yml`** should be used as Index `git_ref` values on [FreeCAD/Addons](https://github.com/FreeCAD/Addons).

**FreeCAD Addon Index Qualities:** A listed `git_ref` must point at a complete, installable snapshot per the [FreeCAD Addon Index Qualities](https://freecad.github.io/Addon-Academy/Topics/Addon-Index/Index/Qualities.html) — Python sources and `package.xml`, nothing else required. The addon ships no prebuilt binaries at all; verify runs before the tag specifically to confirm the snapshot actually installs.

---

## Branches, tags, and the FreeCAD Addon Index

* **`main`** is the development branch. It may be ahead of the latest shipped release.
* **Version tags** (`v{x.y.z}`) are the authoritative install snapshots.
* Listed on the [FreeCAD Addon Index](https://github.com/FreeCAD/Addons) as **`freecad-mcp-bridge`**, using **[Alternative 1: Tagged Releases](https://freecad.github.io/Addon-Academy/Guides/Publishing/Indexed)** — the listing pins a specific tag via `git_ref`, not rolling `main`.

---

## When `package.xml` updates

| Field | Trigger | Mechanism |
|-------|---------|-----------|
| **Patch (post-release)** | After **`release.yml`** ships | Bump job increments patch + `<date>` (and syncs the shim's `manifest.json`/`package.json`) |
| **Patch (opt-in)** | Run **version-bump workflow** on `main` | Increments patch + `<date>` (and syncs the shim's `manifest.json`/`package.json`) |

Ordinary pushes do not change `package.xml`. Run **Bump package version** from Actions only when you deliberately want `main` on the next patch before shipping again (uncommon).

FreeCAD Addon Index `git_ref` is the tag name (`v{x.y.z}`), matching `package.xml` by convention.

---

### Workflows

| Name | File | Role |
|------|------|------|
| **Release orchestrator workflow** | [`release.yml`](.github/workflows/release.yml) | Validate manifest → verify (`main`) → tag → **GitHub Release** (notes + `.mcpb` bundle) → verify (tag path) → dispatch Index PR on fork + release-notes link → post-release patch bump. Actions UI: **Release Orchestrator** → Run workflow. |
| **Install-verify workflow** | [`install-verify.yml`](.github/workflows/install-verify.yml) | Confirms addon installation works on Linux, macOS, and Windows. Can be run at any time; `release.yml` calls it before tag (`main`) and after (`tag` path). Actions UI: **Install verify**. |
| **Version-bump workflow** | [`bump-package-version.yml`](.github/workflows/bump-package-version.yml) | Actions UI: **Bump package version** → Run workflow. |

---

## The release orchestrator workflow (`release.yml`)

The **release orchestrator workflow** is what you run to ship a version. It owns validating the release candidate, the verify gate, tagging, GitHub Release notes (plus packing and uploading the `.mcpb` bundle), dispatching an Index PR workflow on [`CREATeNG/FreeCAD-Addons`](https://github.com/CREATeNG/FreeCAD-Addons) (patch + upstream PR runs there), and the post-release patch bump on `main`. There is no standalone tag path.

```mermaid
flowchart TD
  R[release.yml]
  R --> prep[prepare — resolve version, validate mcpb manifest]
  prep --> verify[install_verify pre-tag — main]
  verify -->|fail| stop[No tag / no GitHub Release]
  verify -->|pass| pub[publish — tag + notes + pack/upload .mcpb]
  pub --> postverify[install_verify tag path]
  postverify -->|fail| stop2[No Index PR / no patch bump]
  postverify -->|pass| indexpr[addons_index_pr — dispatch fork workflow]
  postverify -->|pass| zbump[post-release patch bump on main]
```

**`release.yml` job order:**

1. **Prepare** — resolve the version from `package.xml`, verify the tag does not already exist, validate the Claude Desktop bundle manifest (`mcpb validate`), record the release-candidate commit SHA.
2. **Install-verify** (pre-tag) — [`install-verify.yml`](.github/workflows/install-verify.yml) with `install_mode: main`: full Addon Manager install + restart verify on all three OSes against `main`. **No tag if this fails.**
3. **Publish** — push the matching tag (`v{x.y.z}`) on the verified commit, create a **GitHub Release**, then pack the Claude Desktop bundle (`mcpb pack`) and upload it as a release asset. Uses [`release-publish-orchestrator.sh`](scripts/release-publish-orchestrator.sh) (`RELEASE_PUBLISH_AUTHORIZED=true`; not runnable standalone).
4. **Install-verify** (tag path) — same workflow with `install_mode: tag`: final sanity check that install works from the tag ref (how the FreeCAD Addon Index and custom-repo users install). **No Index PR or patch bump if this fails.**
5. **Addons Index PR** — [`trigger-addons-index-pr.sh`](scripts/trigger-addons-index-pr.sh) runs [`index-release.yml`](https://github.com/CREATeNG/FreeCAD-Addons/blob/main/.github/workflows/index-release.yml) on the fork via `workflow_dispatch` (sync upstream, patch [`Data/Index.json`](https://github.com/FreeCAD/Addons/blob/master/Data/Index.json), push branch), then opens the upstream PR on `FreeCAD/Addons` with **`ADDONS_INDEX_DISPATCH_TOKEN`**. Updates GitHub Release notes with the PR link. PAT needs **Actions: read and write** on `CREATeNG/FreeCAD-Addons` and permission to open PRs on `FreeCAD/Addons` (classic `public_repo` or equivalent). If unset, the job skips. **Non-blocking** (`continue-on-error`).
6. **Bump** — increment patch on `main` for the next dev cycle via [`bump-package-z.sh`](scripts/bump-package-z.sh), syncing the shim's `manifest.json`/`package.json` too. Runs in parallel with step 5.

Re-running **`release.yml`** while `package.xml` still names a tag that already exists on GitHub will fail at **prepare**.

---

## Release assets

Each GitHub Release carries one uploaded asset: **`freecad-mcp-bridge.mcpb`** — the packed Claude Desktop bundle, built from `mcp-stdio-shim/` via `mcpb pack` during the **publish** step. The addon itself isn't a release asset — it ships as plain Python source, installed directly from the repo tree (via the tag or its zip), not from anything attached to the release page.

---

## Shipping a release (checklist)

1. Merge finished work into `main` (topic branches for larger changes).
2. Ensure `package.xml` on `main` is the version you intend to ship. Ordinary pushes do not advance the patch number; the **release orchestrator** bumps the patch after a successful ship.
3. GitHub → **Actions** → **Release Orchestrator** → **Run workflow** — runs **`release.yml`** (branch: **`main`** only).
4. Wait for **`release.yml`** to finish (tag-path install-verify, **Addons Index PR** dispatch, and patch bump).
5. Confirm the automated Index PR was opened (link on the GitHub Release). **FreeCAD Addon Index maintainers** review and merge it on [FreeCAD/Addons](https://github.com/FreeCAD/Addons) — you do not merge upstream yourself.

**Duplicate versions are blocked.** **`release.yml`** reads `package.xml`, checks that `v{x.y.z}` does not already exist (**prepare**), and **publish** checks again before tagging. If the tag is already on GitHub, **`release.yml`** fails — no second tag, no partial publish. After shipping, the post-release patch bump on `main` advances the patch; run **`release.yml`** again only when `package.xml` names the version you intend to ship next.

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
* Secret on **`CREATeNG/freecad-mcp-bridge`:** **`ADDONS_INDEX_DISPATCH_TOKEN`** — PAT that can **run Actions** on `CREATeNG/FreeCAD-Addons` and **open PRs** on `FreeCAD/Addons` (classic **`public_repo`** scope, or fine-grained with both repos).

If automation is skipped or fails, use the local helper (prints fields only; does not edit any file):

```bash
bash scripts/index-pr-fields.sh x.y.z
```

FreeCAD Addon Index cache refresh can take up to **four hours** after the PR merges.

---

## Automated install verification (CI)

See **[TESTING.md](TESTING.md)** for the full CI architecture: triggers, install vs verify phases, `ci_run_freecad.sh`, CI log format, and GitHub annotation behavior.

Summary: **`install-verify.yml`** runs `test_install.py` and `test_verify.py` in **two separate FreeCAD processes** per OS (bash launcher on all platforms). See [TESTING.md](TESTING.md) for trigger and mode details.