"""The architecture doc is generated, so it fails here rather than rotting quietly.

Before this existed, docs/ARCHITECTURE.md named domain/types.py,
application/pipeline.py, application/parser.py, interface/server.py and an "Apy"
adapter. None of them existed. A reader following the doc looked for files that
were not there.
"""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_architecture_doc_matches_the_package():
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "gen_architecture.py"), "--check"],
        capture_output=True,
        text=True,
        cwd=ROOT,
    )
    assert result.returncode == 0, result.stderr or result.stdout


def test_the_doc_names_no_module_that_is_missing():
    """Every `path/to.py` the doc mentions must exist under src/arete."""
    import re

    doc = (ROOT / "docs" / "ARCHITECTURE.md").read_text(encoding="utf-8")
    # Only path-like references. A bare leaf name is ambiguous and appears in the
    # generated tree, where the directory is already shown on its own line.
    named = set(re.findall(r"\b[\w][\w/]*/[\w/]+\.py\b", doc))
    missing = sorted(
        name
        for name in named
        if not (ROOT / "src" / "arete" / name).exists()
        and not (ROOT / name).exists()
        and not (ROOT / "scripts" / Path(name).name).exists()
        and "obsidian-plugin" not in name
    )
    assert not missing, f"the doc names modules that do not exist: {missing}"
