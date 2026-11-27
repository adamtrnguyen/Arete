"""Write one version into every file that carries one.

Arete ships three artifacts from one repository. `pyproject.toml` is the single
source of truth for the version, and `obsidian-plugin/manifest.json` is the single
source of truth for the plugin manifest metadata mirrored at the repository root.

    uv run --no-sync python scripts/sync_versions.py          # write
    uv run --no-sync python scripts/sync_versions.py --check  # fail on drift
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PLUGIN_MANIFEST = ROOT / "obsidian-plugin" / "manifest.json"
ROOT_MANIFEST = ROOT / "manifest.json"

# Files whose version is a JSON "version" key.
JSON_TARGETS = [
    PLUGIN_MANIFEST,  # what BRAT and Obsidian install
    ROOT / "obsidian-plugin" / "package.json",
    ROOT / "arete_ankiconnect" / "manifest.json",  # the Anki add-on
]


def source_version() -> str:
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'^version\s*=\s*"([^"]+)"', text, re.M)
    if not match:
        raise SystemExit("pyproject.toml has no version")
    return match.group(1)


def _rewrite_version(text: str, version: str, path: Path) -> str:
    updated = re.sub(r'("version"\s*:\s*)"[^"]*"', rf'\g<1>"{version}"', text, count=1)
    if json.loads(updated).get("version") != version:
        raise SystemExit(f"{path}: could not rewrite the version safely")
    return updated


def sync_json(path: Path, version: str, check: bool) -> str | None:
    """Return a description of the change, or None when already correct."""
    if not path.exists():
        return None
    text = path.read_text(encoding="utf-8")
    data = json.loads(text)
    current = data.get("version")
    if current == version:
        return None
    if not check:
        # Rewrite only the version line, so indentation and key order survive.
        path.write_text(_rewrite_version(text, version, path), encoding="utf-8")
    return f"{path.relative_to(ROOT)}: {current} -> {version}"


def sync_root_manifest(version: str, check: bool) -> str | None:
    """Keep the repository-root manifest.json identical to obsidian-plugin/manifest.json."""
    plugin_text = PLUGIN_MANIFEST.read_text(encoding="utf-8")
    expected_text = (
        plugin_text
        if json.loads(plugin_text).get("version") == version
        else _rewrite_version(plugin_text, version, PLUGIN_MANIFEST)
    )
    if ROOT_MANIFEST.exists():
        root_text = ROOT_MANIFEST.read_text(encoding="utf-8")
        if json.loads(root_text) == json.loads(expected_text):
            return None
    if not check:
        ROOT_MANIFEST.write_text(expected_text, encoding="utf-8")
    return "manifest.json: sync with obsidian-plugin/manifest.json"


def sync_versions_json(version: str, check: bool) -> str | None:
    """The plugin's version -> minAppVersion map needs an entry for each release."""
    path = ROOT / "obsidian-plugin" / "versions.json"
    manifest = json.loads(PLUGIN_MANIFEST.read_text(encoding="utf-8"))
    min_app = manifest.get("minAppVersion", "0.15.0")
    data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    if data.get(version) == min_app:
        return None
    if not check:
        path.write_text(
            json.dumps({version: min_app, **data}, indent="\t") + "\n", encoding="utf-8"
        )
    return f"obsidian-plugin/versions.json: add {version} -> {min_app}"


def main() -> int:
    check = "--check" in sys.argv
    version = source_version()
    changes = [c for c in (sync_json(p, version, check) for p in JSON_TARGETS) if c]
    for extra in (sync_root_manifest(version, check), sync_versions_json(version, check)):
        if extra:
            changes.append(extra)

    if not changes:
        print(f"every version is {version}")
        return 0
    for change in changes:
        print(("would update " if check else "updated ") + change)
    if check:
        print(
            "\nversions disagree. Run: uv run --no-sync python scripts/sync_versions.py",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
