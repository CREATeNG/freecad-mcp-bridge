# Development & Advanced Usage Guidelines

This document contains instructions for building the bridge from source, running the alternative Python-based MCP server, and using the direct command-line test utility.

---

## 1. Building the Rust MCP Server from Source

If you want to compile the self-contained Rust binary yourself (instead of using the precompiled ones in `bin/`):

1. Ensure you have Rust and Cargo installed.
2. Navigate to the Rust directory:
   ```bash
   cd rust_mcp_server
   ```
3. Build the release binary:
   ```bash
   cargo build --release
   ```
4. The compiled executable will be located at:
   `rust_mcp_server/target/release/freecad-mcp-bridge.exe` (or `freecad-mcp-bridge` on Unix).

---

## Qt imports

Code loaded inside FreeCAD (`freecad/mcp_bridge/init_gui.py`, `freecad/mcp_bridge/bridge.py`) uses FreeCAD's `PySide` shim (`PySide.QtCore`, `PySide.QtNetwork`, etc.), which maps to the Qt binding bundled with your FreeCAD install.

The out-of-process helpers below (`freecad_mcp_server.py`, `send_cmd.py`) run in a normal Python environment and use standalone `PySide6` instead.

## Icons

The addon uses a single **SVG** icon at `Resources/Icons/icon.svg` (repo root). That is sufficient for Windows, Linux, and macOS:

* FreeCAD and Qt load SVG via QtSvg on all platforms (no separate `.ico` or `.png` required).
* `package.xml` references the icon with forward slashes; FreeCAD normalizes paths per OS.
* The same file is used for Addon Manager listing and the in-app toolbar/command.

---

## Releases

### Branches, tags, and the Index

* **`main`** is the development branch. It may be ahead of the latest shipped release.
* **Version tags** (`v0.1.11`, etc.) are the authoritative install snapshots. Each tag must contain the full addon: Python sources, `package.xml`, and prebuilt `bin/` clients.
* **FreeCAD Addon Index** (when listed) will pin a specific tag via `git_ref` in [FreeCAD/Addons](https://github.com/FreeCAD/Addons). Users install tagged releases, not rolling `main`.
* Tags before **v0.1.11** used a legacy flow (tag before `bin/` sync) and are not suitable as Index install refs.

### `bin/` layout (shipped with the addon)

| Path | Platform |
|------|----------|
| `bin/win32/freecad-mcp-bridge.exe` | Windows |
| `bin/linux/freecad-mcp-bridge` | Linux x86_64 |
| `bin/macos/freecad-mcp-bridge` | macOS x86_64 |

### Publishing a release

1. Merge finished work into `main` (use short-lived topic branches for larger changes).
2. Bump `<version>` and `<date>` in `package.xml` on `main`, commit, and push `main`.
3. GitHub → **Actions** → **Release** → **Run workflow** (branch: `main`).
4. CI builds Rust MCP binaries for Windows, Linux, and macOS, commits `bin/` to `main` if needed, creates tag `v{version}` from `package.xml`, pushes `main` + tag, and publishes a GitHub Release with platform assets.

Do not reuse a version number; the workflow fails if the tag already exists.

### After the workflow (Index)

Once listed in the FreeCAD Addon Index, open a PR on [FreeCAD/Addons](https://github.com/FreeCAD/Addons) bumping your entry's `git_ref` and `zip_url` to the new tag. Helper:

```bash
bash scripts/bump-index.sh 0.1.11
```

Index cache updates can take up to four hours after the PR merges.

### Automated install verification (CI)

GHA workflow `.github/workflows/release-install-verify.yml` runs
`scripts/release_install_verify.py` inside FreeCAD on Windows, Linux, and macOS.
It uses the Addon Manager installer API to install a release tag into an isolated
profile, then verifies `package.xml`, platform `bin/`, Python import, starts the
bridge listener (toolbar button trigger), and runs a local-socket Python probe
equivalent to `send_cmd.py`.

Triggers: `workflow_dispatch` (choose tag/mode) or push of a `v*` tag.

---

## 2. Alternative Python-Based MCP Server

If you prefer to run the MCP server using Python rather than the compiled binary:

1. Install Python 3.9+ and the required dependencies globally on your host environment:
   ```bash
   python -m pip install PySide6 mcp
   ```
2. Add this entry to your editor's `mcpServers` configuration (e.g. `claude_desktop_config.json`):
   ```json
   {
     "mcpServers": {
       "freecad-bridge": {
         "command": "python",
         "args": [
           "C:\\Users\\<YourUsername>\\AppData\\Roaming\\FreeCAD\\Mod\\freecad-mcp-bridge\\freecad_mcp_server.py"
         ]
       }
     }
   }
   ```

---

## 3. Direct CLI Testing Utility (`send_cmd.py`)

The `send_cmd.py` script is an optional command-line utility. It connects directly to the FreeCAD bridge socket (bypassing the MCP server) to execute Python code. It is useful for testing connection status independently.

*Requires `PySide6` installed globally on your host terminal environment (`python -m pip install PySide6`).*

**Run a code string to test the connection:**
```bash
python send_cmd.py "print('Hello from the terminal!')"
```

**Run a script file:**
```bash
python send_cmd.py -f path/to/your/script.py
```

---

## 4. Manual Macro (No-Install Alternative)

If you do not want to install any addon folders or global toolbars, you can run the bridge as a one-off macro:

1. Open FreeCAD.
2. Go to **Macro ➔ Macros... ➔ Create**.
3. Name it `RunBridge.FCMacro`.
4. Copy the entire contents of `freecad/mcp_bridge/bridge.py` from this project, paste it into the editor tab, and save (**Ctrl + S**).
5. Open the macro list and click **Run** on the window you want to control.
