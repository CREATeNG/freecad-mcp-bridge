"""Print the version FreeCAD's Addons Index catalog states for an addon.

Reads the catalog cache the Addon Manager loads (addon_catalog_cache.zip,
holding addon_catalog_cache.json) and the package.xml text the cache system
stored in the addon's first entry, the one a clean install takes. Prints the
version on stdout; logs the entry's git_ref and last_update_time on stderr.

Usage: index_catalog_version.py [ADDON_ID] [CATALOG_URL_OR_ZIP_PATH]
"""

import io
import json
import sys
import urllib.request
import zipfile

from test_install_common import version_from_package_text

ADDON_ID = "freecad-mcp-bridge"
CATALOG_URL = "https://addons.freecad.org/addon_catalog_cache.zip"
CATALOG_JSON = "addon_catalog_cache.json"


def load_catalog(source: str) -> dict:
    if source.startswith(("https://", "http://")):
        with urllib.request.urlopen(source, timeout=60) as response:
            data = response.read()
    else:
        with open(source, "rb") as handle:
            data = handle.read()
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        return json.loads(archive.read(CATALOG_JSON).decode("utf-8"))


def entry_version(catalog: dict, addon_id: str) -> tuple[str | None, dict]:
    """Return the version the addon's first catalog entry states, and the entry."""
    entries = catalog.get(addon_id) or []
    if not entries:
        raise LookupError(f"No catalog entry for {addon_id!r}")
    entry = entries[0]
    metadata = entry.get("metadata") or {}
    if isinstance(metadata, str):
        metadata = json.loads(metadata)
    package_xml = metadata.get("package_xml") or ""
    return version_from_package_text(package_xml) if package_xml else None, entry


def main() -> int:
    addon_id = sys.argv[1] if len(sys.argv) > 1 else ADDON_ID
    source = sys.argv[2] if len(sys.argv) > 2 else CATALOG_URL
    try:
        version, entry = entry_version(load_catalog(source), addon_id)
    except (OSError, KeyError, LookupError, ValueError, zipfile.BadZipFile) as exc:
        print(f"Could not read {addon_id!r} from the catalog at {source}: {exc}", file=sys.stderr)
        return 1
    print(
        f"Catalog entry for {addon_id}: git_ref={entry.get('git_ref')} "
        f"last_update_time={entry.get('last_update_time')!r} version={version}",
        file=sys.stderr,
    )
    if not version:
        print("The catalog entry states no version (no package.xml in its metadata)", file=sys.stderr)
        return 1
    print(version)
    return 0


if __name__ == "__main__":
    sys.exit(main())
