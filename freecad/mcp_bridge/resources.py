import os


def icon_path() -> str:
    """Absolute path to the addon toolbar / command icon (SVG)."""
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(here, "Resources", "Icons", "icon.svg").replace("\\", "/")