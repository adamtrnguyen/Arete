"""Write one version into every file that carries one.

Arete ships three artifacts from one repository, and the version lived in six
places. Before this script three of them were stale: the repository-root plugin
manifest said 2.3.0, the Anki add-on said 2.2.1, and a checked-in build artifact
said 1.4.0. `just release` updated none of them, so a release could publish a
plugin and an add-on that disagreed with the wheel beside them.

`pyproject.toml` is the source of truth. Everything else follows it.

    uv run --no-sync python scripts/sync_versions.py          # write
    uv run --no-sync python scripts/sync_versions.py --check  # fail on drift
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Files whose version is a JSON "version" key.
JSON_TARGETS = [
    ROOT / "manifest.json",  # repository root; a duplicate of the plugin manifest
    ROOT / "obsidian-plugin" / "manifest.json",  # what BRAT and Obsidian install
    ROOT / "obsidian-plugin" / "package.json",
    ROOT / "arete_ankiconnect" / "manifest.json",  # the Anki add-on
]


def source_version() -> str:
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'^version\s*=\s*"([^"]+)"', text, re.M)
    if not match:
        raise SystemExit("pyproject.toml has no version")
    return match.group(1)


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
        updated = re.sub(
            r'("version"\s*:\s*)"[^"]*"', rf'\g<1>"{version}"', text, count=1
        )
        if json.loads(updated).get("version") != version:
            raise SystemExit(f"{path}: could not rewrite the version safely")
        path.write_text(updated, encoding="utf-8")
    return f"{path.relative_to(ROOT)}: {current} -> {version}"


def sync_versions_json(version: str, check: bool) -> str | None:
    """The plugin's version -> minAppVersion map needs an entry for each release."""
    path = ROOT / "obsidian-plugin" / "versions.json"
    manifest = json.loads((ROOT / "obsidian-plugin" / "manifest.json").read_text(encoding="utf-8"))
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
    entry = sync_versions_json(version, check)
    if entry:
        changes.append(entry)

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
