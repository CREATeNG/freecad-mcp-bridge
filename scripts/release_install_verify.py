"""Verify a tagged release installs correctly via the Addon Manager API.

Run inside a FreeCAD GUI process (CI or local):

  freecad scripts/release_install_verify.py
  xvfb-run -a freecad scripts/release_install_verify.py

Environment:
  RELEASE_INSTALL_TAG       Git tag to install (default: v0.1.11)
  RELEASE_INSTALL_REPO      Repository URL (default: CREATeNG/freecad-mcp-bridge)
  RELEASE_INSTALL_NAME      Installed Mod folder name (default: freecad-mcp-bridge)
  RELEASE_INSTALL_MODE      tag | index_zip (default: tag)
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


def _version_from_tag(tag: str) -> str:
    return tag[1:] if tag.startswith("v") else tag


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


def _verify_python_import(install_dir: str) -> None:
    if install_dir not in sys.path:
        sys.path.insert(0, install_dir)

    import importlib

    importlib.import_module("freecad.mcp_bridge.bridge")
    _log("Imported freecad.mcp_bridge.bridge")


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


def _run_verification() -> None:
    import FreeCAD as App

    tag = _env("RELEASE_INSTALL_TAG", "v0.1.11")
    repo_url = _env(
        "RELEASE_INSTALL_REPO", "https://github.com/CREATeNG/freecad-mcp-bridge"
    )
    addon_name = _env("RELEASE_INSTALL_NAME", "freecad-mcp-bridge")
    mode = _env("RELEASE_INSTALL_MODE", "tag").lower()
    expected_version = _version_from_tag(tag)

    _log(
        f"Starting install verify (mode={mode}, tag={tag}, repo={repo_url}, "
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

    mod_dir = os.path.join(App.getUserAppDataDir(), "Mod")
    install_dir = os.path.join(mod_dir, addon_name)
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
    _verify_python_import(install_dir)
    _log("Release install verification passed")
    App.quit()
    sys.exit(0)


def _schedule_verification() -> None:
    from PySide.QtCore import QTimer

    delay_ms = int(_env("RELEASE_INSTALL_GUI_DELAY_MS", "3000"))
    QTimer.singleShot(delay_ms, _run_verification)


_schedule_verification()