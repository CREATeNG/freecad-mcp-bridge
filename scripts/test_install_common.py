"""Shared helpers for the test_install / test_verify / acceptance_serve FreeCAD scripts."""

from __future__ import annotations

import os
import xml.etree.ElementTree as ET

LOG_PREFIX = "[test_install]"
PACKAGE_NS = "https://wiki.freecad.org/Package_Metadata"
_active_ci_group: str | None = None


def _format_line(message: str) -> str:
    return f"{LOG_PREFIX} {message}"


def _ci_log_path() -> str | None:
    path = env("RELEASE_INSTALL_CI_LOG", "")
    if path:
        return path
    runner_temp = os.environ.get("RUNNER_TEMP", "").strip()
    if runner_temp:
        return os.path.join(runner_temp, "freecad-ci-test.log")
    return None


def _write_ci_log(message: str, *, level: str = "INFO") -> None:
    path = _ci_log_path()
    if not path:
        return
    try:
        directory = os.path.dirname(path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(f"{level} {_format_line(message)}\n")
    except OSError:
        pass


def log_group_start(title: str) -> None:
    """Mark the start of a test phase in the CI log file."""
    global _active_ci_group
    _active_ci_group = title
    _write_ci_log(f"=== {title} ===", level="GROUP")


def log_group_end() -> None:
    """Mark the end of a test phase in the CI log file."""
    global _active_ci_group
    if _active_ci_group is not None:
        _write_ci_log(f"=== end {_active_ci_group} ===", level="GROUP")
        _active_ci_group = None


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

    line = _format_line(message)
    App.Console.PrintMessage(f"{line}\n")
    _write_ci_log(message)


def log_notice(message: str) -> None:
    """Log a passing checkpoint to FreeCAD console and the CI log file."""
    import FreeCAD as App

    line = _format_line(message)
    App.Console.PrintMessage(f"{line}\n")
    _write_ci_log(message, level="PASS")


def _save_coverage() -> None:
    """Stop and save coverage, when a CI coverage run started it here.

    os._exit skips coverage's atexit save. Does nothing when coverage is not
    importable or not measuring this process.
    """
    try:
        import coverage
    except ImportError:
        return
    try:
        cov = coverage.Coverage.current()
        if cov is None:
            if env("COVERAGE_PROCESS_START", ""):
                log("COVERAGE_PROCESS_START is set but coverage is not active")
            return
        cov.stop()
        cov.save()
        log("Coverage data saved")
    except Exception as exc:
        log(f"Coverage save failed: {exc!r}")


def quit_freecad(exit_code: int = 0) -> None:
    """Exit the FreeCAD process from a CI test script.

    These scripts only run under automation; hard-exit avoids flaky GUI teardown
    on headless runners (SIGSEGV, access violations, 15-minute hangs).
    """
    _drain_qt_events(500)
    _save_coverage()
    os._exit(exit_code)


def fail(message: str) -> None:
    import FreeCAD as App

    line = _format_line(message)
    App.Console.PrintError(f"{line}\n")
    _write_ci_log(message, level="FAIL")
    log_group_end()
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


def flatten_install_dir(resolved_dir: str) -> str:
    """Move a nested GitHub-zip install up to Mod/<addon_name>/ for autoload."""
    import shutil

    preferred = install_dir()
    resolved = os.path.normpath(resolved_dir)
    target = os.path.normpath(preferred)

    if resolved == target:
        return target

    package_xml = os.path.join(resolved, "package.xml")
    if not os.path.isfile(package_xml):
        fail(f"Cannot flatten install: missing package.xml in {resolved}")

    log(f"Flattening install from {resolved} to {target}")
    staging = f"{target}__flatten__"
    if os.path.isdir(staging):
        shutil.rmtree(staging, ignore_errors=True)

    shutil.move(resolved, staging)
    if os.path.isdir(target):
        shutil.rmtree(target, ignore_errors=True)
    shutil.move(staging, target)

    leftover = os.path.dirname(resolved)
    if (
        leftover != target
        and os.path.isdir(leftover)
        and leftover.startswith(os.path.dirname(target))
        and not os.listdir(leftover)
    ):
        os.rmdir(leftover)

    return target


def installed_addon_dir() -> str:
    addon_name = env("RELEASE_INSTALL_NAME", "freecad-mcp-bridge")
    expected_version = version_from_tag(env("RELEASE_INSTALL_TAG", "v0.1.11"))
    found = find_install_dir(addon_name, expected_version)
    if found is not None:
        return flatten_install_dir(found)
    return install_dir()


def addon_manager_paths() -> list[str]:
    import FreeCAD as App

    candidates = [
        os.path.join(App.getUserAppDataDir(), "Mod", "AddonManager"),
        os.path.join(App.getHomePath(), "Mod", "AddonManager"),
    ]
    return [path for path in candidates if os.path.isdir(path)]


def verify_install_tree(install_dir: str, expected_version: str) -> None:
    required = [
        "package.xml",
        os.path.join("freecad", "mcp_bridge", "__init__.py"),
        os.path.join("freecad", "mcp_bridge", "init_gui.py"),
        os.path.join("freecad", "mcp_bridge", "bridge.py"),
    ]
    for rel_path in required:
        full_path = os.path.join(install_dir, rel_path)
        if not os.path.isfile(full_path):
            fail(f"Missing required file: {rel_path}")

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

    log_notice(f"Install tree OK at {install_dir} (version {expected_version})")


def build_addon_descriptor(
    addon_name: str, repo_url: str, tag: str, mode: str
) -> tuple[str, str]:
    if mode == "index_zip":
        # What the Index serves: the release branch's archive. The tag only
        # names the version that branch is expected to hold.
        zip_url = f"{repo_url.rstrip('/')}/archive/refs/heads/release.zip"
        return zip_url, "release"
    if mode == "tag":
        return repo_url.rstrip("/"), tag
    if mode == "main":
        return repo_url.rstrip("/"), "main"
    fail(f"Unsupported RELEASE_INSTALL_MODE: {mode}")
    return "", ""


def find_toolbar_action():
    """Wait for the addon's own toolbar to appear; return its On/Off action."""
    import FreeCADGui as Gui
    from PySide.QtCore import QCoreApplication, QThread
    from PySide.QtWidgets import QToolBar

    from freecad.mcp_bridge.constants import DISPLAY_NAME, TOOLBAR_OBJECT_NAME

    action_label = "MCP Bridge On/Off"
    timeout_ms = int(env("RELEASE_INSTALL_TOOLBAR_TIMEOUT_MS", "15000"))
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


def trigger_toolbar_toggle(log_message: str) -> None:
    """Click the real "MCP Bridge On/Off" toolbar action, as a user would."""
    from PySide.QtCore import QCoreApplication

    action = find_toolbar_action()
    if action is None:
        fail(
            'Toolbar action "MCP Bridge On/Off" not found after startup '
            "(UI was not auto-injected)"
        )

    log(log_message)
    action.trigger()
    QCoreApplication.processEvents()


def check_bridge_off_at_startup() -> None:
    """Fail unless the bridge is off at session start: not running, its
    toolbar button not pressed, and nothing answering on its port."""
    import socket

    from freecad.mcp_bridge import bridge, config

    action = find_toolbar_action()
    if action is None:
        fail(
            'Toolbar action "MCP Bridge On/Off" not found after startup '
            "(UI was not auto-injected)"
        )
    port = config.port()
    try:
        socket.create_connection(("127.0.0.1", port), timeout=5).close()
    except OSError:
        answered = False
    else:
        answered = True
    if bridge.is_running() or action.isChecked() or answered:
        fail(
            "The bridge was on at session start, before any toolbar click "
            f"(running={bridge.is_running()}, "
            f"button pressed={action.isChecked()}, "
            f"port {port} answered={answered})"
        )
    log_notice("Bridge off at session start, until the toolbar click")


def check_listening_status() -> None:
    """Fail unless the status bar shows the address and port the bridge
    listens on."""
    import FreeCADGui as Gui

    from freecad.mcp_bridge import config

    expected = f"MCP Bridge: Listening on 127.0.0.1:{config.port()}"
    mw = Gui.getMainWindow()
    status = mw.statusBar().currentMessage() if mw else ""
    if expected not in status:
        fail(
            "Start toggle did not show the listening address in the status "
            f"bar. Expected substring {expected!r}, got: {status!r}"
        )
    log_notice(f"Status bar shows {expected!r}")


def start_bridge() -> None:
    """Start the HTTP server by triggering the real toolbar button."""
    from freecad.mcp_bridge import bridge

    if bridge.is_running():
        log("Bridge HTTP server already running")
        return

    trigger_toolbar_toggle('Triggering toolbar action "MCP Bridge On/Off" (start)')
    _drain_qt_events(1000)

    if not bridge.is_running():
        fail("Bridge HTTP server failed to start after toolbar click")
    log_notice("Bridge HTTP server started via toolbar toggle")


def schedule_main(main_fn) -> None:
    from PySide.QtCore import QTimer

    delay_ms = int(env("RELEASE_INSTALL_GUI_DELAY_MS", "3000"))
    QTimer.singleShot(delay_ms, main_fn)