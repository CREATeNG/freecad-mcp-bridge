"""Shared helpers for test_install / test_verify FreeCAD scripts."""

from __future__ import annotations

import os
import sys
import xml.etree.ElementTree as ET

LOG_PREFIX = "[test_install]"


def log(message: str) -> None:
    import FreeCAD as App

    App.Console.PrintMessage(f"{LOG_PREFIX} {message}\n")


def quit_freecad(exit_code: int = 0) -> None:
    """Exit FreeCAD from a GUI script (App.quit is not available in all builds)."""
    for name in ("quit", "exit", "closeApplication"):
        fn = getattr(__import__("FreeCAD"), name, None)
        if callable(fn):
            fn()
            sys.exit(exit_code)

    try:
        import FreeCADGui as Gui
        from PySide.QtCore import QCoreApplication

        mw = Gui.getMainWindow()
        if mw is not None:
            mw.close()
        QCoreApplication.processEvents()
        app = QCoreApplication.instance()
        if app is not None:
            app.quit()
            QCoreApplication.processEvents()
    except Exception:
        pass

    sys.exit(exit_code)


def fail(message: str) -> None:
    import FreeCAD as App

    App.Console.PrintError(f"{LOG_PREFIX} {message}\n")
    quit_freecad(1)


def env(name: str, default: str) -> str:
    return os.environ.get(name, default).strip()


def version_from_tag(tag: str) -> str:
    return tag[1:] if tag.startswith("v") else tag


def install_dir() -> str:
    import FreeCAD as App

    addon_name = env("RELEASE_INSTALL_NAME", "freecad-mcp-bridge")
    return os.path.join(App.getUserAppDataDir(), "Mod", addon_name)


def addon_manager_paths() -> list[str]:
    import FreeCAD as App

    candidates = [
        os.path.join(App.getUserAppDataDir(), "Mod", "AddonManager"),
        os.path.join(App.getHomePath(), "Mod", "AddonManager"),
    ]
    return [path for path in candidates if os.path.isdir(path)]


def platform_bin_relpath() -> str:
    if sys.platform.startswith("win"):
        return os.path.join("bin", "win32", "freecad-mcp-bridge.exe")
    if sys.platform == "darwin":
        return os.path.join("bin", "macos", "freecad-mcp-bridge")
    return os.path.join("bin", "linux", "freecad-mcp-bridge")


def verify_install_tree(install_dir: str, expected_version: str) -> None:
    required = [
        "package.xml",
        os.path.join("freecad", "mcp_bridge", "__init__.py"),
        os.path.join("freecad", "mcp_bridge", "init_gui.py"),
        os.path.join("freecad", "mcp_bridge", "bridge.py"),
        platform_bin_relpath(),
    ]
    for rel_path in required:
        full_path = os.path.join(install_dir, rel_path)
        if not os.path.isfile(full_path):
            fail(f"Missing required file: {rel_path}")
        if rel_path.startswith("bin/") and os.path.getsize(full_path) <= 0:
            fail(f"Binary is empty: {rel_path}")

    package_xml = os.path.join(install_dir, "package.xml")
    try:
        root = ET.parse(package_xml).getroot()
    except ET.ParseError as exc:
        fail(f"Invalid package.xml: {exc}")

    version_el = root.find("version")
    if version_el is None or not version_el.text:
        fail("package.xml is missing <version>")
    if version_el.text.strip() != expected_version:
        fail(
            f"Expected version {expected_version}, found {version_el.text.strip()}"
        )

    log(f"Install tree OK at {install_dir} (version {expected_version})")


def build_addon_descriptor(
    addon_name: str, repo_url: str, tag: str, mode: str
) -> tuple[str, str]:
    if mode == "index_zip":
        zip_url = f"{repo_url.rstrip('/')}/archive/refs/tags/{tag}.zip"
        return zip_url, tag
    if mode == "tag":
        return repo_url.rstrip("/"), tag
    fail(f"Unsupported RELEASE_INSTALL_MODE: {mode}")
    return "", ""


def schedule_main(main_fn) -> None:
    from PySide.QtCore import QTimer

    delay_ms = int(env("RELEASE_INSTALL_GUI_DELAY_MS", "3000"))
    QTimer.singleShot(delay_ms, main_fn)