import os


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