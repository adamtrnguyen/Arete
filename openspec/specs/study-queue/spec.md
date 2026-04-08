## Purpose

Build dependency-aware study queues that ensure prerequisite cards are reviewed before dependents. Supports multiple queue algorithms (simple, static with weakness filtering, dynamic with frontier scoring) and creates filtered decks in Anki.

## Requirements

### Requirement: Dependency-aware study queue building
The system SHALL build study queues that respect card prerequisite ordering, ensuring prerequisite cards appear before dependents via topological sort.

#### Scenario: Simple prerequisite ordering
- **WHEN** card A requires card B, and both are due
- **THEN** card B appears before card A in the queue

#### Scenario: Depth-limited prerequisite collection
- **WHEN** the user specifies `--depth 2`
- **THEN** the system walks up to 2 prerequisite levels from due cards

### Requirement: Three queue algorithms
The system SHALL support three queue building strategies: simple (topo sort only), static (with weakness filtering), and dynamic (frontier-based scoring).

#### Scenario: Static queue filters weak prerequisites
- **WHEN** building a static queue
- **THEN** only prerequisites meeting weakness criteria (low FSRS stability, high lapses, few reviews) are included

#### Scenario: Dynamic queue prioritizes by reachability
- **WHEN** building a dynamic queue
- **THEN** cards that unlock more due descendants are prioritized (reachability weighted 2x)

### Requirement: Filtered deck creation in Anki
The system SHALL create a filtered deck in Anki (`Arete::Queue`) containing the ordered queue of cards. Cards SHALL remain in their home decks.

#### Scenario: Filtered deck created
- **WHEN** a queue is built successfully
- **THEN** a filtered deck is created in Anki with cards in topological order

#### Scenario: Dry-run mode
- **WHEN** the user specifies `--dry-run`
- **THEN** the system returns the queue plan without creating a deck

### Requirement: Queue size limits
The system SHALL enforce a maximum queue size (default 50), discarding lower-priority prerequisites first when the limit is exceeded.

#### Scenario: Queue exceeds max size
- **WHEN** the queue would contain more than 50 cards
- **THEN** the system caps at 50, dropping strong prerequisites first

### Requirement: Cross-deck prerequisite control
The system SHALL scope prerequisite collection to the specified deck by default. The `--cross-deck` flag SHALL allow pulling prerequisites from other decks.

#### Scenario: Default deck scoping
- **WHEN** building a queue for deck "CS::Algorithms" without `--cross-deck`
- **THEN** prerequisites from other decks are excluded

#### Scenario: Cross-deck enabled
- **WHEN** building a queue with `--cross-deck`
- **THEN** prerequisites from any deck are included

### Requirement: Cycle handling
The system SHALL detect circular dependencies and treat cyclic card groups as co-requisites, processing them as a single unit in topological sort.

#### Scenario: Cycle detected
- **WHEN** card A requires card B and card B requires card A
- **THEN** both cards are grouped as co-requisites and included in the queue without infinite loops
