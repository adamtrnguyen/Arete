import hashlib
import json
import re
import unicodedata
from typing import Any

import yaml  # type: ignore
import yaml.constructor
import yaml.error
import yaml.scanner  # type: ignore

from .common import sanitize
from .yaml import _LiteralDumper

# ---------- Filename normalization ----------


def normalize_filename(name: str) -> str:
    """Normalize a filename string to NFC for cross-platform comparison.

    macOS APFS/HFS+ stores filenames in NFD (decomposed: e.g., 'é' = 'e' + U+0301),
    while user-typed strings in YAML frontmatter are typically NFC (composed: 'é' = U+00E9).
    Both forms render identically but compare as unequal at the byte level.

    Apply this to any string used as a key for filename-based lookup so that
    `Cramér-Rao Lower Bound` typed by the user matches the on-disk filename
    regardless of the form macOS hands back via pathlib.
    """
    return unicodedata.normalize("NFC", name)


# ---------- Math: Normalize to \( \) and \[ \] delimiters ----------


# ---------- Frontmatter helpers ----------


def _split_frontmatter(md_text: str) -> tuple[list[str], int | None] | None:
    """Split into lines and find the frontmatter's closing `---`, line by line, no regex.

    None when the first line is not `---`. Otherwise (lines, end): lines[end] is the
    closing `---`, or end is None when the frontmatter never closes.
    """
    lines = md_text.split("\n")
    if lines[0].strip() != "---":
        return None
    for i, line in enumerate(lines[1:], start=1):
        if line.rstrip() == "---":  # column 0: an indented --- is a rule inside `Back: |`
            return lines, i
    return lines, None


def parse_frontmatter(md_text: str) -> tuple[dict[str, Any], str]:
    """Parse YAML frontmatter from markdown text.

    Uses line-by-line parsing instead of regex for reliability.
    """
    # Handle potential BOM (Byte Order Mark)
    md_text = md_text.lstrip("\ufeff")

    split = _split_frontmatter(md_text)
    if split is None:
        return {}, md_text
    lines, yaml_end_line = split
    if yaml_end_line is None:
        return {}, md_text

    raw = "\n".join(lines[1:yaml_end_line])
    body = "\n".join(lines[yaml_end_line + 1 :])

    # Fix tabs (common user error)
    if "\t" in raw:
        raw = raw.replace("\t", "  ")

    try:
        # Use our custom loader to get line numbers and handle duplicates
        meta = _load_yaml(raw) or {}

        # Add offset to __line__ to make it absolute (account for opening ---)
        offset = 1  # Opening --- is line 0, YAML starts at line 1

        def _add_offset(d):
            if isinstance(d, dict):
                if "__line__" in d:
                    d["__line__"] += offset
                for v in d.values():
                    _add_offset(v)
            elif isinstance(d, list):
                for v in d:
                    _add_offset(v)

        _add_offset(meta)

    except Exception as e:
        return {"__yaml_error__": str(e)}, md_text

    return meta, body


class _UniqueKeyConstructor(yaml.constructor.SafeConstructor):
    """Forbid duplicate keys and record each mapping's 1-based line as `__line__`."""

    def construct_mapping(self, node, deep=False):
        mapping = set()
        for key_node, _ in node.value:
            key = self.construct_object(key_node, deep=deep)
            if key in mapping:
                raise yaml.constructor.ConstructorError(
                    None, None, f"found duplicate key '{key}'", key_node.start_mark
                )
            mapping.add(key)

        result = super().construct_mapping(node, deep)
        if isinstance(result, dict):
            # Inject line number (1-based)
            result["__line__"] = node.start_mark.line + 1
        return result


class UniqueKeyLoader(_UniqueKeyConstructor, yaml.SafeLoader):
    """Pure-Python YAML loader that forbids duplicate keys. Its errors are the ones shown."""


# libyaml parses about 9x faster than UniqueKeyLoader, and the two must give one answer.
# libyaml words errors differently and its marks carry no snippet, so a document it rejects
# is parsed again by UniqueKeyLoader, whose exception is the one raised. The inputs the
# two read differently go straight to UniqueKeyLoader: a BOM inside the text, a `#` right
# after a block scalar header (`|#`), an explicit tag (`a: !` is null in PyYAML, '' in
# libyaml), and a `?` inside a plain scalar in a flow collection (`[what?]`).
_FastUniqueKeyLoader: type | None = (
    type("_FastUniqueKeyLoader", (_UniqueKeyConstructor, yaml.CSafeLoader), {})
    if getattr(yaml, "__with_libyaml__", False)
    else None
)
_PURE_PYTHON_ONLY = re.compile(r"\ufeff|[|>][-+0-9]*#|(?:^|[\s\[{,:])!", re.MULTILINE)


def _flow_scalar_has_question_mark(root: Any) -> bool:
    """Whether a scalar inside a flow collection contains `?` (libyaml reads it, PyYAML not)."""
    stack = [(root, False)]
    seen: set[int] = set()
    while stack:
        node, in_flow = stack.pop()
        if id(node) in seen:
            continue
        seen.add(id(node))
        if isinstance(node, yaml.ScalarNode):
            if in_flow and "?" in node.value:
                return True
            continue
        in_flow = in_flow or bool(node.flow_style)
        if isinstance(node, yaml.MappingNode):
            stack.extend((n, in_flow) for pair in node.value for n in pair)
        else:
            stack.extend((n, in_flow) for n in node.value)
    return False


def _load_yaml(raw: str) -> Any:
    if _FastUniqueKeyLoader is None or _PURE_PYTHON_ONLY.search(raw) is not None:
        return yaml.load(raw, Loader=UniqueKeyLoader)
    loader = _FastUniqueKeyLoader(raw)
    try:
        node = loader.get_single_node()
        if node is None:
            return None
        if not _flow_scalar_has_question_mark(node):
            return loader.construct_document(node)
    except Exception:  # noqa: S110 - UniqueKeyLoader below raises the error users see
        pass
    finally:
        loader.dispose()
    return yaml.load(raw, Loader=UniqueKeyLoader)


def validate_frontmatter(md_text: str) -> dict[str, Any]:
    """Parse frontmatter and raise detailed exceptions on failure.

    Use line-by-line parsing instead of regex for reliability.
    Return the metadata dict if successful.
    """
    # Handle potential BOM (Byte Order Mark)
    md_text = md_text.lstrip("\ufeff")

    split = _split_frontmatter(md_text)
    if split is None:
        return {}
    lines, yaml_end_line = split

    if yaml_end_line is None:
        # Unclosed frontmatter
        err = yaml.scanner.ScannerError(
            problem="Unclosed YAML frontmatter. Found starting '---' but no closing '---'.",
            problem_mark=yaml.error.Mark("name", 0, 1, -1, "", 0),
        )
        raise err

    # Extract YAML content
    yaml_lines = lines[1:yaml_end_line]
    raw = "\n".join(yaml_lines)

    # Offset for line numbers (opening --- is line 0)
    offset = 1

    # Strict validation: Explicitly forbid tabs anywhere in frontmatter
    if "\t" in raw:
        tab_index = raw.find("\t")
        lines_before_tab = raw[:tab_index].count("\n")
        line = lines_before_tab + 1

        err = yaml.scanner.ScannerError(
            problem="found character '\\t' that cannot start any token",
            problem_mark=yaml.error.Mark("name", 0, line + offset, -1, "", 0),
        )
        raise err

    try:
        return _load_yaml(raw) or {}
    except yaml.YAMLError as e:
        # Adjust the line number in the exception to match the file
        if hasattr(e, "problem_mark"):
            e.problem_mark.line += offset  # type: ignore
        raise e


def scrub_internal_keys(d: Any) -> Any:
    """Recursively remove keys starting with __."""
    if isinstance(d, dict):
        return {k: scrub_internal_keys(v) for k, v in d.items() if not k.startswith("__")}
    elif isinstance(d, list):
        return [scrub_internal_keys(v) for v in d]
    return d


def rebuild_markdown_with_frontmatter(meta: dict[str, Any], body: str) -> str:
    clean_meta = scrub_internal_keys(meta)
    yaml_text = yaml.dump(
        clean_meta,
        Dumper=_LiteralDumper,
        sort_keys=False,
        allow_unicode=True,
        default_flow_style=False,
        width=10**9,
    )
    return f"---\n{yaml_text}---\n{body}"


def _extract_frontmatter_bounds(md_text: str) -> tuple[str, int, int] | None:
    """Extract frontmatter content and its character bounds from markdown text.

    Returns (yaml_content, start_index, end_index) or None if no frontmatter.
    Uses line-by-line parsing (no regex).
    """
    split = _split_frontmatter(md_text)
    if split is None:
        return None
    lines, end = split
    if end is None:
        return None
    start = len(lines[0]) + 1  # After opening ---\n
    yaml_content = "\n".join(lines[1:end])
    return yaml_content, start, start + len(yaml_content)


def _preprocess_frontmatter_text(fm_text: str) -> str:
    """Minimal text fixes to make broken YAML parseable.

    Only fixes syntax issues that would prevent ``yaml.load`` from succeeding.
    All *formatting* (block scalars, indentation, quote styles) is handled by
    the ``_LiteralDumper`` round-trip in ``apply_fixes``.
    """
    # Tabs → spaces (YAML spec forbids tabs for indentation)
    if "\t" in fm_text:
        fm_text = fm_text.replace("\t", "  ")

    # Quote bare template tags: {{title}} → "{{title}}" (YAML flow-mapping syntax)
    fm_text = re.sub(r"(:\s*)\{\{(.*?)\}\}", r'\1"{{\2}}"', fm_text)

    # Fix invalid block scalar start: `| '...` → `'...`
    fm_text = re.sub(r"(\|[-+]?)\s*(['\"])", r"\2", fm_text)

    return fm_text


def _fix_frontmatter_dict(meta: dict) -> dict:
    """Structural fixes on the parsed frontmatter dict."""
    # Add missing cards list when deck/model present
    has_deck_or_model = "deck" in meta or "model" in meta
    if has_deck_or_model and "cards" not in meta:
        meta["cards"] = []
    return meta


def apply_fixes(md_text: str) -> str:
    """Fix common frontmatter issues via parse → fix dict → dump.

    Phase 1: Minimal text pre-processing (make broken YAML parseable).
    Phase 2: Parse to dict, fix structure, dump with _LiteralDumper
             (handles block scalars, indentation, quote styles automatically).
    """
    # Strip leading empty frontmatter blocks
    md_text = md_text.lstrip()
    while re.match(r"^---\s*\n(\s*\n)*---\s*\n", md_text):
        md_text = re.sub(r"^---\s*\n(\s*\n)*---\s*\n", "", md_text, count=1)
        md_text = md_text.lstrip()

    bounds = _extract_frontmatter_bounds(md_text)
    if bounds is None:
        return md_text

    original_fm, fm_start, fm_end = bounds

    # Phase 1: text-level pre-processing
    preprocessed_fm = _preprocess_frontmatter_text(original_fm)

    # Apply text fixes if needed
    if preprocessed_fm != original_fm:
        md_text = md_text[:fm_start] + preprocessed_fm + md_text[fm_end:]

    # Phase 2: parse → fix dict → dump
    meta, body = parse_frontmatter(md_text)

    if not meta or "__yaml_error__" in meta:
        return md_text  # Can't parse; return with text fixes only

    meta = _fix_frontmatter_dict(meta)

    return rebuild_markdown_with_frontmatter(meta, body)


def card_content_hash(
    model: str,
    deck: str,
    tags: list[str],
    fields: dict[str, str],
    nid: str | None = None,
    cid: str | None = None,
) -> str:
    """The md5 of everything Anki receives for a card, which sync compares to the cache.

    Changing what goes in changes every hash, but an ordinary sync skips files whose
    stat is unchanged, so old hashes only go stale; --force or --clear-cache re-sends all.
    """
    payload = {
        "model": model,
        "deck": deck,
        "tags": tags,
        "fields": {k: sanitize(v) for k, v in sorted(fields.items())},
        "nid": nid,
        "cid": None if nid else cid,
    }
    return hashlib.md5(json.dumps(payload, ensure_ascii=False).encode("utf-8")).hexdigest()
