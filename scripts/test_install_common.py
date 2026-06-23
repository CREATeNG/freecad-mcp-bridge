"""Shared helpers for test_install / test_verify FreeCAD scripts."""

from __future__ import annotations

import os
import sys
import xml.etree.ElementTree as ET

LOG_PREFIX = "[test_install]"
PACKAGE_NS = "https://wiki.freecad.org/Package_Metadata"


def _drain_qt_events(delay_ms: int = 2000) -> None:
    try:
        from PySide.QtCore import QCoreApplication, QThread

        interval_ms = 100
        attempts = max(1, delay_ms // interval_ms)
        for _ in range(attempts):
            QCoreApplication.processEvents()
            QThread.msleep(interval_ms)
    except Exception:
        pass


def log(message: str) -> None:
    import FreeCAD as App

    App.Console.PrintMessage(f"{LOG_PREFIX} {message}\n")


def quit_freecad(exit_code: int = 0) -> None:
    """Exit FreeCAD from a GUI script (App.quit is not available in all builds)."""
    _drain_qt_events()

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


def mod_dir() -> str:
    import FreeCAD as App

    return os.path.join(App.getUserAppDataDir(), "Mod")


def install_dir() -> str:
    addon_name = env("RELEASE_INSTALL_NAME", "freecad-mcp-bridge")
    return os.path.join(mod_dir(), addon_name)


def _version_from_package_root(root: ET.Element) -> str | None:
    for tag in (f"{{{PACKAGE_NS}}}version", "version"):
        version_el = root.find(tag)
        if version_el is not None and version_el.text:
            return version_el.text.strip()
    for el in root.iter():
        if el.tag.endswith("version") and el.text and el.text.strip():
            return el.text.strip()
    return None


def _package_version(package_xml: str) -> str | None:
    try:
        root = ET.parse(package_xml).getroot()
    except ET.ParseError:
        return None
    return _version_from_package_root(root)


def find_install_dir(addon_name: str, expected_version: str) -> str | None:
    """Locate the installed addon, including nested GitHub zip layouts."""
    mod_root = mod_dir()
    if not os.path.isdir(mod_root):
        return None

    candidates: list[str] = []
    preferred = install_dir()
    candidates.append(preferred)
    if os.path.isdir(preferred):
        for entry in sorted(os.listdir(preferred)):
            sub = os.path.join(preferred, entry)
            candidates.append(sub)
            if os.path.isdir(sub):
                for nested in sorted(os.listdir(sub)):
                    candidates.append(os.path.join(sub, nested))
    for entry in sorted(os.listdir(mod_root)):
        candidates.append(os.path.join(mod_root, entry))

    seen: set[str] = set()
    for candidate in candidates:
        if candidate in seen or not os.path.isdir(candidate):
            continue
        seen.add(candidate)
        package_xml = os.path.join(candidate, "package.xml")
        if not os.path.isfile(package_xml):
            continue
        if _package_version(package_xml) == expected_version:
            return candidate
    return None


def wait_for_install_dir(
    addon_name: str, expected_version: str, timeout_ms: int = 120000
) -> str:
    from PySide.QtCore import QCoreApplication, QThread

    interval_ms = 250
    attempts = max(1, timeout_ms // interval_ms)
    for _ in range(attempts):
        QCoreApplication.processEvents()
        found = find_install_dir(addon_name, expected_version)
        if found is not None:
            return found
        QThread.msleep(interval_ms)

    fail(
        "Timed out waiting for installed addon. "
        f"Mod tree under {mod_dir()}:\n{describe_mod_dir()}"
    )
    return ""


def describe_mod_dir() -> str:
    mod_root = mod_dir()
    if not os.path.isdir(mod_root):
        return f"(missing {mod_root})"

    lines: list[str] = []
    for root, dirs, files in os.walk(mod_root):
        depth = root[len(mod_root) :].count(os.sep)
        indent = "  " * depth
        lines.append(f"{indent}{os.path.basename(root) or mod_root}/")
        for filename in sorted(files)[:12]:
            lines.append(f"{indent}  {filename}")
        if len(files) > 12:
            lines.append(f"{indent}  ... ({len(files)} files)")
        dirs[:] = sorted(dirs)[:8]
    return "\n".join(lines) or "(empty)"


def installed_addon_dir() -> str:
    addon_name = env("RELEASE_INSTALL_NAME", "freecad-mcp-bridge")
    expected_version = version_from_tag(env("RELEASE_INSTALL_TAG", "v0.1.11"))
    found = find_install_dir(addon_name, expected_version)
    return found if found is not None else install_dir()


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

    found_version = _version_from_package_root(root)
    if found_version is None:
        fail("package.xml is missing <version>")
    if found_version != expected_version:
        fail(f"Expected version {expected_version}, found {found_version}")

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