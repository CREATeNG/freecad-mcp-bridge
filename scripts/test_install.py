"""Install the addon into FreeCAD's Mod folder, then check the tree.

Run inside a FreeCAD GUI process (first of two launches; CI restarts FreeCAD
before scripts/test_verify.py):

  freecad scripts/test_install.py

Environment:
  RELEASE_INSTALL_TAG     Git tag to install (default: v0.1.11)
  RELEASE_INSTALL_REPO    Repository URL
  RELEASE_INSTALL_NAME    Installed Mod folder name (default: freecad-mcp-bridge)
  RELEASE_INSTALL_MODE    tag | index_zip | main | branch | index | copy
                          (default: tag)
  RELEASE_INSTALL_BRANCH  Branch to install in branch mode
  RELEASE_INSTALL_GUI_DELAY_MS  Delay before work starts (default: 3000)
  RELEASE_INSTALL_WAIT_MS       Install completion timeout (default: 180000)
"""

from __future__ import annotations

import contextlib
import os
import shutil
import sys

_script_dir = os.path.dirname(os.path.abspath(__file__))
if _script_dir not in sys.path:
    sys.path.insert(0, _script_dir)

import test_install_common as common


class _ConsoleTee:
    """Pass Addon Manager console output on, and copy it to the CI log.

    Log-level lines ("Downloading ZIP file from ...") are otherwise hidden
    at FreeCAD's default console level.
    """

    def __init__(self, inner) -> None:
        self._inner = inner

    def __getattr__(self, name):
        return getattr(self._inner, name)

    def _copy(self, kind: str, message: str) -> None:
        common._write_ci_log(f"Addon Manager {kind}: {message.rstrip()}")

    def PrintLog(self, message: str) -> None:
        self._copy("log", message)
        self._inner.PrintLog(message)

    def PrintMessage(self, message: str) -> None:
        self._copy("message", message)
        self._inner.PrintMessage(message)

    def PrintWarning(self, message: str) -> None:
        self._copy("warning", message)
        self._inner.PrintWarning(message)

    def PrintError(self, message: str) -> None:
        self._copy("error", message)
        self._inner.PrintError(message)


@contextlib.contextmanager
def _addon_manager_console_to_ci_log():
    import addonmanager_freecad_interface as fci

    inner = fci.Console
    fci.Console = _ConsoleTee(inner)
    try:
        yield
    finally:
        fci.Console = inner


def _index_addon(addon_name: str):
    """Load the live Index catalog as the Addon Manager does; return our
    Addon and the version the Index states for it (None if unstated)."""
    import json

    import addonmanager_freecad_interface as fci
    import NetworkManager
    from AddonCatalog import AddonCatalog
    from addonmanager_workers_startup import CreateAddonListWorker

    cache_url = fci.Preferences().get("addon_catalog_cache_url")
    common.log(f"Loading the Index catalog from {cache_url}")
    NetworkManager.InitializeNetworkManager()
    try:
        text = CreateAddonListWorker.get_remote_cache("addon_catalog")
    except (RuntimeError, FileNotFoundError, ConnectionError) as exc:
        common.fail(f"Could not load the Index catalog: {exc}")
    if not text:
        common.fail("The Index catalog came back empty")
    catalog = AddonCatalog(json.loads(text))

    # A clean install takes the first compatible branch, as
    # CreateAddonListWorker.process_addon_cache does with no manifest.
    branches = catalog.get_available_branches(addon_name)
    if not branches:
        near = [
            addon_id
            for addon_id in catalog.get_all_addon_ids()
            if "mcp" in addon_id.lower()
        ]
        common.fail(
            f"No Index entry {addon_name!r} compatible with this FreeCAD "
            f"(ids containing 'mcp': {near})"
        )
    branch = branches[0]
    common.log(
        f"Index branches for {addon_name}: {branches}; using {branch!r}"
    )
    addon = catalog.get_addon_from_id(addon_name, branch)

    entry = next(
        e
        for e in catalog.get_catalog()[addon_name]
        if e.is_compatible()
        and (not branch or e.branch_display_name == branch)
    )
    common.log(
        f"Index entry: repository={entry.repository} "
        f"git_ref={entry.git_ref} zip_url={entry.zip_url}"
    )
    common.log(
        f"Index entry: sparse_cache={entry.sparse_cache} "
        f"relative_cache_path={entry.relative_cache_path!r} "
        f"last_update_time={entry.last_update_time!r}"
    )
    common.log(
        f"Addon from catalog: url={addon.url} branch={addon.branch} "
        f"relative_cache_path={addon.relative_cache_path!r}"
    )
    common.log(f"Addon.get_zip_url() returns {addon.get_zip_url()}")

    index_version = None
    if entry.metadata and entry.metadata.package_xml:
        index_version = common.version_from_package_text(
            entry.metadata.package_xml
        )
    common.log(
        "Index states version "
        f"{index_version or '(none: no package.xml in the catalog)'}"
    )
    return addon, index_version


def _run() -> None:
    import FreeCAD as App

    tag = common.env("RELEASE_INSTALL_TAG", "v0.1.11")
    repo_url = common.env(
        "RELEASE_INSTALL_REPO", "https://github.com/CREATeNG/freecad-mcp-bridge"
    )
    addon_name = common.env("RELEASE_INSTALL_NAME", "freecad-mcp-bridge")
    mode = common.env("RELEASE_INSTALL_MODE", "tag").lower()
    branch = common.env("RELEASE_INSTALL_BRANCH", "")
    expected_version = common.version_from_tag(tag)
    title = (
        "Install addon by copying into Mod"
        if mode == "copy"
        else "Install addon via Addon Manager"
    )
    common.log_group_start(title)

    common.log(
        f"Install phase (mode={mode}, tag={tag}, repo={repo_url}, "
        f"user_data={App.getUserAppDataDir()})"
    )
    common.log_opengl_info()

    mod_root = common.mod_dir()
    if os.path.isdir(mod_root):
        for entry in os.listdir(mod_root):
            if entry == addon_name or "mcp-bridge" in entry.lower():
                stale = os.path.join(mod_root, entry)
                common.log(f"Removing previous install at {stale}")
                shutil.rmtree(stale, ignore_errors=True)

    if mode == "copy":
        common.copy_addon_checkout(
            common.checkout_source(), common.install_dir()
        )
        common.verify_install_tree(common.install_dir(), expected_version)
        common.log_notice(
            "Install passed; restart FreeCAD and run scripts/test_verify.py"
        )
        common.log_group_end()
        common.quit_freecad(0)
        return

    am_paths = common.addon_manager_paths()
    if not am_paths:
        common.fail("Addon Manager not found under user or installation Mod paths")

    for path in am_paths:
        if path not in sys.path:
            sys.path.insert(0, path)

    from Addon import Addon
    from addonmanager_installer import AddonInstaller, InstallationMethod

    with _addon_manager_console_to_ci_log():
        if mode == "index":
            # The Index decides the version installed; the tag is not
            # checked against it.
            addon, expected_version = _index_addon(addon_name)
            common.log(f"Tag {tag} is not checked in index mode")
        else:
            addon_url, addon_branch = common.build_addon_descriptor(
                addon_name, repo_url, tag, mode, branch
            )
            common.log(f"Installing {addon_url} at {addon_branch}")
            addon = Addon(addon_name, addon_url, branch=addon_branch)
        installer = AddonInstaller(addon)
        method = installer._determine_install_method(
            addon.url.replace(os.path.sep, "/"), InstallationMethod.ANY
        )
        common.log(
            "Installer method for InstallationMethod.ANY: "
            f"{method.name if method else None}"
        )
        if not installer.run(InstallationMethod.ANY):
            common.fail(
                f"AddonInstaller failed for {addon_name} ({mode}, {tag})"
            )

    common._drain_qt_events(3000)

    timeout_ms = int(common.env("RELEASE_INSTALL_WAIT_MS", "180000"))
    install_dir = common.wait_for_install_dir(
        addon_name, expected_version, timeout_ms=timeout_ms
    )
    if install_dir != common.install_dir():
        common.log(f"Resolved install dir: {install_dir}")

    install_dir = common.flatten_install_dir(install_dir)
    common.verify_install_tree(install_dir, expected_version)
    common.log_notice(
        "Install passed; restart FreeCAD and run scripts/test_verify.py"
    )
    common.log_group_end()
    common.quit_freecad(0)


common.LOG_PREFIX = "[test_install]"
common.schedule_main(_run)
