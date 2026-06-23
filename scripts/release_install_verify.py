"""Install and verify a tagged release through FreeCAD + Addon Manager.

Run inside a FreeCAD GUI process in two separate launches (CI restarts FreeCAD
between them):

  RELEASE_INSTALL_PHASE=install freecad scripts/release_install_verify.py
  RELEASE_INSTALL_PHASE=verify  freecad scripts/release_install_verify.py

Environment:
  RELEASE_INSTALL_PHASE   install | verify (required)
  RELEASE_INSTALL_TAG     Git tag to install (default: v0.1.11)
  RELEASE_INSTALL_REPO    Repository URL
  RELEASE_INSTALL_NAME    Installed Mod folder name (default: freecad-mcp-bridge)
  RELEASE_INSTALL_MODE    tag | index_zip (default: tag)
  RELEASE_INSTALL_PROBE   Python sent over the local socket (verify phase)
  RELEASE_INSTALL_GUI_DELAY_MS        Delay before work starts (default: 3000)
  RELEASE_INSTALL_TOOLBAR_TIMEOUT_MS  Wait for auto-injected toolbar (default: 15000)
"""

from __future__ import annotations

import os
import shutil
import sys
import xml.etree.ElementTree as ET

LOG_PREFIX = "[release_install_verify]"


def _log(message: str) -> None:
    import FreeCAD as App

    App.Console.PrintMessage(f"{LOG_PREFIX} {message}\n")


def _fail(message: str) -> None:
    import FreeCAD as App

    App.Console.PrintError(f"{LOG_PREFIX} {message}\n")
    App.quit()
    sys.exit(1)


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default).strip()


def _phase() -> str:
    phase = _env("RELEASE_INSTALL_PHASE", "")
    if phase not in ("install", "verify"):
        _fail("RELEASE_INSTALL_PHASE must be 'install' or 'verify'")
    return phase


def _version_from_tag(tag: str) -> str:
    return tag[1:] if tag.startswith("v") else tag


def _install_dir() -> str:
    import FreeCAD as App

    addon_name = _env("RELEASE_INSTALL_NAME", "freecad-mcp-bridge")
    return os.path.join(App.getUserAppDataDir(), "Mod", addon_name)


def _addon_manager_paths() -> list[str]:
    import FreeCAD as App

    candidates = [
        os.path.join(App.getUserAppDataDir(), "Mod", "AddonManager"),
        os.path.join(App.getHomePath(), "Mod", "AddonManager"),
    ]
    return [path for path in candidates if os.path.isdir(path)]


def _platform_bin_relpath() -> str:
    if sys.platform.startswith("win"):
        return os.path.join("bin", "win32", "freecad-mcp-bridge.exe")
    if sys.platform == "darwin":
        return os.path.join("bin", "macos", "freecad-mcp-bridge")
    return os.path.join("bin", "linux", "freecad-mcp-bridge")


def _verify_install_tree(install_dir: str, expected_version: str) -> None:
    required = [
        "package.xml",
        os.path.join("freecad", "mcp_bridge", "__init__.py"),
        os.path.join("freecad", "mcp_bridge", "init_gui.py"),
        os.path.join("freecad", "mcp_bridge", "bridge.py"),
        _platform_bin_relpath(),
    ]
    for rel_path in required:
        full_path = os.path.join(install_dir, rel_path)
        if not os.path.isfile(full_path):
            _fail(f"Missing required file: {rel_path}")
        if rel_path.startswith("bin/") and os.path.getsize(full_path) <= 0:
            _fail(f"Binary is empty: {rel_path}")

    package_xml = os.path.join(install_dir, "package.xml")
    try:
        root = ET.parse(package_xml).getroot()
    except ET.ParseError as exc:
        _fail(f"Invalid package.xml: {exc}")

    version_el = root.find("version")
    if version_el is None or not version_el.text:
        _fail("package.xml is missing <version>")
    if version_el.text.strip() != expected_version:
        _fail(
            f"Expected version {expected_version}, found {version_el.text.strip()}"
        )

    _log(f"Install tree OK at {install_dir} (version {expected_version})")


def _build_addon_descriptor(
    addon_name: str, repo_url: str, tag: str, mode: str
) -> tuple[str, str]:
    if mode == "index_zip":
        zip_url = f"{repo_url.rstrip('/')}/archive/refs/tags/{tag}.zip"
        return zip_url, tag
    if mode == "tag":
        return repo_url.rstrip("/"), tag
    _fail(f"Unsupported RELEASE_INSTALL_MODE: {mode}")
    return "", ""


def _run_install_phase() -> None:
    import FreeCAD as App

    tag = _env("RELEASE_INSTALL_TAG", "v0.1.11")
    repo_url = _env(
        "RELEASE_INSTALL_REPO", "https://github.com/CREATeNG/freecad-mcp-bridge"
    )
    addon_name = _env("RELEASE_INSTALL_NAME", "freecad-mcp-bridge")
    mode = _env("RELEASE_INSTALL_MODE", "tag").lower()
    expected_version = _version_from_tag(tag)

    _log(
        f"Install phase (mode={mode}, tag={tag}, repo={repo_url}, "
        f"user_data={App.getUserAppDataDir()})"
    )

    am_paths = _addon_manager_paths()
    if not am_paths:
        _fail("Addon Manager not found under user or installation Mod paths")

    for path in am_paths:
        if path not in sys.path:
            sys.path.insert(0, path)

    from Addon import Addon
    from addonmanager_installer import AddonInstaller, InstallationMethod

    install_dir = _install_dir()
    if os.path.isdir(install_dir):
        _log(f"Removing previous install at {install_dir}")
        shutil.rmtree(install_dir, ignore_errors=True)

    addon_url, addon_branch = _build_addon_descriptor(
        addon_name, repo_url, tag, mode
    )
    addon = Addon(addon_name, addon_url, branch=addon_branch)
    installer = AddonInstaller(addon)
    if not installer.run(InstallationMethod.ANY):
        _fail(f"AddonInstaller failed for {addon_name} ({mode}, {tag})")

    if not os.path.isdir(install_dir):
        _fail(f"Install directory was not created: {install_dir}")

    _verify_install_tree(install_dir, expected_version)
    _log("Install phase passed; restart FreeCAD for verify phase")
    App.quit()
    sys.exit(0)


def _verify_addon_startup() -> None:
    """Confirm FreeCAD loaded and registered the addon without manual injection."""
    import FreeCAD as App
    import FreeCADGui as Gui

    from freecad.mcp_bridge.constants import COMMAND_ID

    if COMMAND_ID not in Gui.listCommands():
        _fail(
            f"Addon command {COMMAND_ID!r} was not registered on startup "
            "(init_gui did not run automatically)"
        )

    if not os.path.isdir(_install_dir()):
        _fail("Installed addon directory is missing after restart")

    _log(f"Addon command {COMMAND_ID!r} registered on startup")


def _find_toolbar_action():
    import FreeCADGui as Gui
    from PySide.QtCore import QCoreApplication, QThread
    from PySide.QtWidgets import QToolBar

    from freecad.mcp_bridge.constants import DISPLAY_NAME, TOOLBAR_OBJECT_NAME

    action_label = "MCP Bridge On/Off"
    timeout_ms = int(_env("RELEASE_INSTALL_TOOLBAR_TIMEOUT_MS", "15000"))
    interval_ms = 200
    attempts = max(1, timeout_ms // interval_ms)

    for _ in range(attempts):
        QCoreApplication.processEvents()

        mw = Gui.getMainWindow()
        if mw:
            for toolbar in mw.findChildren(QToolBar):
                if (
                    toolbar.windowTitle() == DISPLAY_NAME
                    or toolbar.objectName() == TOOLBAR_OBJECT_NAME
                ):
                    for action in toolbar.actions():
                        if action.text() == action_label:
                            return action

        QThread.msleep(interval_ms)

    return None


def _start_bridge_listener() -> None:
    """Start the listener by triggering the real toolbar button."""
    import FreeCAD as App
    from PySide.QtCore import QCoreApplication
    from freecad.mcp_bridge import bridge as bridge_mod

    inst = bridge_mod._bridge_instance
    if inst and inst.isListening():
        _log("Bridge listener already running")
        return

    action = _find_toolbar_action()
    if action is None:
        _fail(
            'Toolbar action "MCP Bridge On/Off" not found after startup '
            "(UI was not auto-injected)"
        )

    _log('Triggering toolbar action "MCP Bridge On/Off"')
    action.trigger()
    QCoreApplication.processEvents()

    inst = bridge_mod._bridge_instance
    if not inst or not inst.isListening():
        _fail("Bridge listener failed to start after toolbar click")


def _verify_socket_round_trip() -> None:
    """Run Python through the local socket (same protocol as send_cmd.py)."""
    from PySide.QtCore import QCoreApplication
    from PySide.QtNetwork import QLocalSocket

    from freecad.mcp_bridge.constants import SOCKET_NAME

    probe = _env(
        "RELEASE_INSTALL_PROBE",
        'print("release_install_verify_ok")',
    )
    if not probe.endswith("\n"):
        probe += "\n"
    expected = _env("RELEASE_INSTALL_PROBE_EXPECT", "release_install_verify_ok")

    socket = QLocalSocket()
    socket.connectToServer(SOCKET_NAME)
    if not socket.waitForConnected(5000):
        _fail(
            "Socket connection failed. Is the bridge listener running? "
            f"({socket.errorString()})"
        )

    socket.write(probe.encode("utf-8"))
    socket.flush()
    socket.waitForBytesWritten(1000)

    if not socket.waitForReadyRead(10000):
        socket.disconnectFromServer()
        _fail("Timed out waiting for bridge execution response")

    response = socket.readAll().data().decode("utf-8")
    socket.disconnectFromServer()
    QCoreApplication.processEvents()

    if expected not in response:
        _fail(
            "Unexpected socket response. "
            f"Expected substring {expected!r}, got: {response!r}"
        )

    _log(f"Socket round-trip OK (saw {expected!r})")


def _run_verify_phase() -> None:
    import FreeCAD as App

    _log(
        "Verify phase after restart "
        f"(user_data={App.getUserAppDataDir()})"
    )

    _verify_addon_startup()
    _start_bridge_listener()
    _verify_socket_round_trip()
    _log("Verify phase passed")
    App.quit()
    sys.exit(0)


def _run_phase() -> None:
    if _phase() == "install":
        _run_install_phase()
    _run_verify_phase()


def _schedule_phase() -> None:
    from PySide.QtCore import QTimer

    delay_ms = int(_env("RELEASE_INSTALL_GUI_DELAY_MS", "3000"))
    QTimer.singleShot(delay_ms, _run_phase)


_schedule_phase()