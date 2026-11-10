"""Write card ids into a note's frontmatter as line edits; every other byte stays.

A sync records three values in the vault: a card's Arete id (`id`), and the Anki note and card
ids (`anki.nid`, `anki.cid`). A missing key is inserted as a new line at the indentation of the
mapping it joins, after that mapping's last value. A key that holds another value gets the new
value in place, in the quoting style it had. Comments, quoting, key order, block scalars, blank
lines, line endings and the body keep their bytes, so removing the inserted lines gives back the
file as it was.

Locations come from PyYAML's node marks over the same text `parse_frontmatter` reads. A card
whose shape has no safe place for a line is refused: a flow mapping (`- {Front: a}`), a flow
`cards` list, an `anki` value that is not a block mapping, a value spanning several lines or
written as a block scalar, a merge key or an alias. The edited text is parsed again and must
give the frontmatter it had plus the new ids, or every card in the file is refused. A refusal
leaves the whole file as it was: the text returned is the text given.
"""

from __future__ import annotations

import bisect
import copy
import json
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from arete.application.utils.common import sanitize
from arete.application.utils.text import (
    _split_frontmatter,
    parse_frontmatter,
    scrub_internal_keys,
)

# A value written without quotes only when YAML reads it back as this same string.
_PLAIN = re.compile(r"[A-Za-z_][A-Za-z0-9_-]*")
# Each level of a nested `anki:` block is indented this much past its key.
_NEST = 2


@dataclass(frozen=True)
class CardIds:
    """The ids the card at `index` (0-based, in `cards`) must hold; None leaves that id alone."""

    index: int
    id: str | None = None
    nid: str | None = None
    cid: str | None = None


@dataclass
class IdWrite:
    """The text after the write, and why each refused card (0-based index) was refused.

    When `refused` is non-empty, `text` is the text that was given, unchanged.
    """

    text: str
    refused: dict[int, str] = field(default_factory=dict)


def read_exact(path: Path) -> str:
    """A file's text with its line endings as stored (`read_text` turns CRLF into LF)."""
    return path.read_bytes().decode("utf-8")


def write_exact(path: Path, text: str) -> None:
    """Write `text` with no newline translation, so a CRLF file stays CRLF."""
    path.write_bytes(text.encode("utf-8"))


class _Refused(Exception):
    pass


@dataclass
class _Insert:
    col: int  # the indentation of the first inserted line; deeper inserts at a row go first
    order: int
    lines: list[str]


class _Frontmatter:
    """The frontmatter rows of one file and the node marks that locate keys in them."""

    def __init__(self, rows: list[str], root: yaml.Node):
        self.rows = rows
        # `parse_frontmatter` reads tabs as two spaces; marks index that text.
        self.read = [r.replace("\t", "  ") for r in rows]
        self.starts: list[int] = []
        offset = 0
        for r in self.read:
            self.starts.append(offset)
            offset += len(r) + 1
        self.shared = _shared_nodes(root)
        self.replaces: dict[int, tuple[int, int, str]] = {}
        self.inserts: dict[int, list[_Insert]] = {}

    def pos(self, mark: Any) -> tuple[int, int]:
        """(row, column) of a mark, found from its character index.

        The mark's own line number counts breaks (a lone CR, U+2028) that rows do not.
        """
        row = bisect.bisect_right(self.starts, mark.index) - 1
        return row, mark.index - self.starts[row]

    @staticmethod
    def _leaf(node: yaml.Node) -> yaml.Node:
        """The node whose text ends a block collection's text: its last value, recursively."""
        while (
            isinstance(node, yaml.MappingNode | yaml.SequenceNode)
            and not node.flow_style
            and node.value
        ):
            last = node.value[-1]
            node = last[1] if isinstance(node, yaml.MappingNode) else last
        return node

    def _end_row(self, node: yaml.Node) -> int:
        row, col = self.pos(node.end_mark)
        if not self.read[row][:col].strip(" "):
            row -= 1  # the mark sits in the indentation of the row after the value
        return row

    def last_row(self, node: yaml.Node) -> int:
        """The last row of a node's text, without the blank rows a clipped block scalar drops."""
        node = self._leaf(node)
        row = self._end_row(node)
        if isinstance(node, yaml.ScalarNode) and node.style in ("|", ">"):
            head_row = self.pos(node.start_mark)[0]
            if "+" not in self._header(node):
                while row > head_row and self.rows[row].rstrip("\r") == "":
                    row -= 1
        return row

    def insert_row(self, mapping: yaml.MappingNode) -> int:
        """The row after which a new key of `mapping` goes: after its last value.

        A `|` or `>` scalar that is not stripped (`-`) and whose last line is the
        frontmatter's last line has no line break after it; a line below would give it one.
        The key then goes above the mapping's last key, which a mapping of one key lacks.
        """
        leaf = self._leaf(mapping)
        if (
            isinstance(leaf, yaml.ScalarNode)
            and leaf.style in ("|", ">")
            and self._end_row(leaf) == len(self.rows) - 1
            and not self.rows[-1].endswith("\r")  # a CRLF row ends in a break of its own
            and "-" not in self._header(leaf)
        ):
            if len(mapping.value) < 2:
                raise _Refused("its only key holds a block scalar that ends the frontmatter")
            return self.pos(mapping.value[-1][0].start_mark)[0] - 1
        return self.last_row(mapping)

    def _header(self, node: yaml.ScalarNode) -> str:
        """A block scalar's indicators (`|-`, `>2+`), without any comment after them."""
        row, col = self.pos(node.start_mark)
        words = self.read[row][col:].split()
        return words[0].split("#")[0] if words else ""

    @staticmethod
    def keys(mapping: yaml.MappingNode) -> dict[str, tuple[yaml.Node, yaml.Node]]:
        """A mapping's (key node, value node) by key text.

        A merge key (`<<`) never gets here: `parse_frontmatter` rejects it, and a file it
        rejects is never edited.
        """
        return {k.value: (k, v) for k, v in mapping.value if isinstance(k, yaml.ScalarNode)}

    def insert(self, after_row: int, col: int, lines: list[str]) -> None:
        at = self.inserts.setdefault(after_row, [])
        at.append(_Insert(col, len(at), lines))

    def set_value(
        self,
        mapping: yaml.MappingNode,
        keys: dict[str, tuple[yaml.Node, yaml.Node]],
        name: str,
        value: str,
        current: Any,
    ) -> None:
        """Hold `name: value` in a block mapping; nothing when it already does."""
        if name not in keys:
            col = self.pos(mapping.value[0][0].start_mark)[1]
            self.insert(self.insert_row(mapping), col, [f"{' ' * col}{name}: {_scalar(value)}"])
            return
        if sanitize(current).strip() == value:
            return
        node = keys[name][1]
        if (
            not isinstance(node, yaml.ScalarNode)
            or node.style in ("|", ">")
            or id(node) in self.shared
        ):
            raise _Refused(f"`{name}` does not hold a one-line value")
        row, start = self.pos(node.start_mark)
        end_row, end = self.pos(node.end_mark)
        if end_row != row:
            raise _Refused(f"`{name}` spans several lines")
        if self.rows[row] != self.read[row]:
            raise _Refused(f"the line holding `{name}` has a tab")
        if row in self.replaces:
            raise _Refused(f"`{name}` shares its line with another id")
        text = _scalar(value, node.style if end > start else "insert")
        self.replaces[row] = (start, end, text if end > start else f" {text}")

    def edited_rows(self) -> list[str]:
        out: list[str] = []
        for i, row in enumerate(self.rows):
            if i in self.replaces:
                start, end, text = self.replaces[i]
                row = row[:start] + text + row[end:]
            out.append(row)
            eol = "\r" if row.endswith("\r") else ""
            for ins in sorted(self.inserts.get(i, []), key=lambda x: (-x.col, x.order)):
                out.extend(line + eol for line in ins.lines)
        return out


def _shared_nodes(root: yaml.Node) -> set[int]:
    """Ids of the nodes reached more than once: an anchor with an alias to it."""
    seen: set[int] = set()
    shared: set[int] = set()
    stack = [root]
    while stack:
        node = stack.pop()
        if id(node) in seen:
            shared.add(id(node))
            continue
        seen.add(id(node))
        if isinstance(node, yaml.MappingNode):
            stack.extend(n for pair in node.value for n in pair)
        elif isinstance(node, yaml.SequenceNode):
            stack.extend(node.value)
    return shared


def _scalar(value: str, style: str | None = "insert") -> str:
    """`value` as a YAML scalar: in `style` when replacing, else plain only when that is safe."""
    if style == '"':
        return json.dumps(value)
    if style != "'":
        safe = _PLAIN.fullmatch(value) is not None and yaml.safe_load(value) == value
        if safe or (style is None and value.isdigit()):
            return value
    return "'" + value.replace("'", "''") + "'"


def _norm(meta: Any) -> Any:
    """Frontmatter without line numbers, its ids compared as the parser reads them."""
    meta = scrub_internal_keys(meta)
    cards = meta.get("cards") if isinstance(meta, dict) else None
    for card in cards if isinstance(cards, list) else []:
        if not isinstance(card, dict):
            continue
        if "id" in card:
            card["id"] = sanitize(card["id"]).strip()
        anki = card.get("anki")
        if isinstance(anki, dict):
            for k in ("nid", "cid"):
                if k in anki:
                    anki[k] = sanitize(anki[k]).strip()
    return meta


def _expected(meta: dict[str, Any], edits: Sequence[CardIds]) -> dict[str, Any]:
    out = copy.deepcopy(meta)
    for e in edits:
        card = out["cards"][e.index]
        if e.id:
            card["id"] = e.id
        if e.nid or e.cid:
            if not isinstance(card.get("anki"), dict):
                card["anki"] = {}
            if e.nid:
                card["anki"]["nid"] = e.nid
            if e.cid:
                card["anki"]["cid"] = e.cid
    return out


def _differs(held: Any, value: str | None) -> bool:
    return value is not None and sanitize(held).strip() != value


def _plan(fm: _Frontmatter, seq: yaml.SequenceNode, card: Any, e: CardIds) -> None:
    if not 0 <= e.index < len(seq.value):
        raise _Refused("there is no card at this position")
    node = seq.value[e.index]
    if not isinstance(node, yaml.MappingNode) or not isinstance(card, dict):
        raise _Refused("the card is not a mapping")
    held: dict[str, Any] = card["anki"] if isinstance(card.get("anki"), dict) else {}
    if not (
        _differs(card.get("id"), e.id)
        or _differs(held.get("nid"), e.nid)
        or _differs(held.get("cid"), e.cid)
    ):
        return  # the card holds these ids already, whatever its shape
    if node.flow_style:
        raise _Refused("the card is written in flow style (`{...}`); write it as a block mapping")
    if id(node) in fm.shared:
        raise _Refused("the card is an anchor that an alias repeats")
    keys = fm.keys(node)
    if e.id:
        fm.set_value(node, keys, "id", e.id, card.get("id"))
    wanted = [(k, v) for k, v in (("nid", e.nid), ("cid", e.cid)) if v and _differs(held.get(k), v)]
    if wanted:
        _plan_anki(fm, node, keys, held, wanted)


def _plan_anki(
    fm: _Frontmatter,
    node: yaml.MappingNode,
    keys: dict[str, tuple[yaml.Node, yaml.Node]],
    held: dict[str, Any],
    wanted: list[tuple[str, str]],
) -> None:
    """Hold `wanted` (nid, cid) in the card's `anki` block, adding the block if it is missing."""
    if "anki" not in keys:
        col = fm.pos(node.value[0][0].start_mark)[1]
        inner = " " * (col + _NEST)
        lines = [f"{' ' * col}anki:"] + [f"{inner}{k}: {_scalar(v)}" for k, v in wanted]
        fm.insert(fm.insert_row(node), col, lines)
        return
    key, anki = keys["anki"]
    if id(anki) in fm.shared:
        raise _Refused("`anki` is an anchor that an alias repeats")
    if isinstance(anki, yaml.MappingNode) and not anki.flow_style:
        akeys = fm.keys(anki)
        for k, v in wanted:
            fm.set_value(anki, akeys, k, v, held.get(k))
        return
    if isinstance(anki, yaml.ScalarNode) and anki.style is None and anki.value == "":
        col = fm.pos(key.start_mark)[1] + _NEST
        fm.insert(fm.last_row(anki), col, [f"{' ' * col}{k}: {_scalar(v)}" for k, v in wanted])
        return
    raise _Refused("`anki` is not a block mapping")


@dataclass
class _Located:
    meta: dict[str, Any]
    body: str
    cards: list[Any]
    fm: _Frontmatter
    seq: yaml.SequenceNode
    bom: int
    lines: list[str]
    end: int


def _locate(text: str) -> _Located:
    """The parse of `text` and the nodes of its `cards`, or _Refused for the whole file."""
    meta, body = parse_frontmatter(text)
    cards = meta.get("cards") if meta and "__yaml_error__" not in meta else None
    if not isinstance(cards, list):
        raise _Refused("the frontmatter has no readable `cards` list")
    bom = len(text) - len(text.lstrip("\ufeff"))
    split = _split_frontmatter(text[bom:])
    lines, end = split if split is not None else ([], None)
    if end is None:
        raise _Refused("the frontmatter has no closing `---`")
    rows = lines[1:end]
    try:
        root = yaml.compose("\n".join(r.replace("\t", "  ") for r in rows), yaml.SafeLoader)
    except yaml.YAMLError as err:
        raise _Refused(f"the frontmatter does not parse for editing: {err}") from err
    if not isinstance(root, yaml.MappingNode) or root.flow_style:
        raise _Refused("the frontmatter is not a block mapping")
    fm = _Frontmatter(rows, root)
    seq = fm.keys(root).get("cards", (None, None))[1]
    if not isinstance(seq, yaml.SequenceNode) or len(seq.value) != len(cards):
        raise _Refused("`cards` could not be located in the text")
    if seq.flow_style:
        raise _Refused("`cards` is written in flow style (`[...]`)")
    return _Located(meta, body, cards, fm, seq, bom, lines, end)


def write_card_ids(text: str, edits: Sequence[CardIds]) -> IdWrite:
    """`text` with each card holding the ids `edits` give it, and no other byte changed.

    An id the card already holds is left as written, so writing a card's own ids is a no-op.
    """
    edits = [e for e in edits if e.id or e.nid or e.cid]
    if not edits:
        return IdWrite(text)
    try:
        at = _locate(text)
    except _Refused as err:
        return IdWrite(text, {e.index: str(err) for e in edits})

    refused: dict[int, str] = {}
    for e in edits:
        card = at.cards[e.index] if 0 <= e.index < len(at.cards) else None
        try:
            _plan(at.fm, at.seq, card, e)
        except _Refused as err:
            refused[e.index] = str(err)
    if refused or not (at.fm.replaces or at.fm.inserts):
        return IdWrite(text, refused)

    rows = at.fm.edited_rows()
    new_text = text[: at.bom] + "\n".join([at.lines[0], *rows, *at.lines[at.end :]])
    new_meta, new_body = parse_frontmatter(new_text)
    if new_body != at.body or _norm(new_meta) != _norm(_expected(at.meta, edits)):
        why = "the edited frontmatter did not read back as the same cards plus their ids"
        return IdWrite(text, {e.index: why for e in edits})
    return IdWrite(new_text)
