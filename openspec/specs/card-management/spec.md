## Purpose

Card data model, validation, rendering, and direct management operations. Covers card models (Basic/Cloze/Custom), deck hierarchy, field overrides, suspension, content reading, and math protection during sync.

## Requirements

### Requirement: Card model support
The system SHALL support Basic (Front/Back), Cloze (Text), and Custom (arbitrary fields) card models. Each model SHALL enforce its required fields during parsing.

#### Scenario: Basic card validation
- **WHEN** a card specifies model "Basic"
- **THEN** it MUST have Front and Back fields

#### Scenario: Cloze card validation
- **WHEN** a card specifies model "Cloze"
- **THEN** it MUST have a Text field

#### Scenario: Custom card validation
- **WHEN** a card specifies a custom model
- **THEN** it MUST have at least one field

### Requirement: Deck hierarchy support
The system SHALL support Anki's deck hierarchy using `::` separator notation (e.g., `Subject::Chapter::Topic`).

#### Scenario: Nested deck assignment
- **WHEN** a card specifies `deck: "CS::Algorithms::Sort"`
- **THEN** the card is placed in the nested deck hierarchy in Anki

### Requirement: Card-level overrides
The system SHALL allow individual cards to override file-level defaults for model, deck, and tags.

#### Scenario: Card overrides file deck
- **WHEN** a file has `deck: "CS"` and a card has `deck: "Math"`
- **THEN** that card syncs to the "Math" deck

### Requirement: Card suspension control
The system SHALL allow suspending and unsuspending cards in Anki by card ID via CLI, MCP, and HTTP interfaces.

#### Scenario: Suspend a card
- **WHEN** the user suspends card with CID 12345
- **THEN** the card is suspended in Anki

#### Scenario: Unsuspend a card
- **WHEN** the user unsuspends card with CID 12345
- **THEN** the card is unsuspended in Anki

### Requirement: Card content reading from vault
The system SHALL allow reading card content directly from vault markdown files without requiring Anki to be running.

#### Scenario: Read cards by concept
- **WHEN** the user queries for cards related to concept "recursion"
- **THEN** the system searches vault files and returns matching card content

### Requirement: Math protection during rendering
The system SHALL preserve LaTeX/MathJax blocks (`$$...$$` and `$...$`) during markdown-to-HTML conversion.

#### Scenario: LaTeX preserved in sync
- **WHEN** a card field contains `$$\int_0^1 x\,dx$$`
- **THEN** the LaTeX is preserved in the Anki note without being mangled by markdown processing
