import os
import re


def _mod_root() -> str:
    here = os.path.dirname(os.path.abspath(__file__))
    candidate = here
    for _ in range(6):
        if os.path.isfile(os.path.join(candidate, "package.xml")):
            return candidate
        parent = os.path.dirname(candidate)
        if parent == candidate:
            break
        candidate = parent
    return here


def icon_path() -> str:
    """Absolute path to the addon toolbar / command icon (SVG)."""
    mod_root = _mod_root()
    root_icon = os.path.join(mod_root, "Resources", "Icons", "icon.svg")
    if os.path.isfile(root_icon):
        return root_icon.replace("\\", "/")
    legacy = os.path.join(os.path.dirname(__file__), "Resources", "Icons", "icon.svg")
    return legacy.replace("\\", "/")


def preferences_ui_path() -> str:
    """Absolute path to the preferences page .ui file."""
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(here, "Resources", "Ui", "preferences.ui").replace("\\", "/")


def addon_version() -> str:
    """Read <version> from package.xml; '0.0.0' if it can't be read."""
    pkg = os.path.join(_mod_root(), "package.xml")
    try:
        with open(pkg, encoding="utf-8") as fh:
            text = fh.read()
    except OSError:
        return "0.0.0"
    match = re.search(r"<version>([^<]+)</version>", text)
    return match.group(1) if match else "0.0.0"