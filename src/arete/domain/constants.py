"""Centralized constants for the Arete application.

All magic numbers and configuration defaults live here so every layer
imports from a single source of truth.
"""

# ---------- Pipeline ----------
CONSUMER_BATCH_SIZE = 50
# The markdown-to-Anki renderer's version, stored with each cached render. A render from
# another version is not the vault's fields: the card is rendered again. Raise it with
# any change to what the renderer produces.
RENDER_VERSION = 1

# ---------- AnkiConnect / HTTP ----------
REQUEST_TIMEOUT = 30.0
RESPONSIVENESS_TIMEOUT = 2.0
CHUNK_SIZE = 500
SYNC_CONCURRENCY = 8  # Max concurrent card syncs within a batch

# ---------- FSRS ----------
FSRS_DIFFICULTY_SCALE = 10.0

# ---------- GUI Browse Polling ----------
BROWSE_POLL_ATTEMPTS = 40
BROWSE_POLL_INTERVAL = 0.5  # seconds
BROWSE_INITIAL_DELAY = 1.0  # seconds

# ---------- Queue Builder ----------
DEFAULT_PREREQ_DEPTH = 2
DEFAULT_MAX_QUEUE_SIZE = 50

# ---------- Media Discovery ----------
MEDIA_DIR_NAMES = ["attachments", "attach", "assets", ".assets", "images", "img", "media"]

# ---------- Card Schema ----------
PRIMARY_FIELD_NAMES = {
    "Front",
    "Text",
    "Question",
    "Term",
    "Expression",
}
