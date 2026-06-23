"""Install a tagged release through FreeCAD + Addon Manager.

Run inside a FreeCAD GUI process (first of two launches; CI restarts FreeCAD
before scripts/test_verify.py):

  freecad scripts/test_install.py

Environment:
  RELEASE_INSTALL_TAG     Git tag to install (default: v0.1.11)
  RELEASE_INSTALL_REPO    Repository URL
  RELEASE_INSTALL_NAME    Installed Mod folder name (default: freecad-mcp-bridge)
  RELEASE_INSTALL_MODE    tag | index_zip (default: tag)
  RELEASE_INSTALL_GUI_DELAY_MS  Delay before work starts (default: 3000)
"""

from __future__ import annotations

import os
import shutil
import sys

_script_dir = os.path.dirname(os.path.abspath(__file__))
if _script_dir not in sys.path:
    sys.path.insert(0, _script_dir)

import test_install_common as common


def _run() -> None:
    import FreeCAD as App

    tag = common.env("RELEASE_INSTALL_TAG", "v0.1.11")
    repo_url = common.env(
        "RELEASE_INSTALL_REPO", "https://github.com/CREATeNG/freecad-mcp-bridge"
    )
    addon_name = common.env("RELEASE_INSTALL_NAME", "freecad-mcp-bridge")
    mode = common.env("RELEASE_INSTALL_MODE", "tag").lower()
    expected_version = common.version_from_tag(tag)

    common.log(
        f"Install phase (mode={mode}, tag={tag}, repo={repo_url}, "
        f"user_data={App.getUserAppDataDir()})"
    )

    am_paths = common.addon_manager_paths()
    if not am_paths:
        common.fail("Addon Manager not found under user or installation Mod paths")

    for path in am_paths:
        if path not in sys.path:
            sys.path.insert(0, path)

    from Addon import Addon
    from addonmanager_installer import AddonInstaller, InstallationMethod

    mod_root = common.mod_dir()
    if os.path.isdir(mod_root):
        for entry in os.listdir(mod_root):
            if entry == addon_name or "mcp-bridge" in entry.lower():
                stale = os.path.join(mod_root, entry)
                common.log(f"Removing previous install at {stale}")
                shutil.rmtree(stale, ignore_errors=True)

    addon_url, addon_branch = common.build_addon_descriptor(
        addon_name, repo_url, tag, mode
    )
    addon = Addon(addon_name, addon_url, branch=addon_branch)
    installer = AddonInstaller(addon)
    if not installer.run(InstallationMethod.ANY):
        common.fail(f"AddonInstaller failed for {addon_name} ({mode}, {tag})")

    install_dir = common.wait_for_install_dir(addon_name, expected_version)
    if install_dir != common.install_dir():
        common.log(f"Resolved install dir: {install_dir}")

    common.verify_install_tree(install_dir, expected_version)
    common.log("Install passed; restart FreeCAD and run scripts/test_verify.py")
    common.quit_freecad(0)


common.LOG_PREFIX = "[test_install]"
common.schedule_main(_run)