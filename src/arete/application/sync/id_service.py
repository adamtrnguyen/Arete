"""Service for managing stable Arete IDs for cards."""

from ulid import ULID


def generate_arete_id() -> str:
    """Generate a stable Arete ID using ULID."""
    return f"arete_{ULID()}"
