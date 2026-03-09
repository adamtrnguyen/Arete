import re
from typing import Any

import yaml  # type: ignore
import yaml.constructor
import yaml.error
import yaml.scanner  # type: ignore

from .common import sanitize
from .yaml import _LiteralDumper

# ---------- Math: Normalize to \( \) and \[ \] delimiters ----------


def convert_math_to_tex_delimiters(text: str) -> str:
    # 1) Blocks first
    block_dollars = re.compile(r"(?<!\\)\$\$(.*?)(?<!\\)\$\$", re.DOTALL)
    block_brackets = re.compile(r"\\\[\s*(.*?)\s*\\\]", re.DOTALL)
    block_bbcode = re.compile(r"\[\$\$\]\s*(.*?)\s*\[/\$\$\]", re.DOTALL)

    def to_block(m: re.Match) -> str:
        return r"\[" + m.group(1) + r"\]"

    out = text
    out = block_dollars.sub(to_block, out)
    out = block_bbcode.sub(to_block, out)
    out = block_brackets.sub(to_block, out)

    # 2) Inline next
    inline_dollar = re.compile(r"(?<!\\)\$(?!\$)(.*?)(?<!\\)\$", re.DOTALL)
    inline_paren = re.compile(r"\\\(\s*(.*?)\s*\\\)", re.DOTALL)
    inline_bbcode = re.compile(r"\[\$\]\s*(.*?)\s*\[/\$\]", re.DOTALL)

    def to_inline(m: re.Match) -> str:
        return r"\(" + m.group(1) + r"\)"

    out = inline_dollar.sub(to_inline, out)
    out = inline_bbcode.sub(to_inline, out)
    out = inline_paren.sub(to_inline, out)

    return out


# ---------- Frontmatter helpers ----------


def parse_frontmatter(md_text: str) -> tuple[dict[str, Any], str]:
    """Parse YAML frontmatter from markdown text.

    Uses line-by-line parsing instead of regex for reliability.
    """
    # Handle potential BOM (Byte Order Mark)
    md_text = md_text.lstrip("\ufeff")

    lines = md_text.split("\n")

    # Check for opening ---
    if not lines or lines[0].strip() != "---":
        return {}, md_text

    # Find closing ---
    yaml_end_line = None
    for i, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            yaml_end_line = i
            break

    if yaml_end_line is None:
        # No closing ---, return empty
        return {}, md_text

    # Extract YAML content and body
    yaml_lines = lines[1:yaml_end_line]
    body_lines = lines[yaml_end_line + 1 :]

    raw = "\n".join(yaml_lines)
    body = "\n".join(body_lines)

    # Fix tabs (common user error)
    if "\t" in raw:
        raw = raw.replace("\t", "  ")

    try:
        # Use our custom loader to get line numbers and handle duplicates
        meta = yaml.load(raw, Loader=UniqueKeyLoader) or {}

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


class UniqueKeyLoader(yaml.SafeLoader):
    """Custom YAML loader that forbids duplicate keys."""

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


def validate_frontmatter(md_text: str) -> dict[str, Any]:
    """Parse frontmatter and raise detailed exceptions on failure.

    Use line-by-line parsing instead of regex for reliability.
    Return the metadata dict if successful.
    """
    # Handle potential BOM (Byte Order Mark)
    md_text = md_text.lstrip("\ufeff")

    lines = md_text.split("\n")

    # Check for opening ---
    if not lines or lines[0].strip() != "---":
        return {}

    # Find closing ---
    yaml_end_line = None
    for i, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            yaml_end_line = i
            break

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
        return yaml.load(raw, Loader=UniqueKeyLoader) or {}
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
    lines = md_text.split("\n")

    if not lines or lines[0].strip() != "---":
        return None

    # Find closing ---
    char_pos = len(lines[0]) + 1  # +1 for newline
    for i, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            # Found closing ---
            start = len(lines[0]) + 1  # After opening ---\n
            yaml_content = "\n".join(lines[1:i])
            return yaml_content, start, start + len(yaml_content)
        char_pos += len(line) + 1  # +1 for newline

    return None


# ---------- Build apy editor-note text ----------
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

    # Split same-line metadata: "content  nid: '123'" → separate lines
    fm_text = re.sub(r"([^\n])\s+(nid|cid):", r"\1\n  \2:", fm_text)

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


def fix_mathjax_escapes(md_text: str) -> str:
    r"""Find double-quoted strings in frontmatter that contain common MathJax.

    escapes like \\in or \\mathbb and ensure they are double-escaped so
    PyYAML can parse them. This allows migrating broken files to |- blocks.
    """
    bounds = _extract_frontmatter_bounds(md_text)
    if bounds is None:
        return md_text

    original_fm, fm_start, fm_end = bounds
    # Simple heuristic for YAML 1.2 double-quote escapes: 0 abt nr vf e " / \ L P _
    valid_escapes = '0abtnrvfe"/\\ '

    def fix_line(line: str) -> str:
        # Match lines that look like key: "..." or - key: "..."
        # We use a regex to find the content between the first and last quote.
        import re

        match = re.search(r'^(\s*(?:-\s*)?[^:]+:\s*)"(.*)"\s*$', line)
        if match:
            prefix, val = match.groups()
            new_val = ""
            i = 0
            while i < len(val):
                if val[i] == "\\":
                    # Check if it's a valid escape sequence (like \n or \\)
                    if i + 1 < len(val) and val[i + 1] in valid_escapes:
                        new_val += "\\" + val[i + 1]
                        i += 2
                    else:
                        # Broken escape (like \i or \{). Escape the backslash.
                        new_val += "\\\\"
                        i += 1
                else:
                    new_val += val[i]
                    i += 1
            return f'{prefix}"{new_val}"'
        return line

    lines = original_fm.split("\n")
    fixed_lines = [fix_line(line) for line in lines]
    new_fm = "\n".join(fixed_lines)

    if new_fm != original_fm:
        return md_text[:fm_start] + new_fm + md_text[fm_end:]

    return md_text


def make_editor_note(
    model: str,
    deck: str,
    tags: list[str],
    fields: dict[str, str],
    nid: str | None = None,
    cid: str | None = None,
    markdown: bool = True,
) -> str:
    lines = []
    if nid:
        lines.append(f"nid: {nid}")
    if cid and not nid:
        lines.append(f"cid: {cid}")
    lines += [f"model: {model}", f"deck: {deck}"]
    if tags:
        lines.append(f"tags: {' '.join(tags)}")
    lines += [f"markdown: {'true' if markdown else 'false'}", "", "# Note", ""]
    mlow = model.lower()
    if mlow == "basic":
        f_list = ["Front", "Back"]
    elif mlow == "cloze":
        f_list = ["Text", "Back Extra"]
    else:
        f_list = sorted(fields.keys())

    # Always ensure _obsidian_source is included if present
    if "_obsidian_source" in fields and "_obsidian_source" not in f_list:
        f_list.append("_obsidian_source")

    for k in f_list:
        v = fields.get(k, "")
        if k == "Back Extra" and not v:
            v = fields.get("Extra", "")
        lines += [f"## {k}", sanitize(v), ""]
    return "\n".join(lines)
