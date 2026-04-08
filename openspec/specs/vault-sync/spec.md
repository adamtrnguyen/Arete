## Purpose

One-way synchronization bridge from Obsidian vault markdown files to Anki. Obsidian owns card content; Anki owns learning data. Handles card creation, updates, media, caching, pruning, and duplicate resolution.

## Requirements

### Requirement: One-way sync from Obsidian to Anki
The system SHALL sync cards defined in Obsidian vault markdown files to Anki. Obsidian is the source of truth for card content; Anki is the source of truth for learning data (reviews, intervals, FSRS metrics).

#### Scenario: Sync new cards
- **WHEN** a markdown file contains cards with `arete: true` frontmatter and no `anki.nid`
- **THEN** new notes are created in Anki with the specified fields, tags, deck, and model

#### Scenario: Sync updated cards
- **WHEN** a previously-synced card's content changes in the markdown file
- **THEN** the corresponding Anki note's fields, tags, and deck are updated without touching learning data

#### Scenario: Learning data preserved
- **WHEN** a card is updated via sync
- **THEN** Anki's review history, intervals, difficulty scores, and FSRS metrics remain untouched

### Requirement: Stable card identity via Arete IDs
The system SHALL assign a stable ULID-based ID (`arete_<ULID>`) to every card that lacks one during a pre-processing stage before sync. These IDs SHALL be persisted back to the markdown file.

#### Scenario: ID assignment on first sync
- **WHEN** a card in the `cards:` array has no `id` field
- **THEN** the system assigns an `arete_<ULID>` ID and writes it back to the file

#### Scenario: ID stability across syncs
- **WHEN** a card already has an `id` field
- **THEN** the system uses the existing ID without modification

### Requirement: NID/CID write-back
The system SHALL write Anki's note ID and card ID back into the markdown frontmatter after successful sync, stored in the `anki:` block.

#### Scenario: Write-back after first sync
- **WHEN** a new card is successfully created in Anki
- **THEN** the `anki.nid` and `anki.cid` fields are written to the card's frontmatter

### Requirement: Content-based caching
The system SHALL cache file metadata (mtime, size, content hash) in a SQLite database. Files that haven't changed SHALL skip parsing entirely.

#### Scenario: Unchanged file skips parsing
- **WHEN** a file's mtime and size match the cache and the content hash is unchanged
- **THEN** the system uses cached metadata without re-parsing

#### Scenario: Changed file triggers re-parse
- **WHEN** a file's content hash differs from the cached hash
- **THEN** the system re-parses the file and updates the cache

### Requirement: Self-healing duplicate detection
The system SHALL detect duplicate cards in Anki when creation fails or no NID exists, by normalizing and comparing first-field content within the same deck and model.

#### Scenario: Duplicate found during sync
- **WHEN** a card has no NID and a matching note exists in Anki (same deck, model, normalized first field)
- **THEN** the system adopts the existing note's NID and updates it instead of creating a duplicate

### Requirement: Async producer-consumer pipeline
The system SHALL parse files concurrently (configurable worker count) and sync to Anki in batches. AnkiDirect backend SHALL force sequential processing to prevent SQLite corruption.

#### Scenario: Concurrent sync with AnkiConnect
- **WHEN** syncing via AnkiConnect backend
- **THEN** multiple producers and consumers run in parallel

#### Scenario: Sequential sync with AnkiDirect
- **WHEN** syncing via AnkiDirect backend
- **THEN** a single consumer processes batches sequentially

### Requirement: Media sync
The system SHALL copy referenced images from the vault to Anki's media folder with content-hash-based filenames to prevent collisions. Wikilinks SHALL be resolved to Obsidian URIs.

#### Scenario: Image copied to Anki media
- **WHEN** a card references an image in markdown
- **THEN** the image is copied to Anki's media folder with a hash-based filename

### Requirement: Pruning orphaned cards
The system SHALL identify and delete Anki notes and decks that no longer have corresponding cards in the vault, only when the `--prune` flag is provided and the user confirms.

#### Scenario: Prune with confirmation
- **WHEN** the user runs sync with `--prune` and orphaned notes exist
- **THEN** the system lists orphans and prompts for confirmation before deleting

#### Scenario: Dry-run prune
- **WHEN** the user runs sync with `--prune --dry-run`
- **THEN** the system reports what would be deleted without modifying Anki

### Requirement: Tag merging
The system SHALL merge tags from markdown with existing Anki tags using set difference — adding new tags and removing tags no longer in markdown, without blind overwrite.

#### Scenario: Tag added in markdown
- **WHEN** a tag exists in the markdown but not in Anki
- **THEN** the tag is added to the Anki note

#### Scenario: Tag removed from markdown
- **WHEN** a tag exists in Anki but not in the markdown
- **THEN** the tag is removed from the Anki note
