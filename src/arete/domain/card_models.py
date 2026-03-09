"""Pydantic v2 models for Arete card frontmatter validation.

These models validate the structure of YAML frontmatter in Obsidian markdown files
before constructing AnkiNote dataclasses. They belong in the domain layer because
they define what an Arete card IS.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# ---------------------------------------------------------------------------
# Nested blocks
# ---------------------------------------------------------------------------


class AnkiBlock(BaseModel):
    """Nested ``anki:`` block for Anki-assigned note/card IDs."""

    nid: str | None = None
    cid: str | None = None

    @field_validator("nid", "cid", mode="before")
    @classmethod
    def coerce_to_str(cls, v: Any) -> str | None:
        if v is None:
            return None
        return str(v).strip() or None


class DepsBlock(BaseModel):
    """Card dependency tracking (``deps:`` block)."""

    requires: list[str] = Field(default_factory=list)
    related: list[str] = Field(default_factory=list)

    @field_validator("requires", "related", mode="before")
    @classmethod
    def coerce_list(cls, v: Any) -> list[str]:
        if v is None:
            return []
        if isinstance(v, str):
            return [v]
        if isinstance(v, list):
            return [str(x) for x in v if x is not None]
        return [str(v)]


# ---------------------------------------------------------------------------
# Reserved field names excluded from custom model content
# ---------------------------------------------------------------------------

RESERVED_CARD_FIELDS: set[str] = {
    "model",
    "deck",
    "tags",
    "markdown",
    "anki",
    "id",
    "deps",
}


# ---------------------------------------------------------------------------
# Card models
# ---------------------------------------------------------------------------


class BasicCard(BaseModel):
    """Basic card model (Front/Back)."""

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    model: str = "Basic"
    Front: str = ""
    Back: str = ""
    id: str | None = None
    deck: str | None = None
    tags: list[str] | None = None
    anki: AnkiBlock | None = None
    deps: DepsBlock | None = None
    markdown: bool = True

    @model_validator(mode="before")
    @classmethod
    def normalize_case_insensitive_fields(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        # Normalize Front/Back from case-insensitive keys
        front = data.get("Front") or data.get("front") or ""
        back = data.get("Back") or data.get("back") or ""
        data["Front"] = str(front).strip() if front else ""
        data["Back"] = str(back).strip() if back else ""
        return data

    @model_validator(mode="after")
    def require_front_and_back(self) -> BasicCard:
        if not self.Front:
            raise ValueError("Basic card requires a non-empty 'Front' field")
        if not self.Back:
            raise ValueError("Basic card requires a non-empty 'Back' field")
        return self


class ClozeCard(BaseModel):
    """Cloze card model (Text / Back Extra)."""

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    model: str = "Cloze"
    Text: str = ""
    Back_Extra: str | None = None
    id: str | None = None
    deck: str | None = None
    tags: list[str] | None = None
    anki: AnkiBlock | None = None
    deps: DepsBlock | None = None
    markdown: bool = True

    @model_validator(mode="before")
    @classmethod
    def normalize_case_insensitive_fields(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        # Normalize Text from case-insensitive keys
        text = data.get("Text") or data.get("text") or ""
        data["Text"] = str(text).strip() if text else ""
        # Back Extra has multiple aliases: "Back Extra", "back extra", "Extra", "extra"
        extra = (
            data.get("Back Extra")
            or data.get("back extra")
            or data.get("Back_Extra")
            or data.get("Extra")
            or data.get("extra")
        )
        data["Back_Extra"] = str(extra).strip() if extra else None
        return data

    @model_validator(mode="after")
    def require_text(self) -> ClozeCard:
        if not self.Text:
            raise ValueError("Cloze card requires a non-empty 'Text' field")
        return self


class CustomCard(BaseModel):
    """Custom card model with arbitrary fields.

    Content fields are accessible via ``model_extra``. Reserved fields
    (model, deck, tags, id, anki, deps, etc.) are excluded from content.
    """

    model_config = ConfigDict(extra="allow")

    model: str
    id: str | None = None
    deck: str | None = None
    tags: list[str] | None = None
    anki: AnkiBlock | None = None
    deps: DepsBlock | None = None
    markdown: bool = True

    @model_validator(mode="before")
    @classmethod
    def normalize_fields(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        return data

    @property
    def content_fields(self) -> dict[str, Any]:
        """Return only the content fields (excludes reserved metadata)."""
        if self.model_extra is None:
            return {}
        return {
            k: v
            for k, v in self.model_extra.items()
            if k not in RESERVED_CARD_FIELDS and not k.startswith("__")
        }


# ---------------------------------------------------------------------------
# File-level metadata
# ---------------------------------------------------------------------------


class AreteFileMetadata(BaseModel):
    """Top-level frontmatter for an Arete-enabled markdown file."""

    model_config = ConfigDict(extra="allow")

    arete: bool = False
    deck: str | None = None
    model: str = "Basic"
    tags: list[str] = Field(default_factory=list)
    cards: list[dict[str, Any]] = Field(default_factory=list)

    @field_validator("tags", mode="before")
    @classmethod
    def coerce_tags(cls, v: Any) -> list[str]:
        """Coerce tags from string, list, or None."""
        if v is None:
            return []
        if isinstance(v, str):
            return [v.strip()] if v.strip() else []
        if isinstance(v, list):
            return [str(t).strip() for t in v if t is not None and str(t).strip()]
        return [str(v)]

    @model_validator(mode="after")
    def validate_deck_requirements(self) -> AreteFileMetadata:
        """If arete=True or cards are present, deck must be specified."""
        if self.arete and not self.deck:
            raise ValueError("File marked 'arete: true' requires a 'deck' field")
        if self.cards and not self.deck:
            raise ValueError("Cards are present but no 'deck' field specified")
        return self


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

CardModel = BasicCard | ClozeCard | CustomCard


def parse_card(raw: dict[str, Any], default_model: str = "Basic") -> CardModel:
    """Parse a raw card dict into the appropriate Pydantic model.

    Dispatches based on the ``model`` field (case-insensitive).
    Falls back to *default_model* when ``model`` is not specified.
    """
    model_name = str(raw.get("model", default_model)).strip()
    model_lower = model_name.lower()

    # Ensure the model field is set for the Pydantic constructor
    data = dict(raw)
    data["model"] = model_name

    if model_lower == "basic":
        return BasicCard.model_validate(data)
    elif model_lower == "cloze":
        return ClozeCard.model_validate(data)
    else:
        return CustomCard.model_validate(data)


def parse_file_metadata(raw: dict[str, Any]) -> AreteFileMetadata:
    """Validate top-level frontmatter and return an ``AreteFileMetadata`` instance."""
    return AreteFileMetadata.model_validate(raw)
