## Purpose

Construct and analyze a directed dependency graph from vault card prerequisites. Provides cycle detection, health diagnostics, topological sorting, and subgraph extraction for the queue system.

## Requirements

### Requirement: Dependency graph construction from vault
The system SHALL build a directed graph from vault markdown files, where edges represent `deps.requires` relationships between cards. References SHALL resolve by Arete ID or by file basename (all cards in that file).

#### Scenario: Direct ID reference
- **WHEN** card A's `deps.requires` contains `arete_01JH8Y...`
- **THEN** an edge is created from the referenced card to card A

#### Scenario: Basename reference
- **WHEN** card A's `deps.requires` contains `algebra`
- **THEN** edges are created from all cards in `algebra.md` to card A

#### Scenario: Unresolved reference
- **WHEN** a `deps.requires` value doesn't match any known card or file
- **THEN** the reference is tracked as unresolved and reported in diagnostics

### Requirement: Graph health analysis
The system SHALL detect cycles, isolated nodes, and connected components in the dependency graph.

#### Scenario: Cycle detection
- **WHEN** the graph contains a strongly connected component
- **THEN** the system reports it as a cycle with the involved cards

#### Scenario: Isolated node detection
- **WHEN** a card has no incoming or outgoing dependency edges
- **THEN** the system reports it as isolated

### Requirement: Topological sort with tiebreaking
The system SHALL topologically sort cards with depth-based tiebreaking (more fundamental cards first), falling back to input order for equal depth.

#### Scenario: Depth-based ordering
- **WHEN** two cards at the same topological level have different graph depths
- **THEN** the card with greater depth (more fundamental) appears first
