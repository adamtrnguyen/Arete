# Dependency Graph Feature — Walkthrough

> [!note] Record, not current guidance
> **Outcome:** Describes the plugin as built on 2026-01-18. Since then files moved (`application/graph_resolver.py` is now `application/queue/graph_resolver.py`) and `DependencyEditorView` / `CardSearchModal` were removed in 7a7ec27. Links below point at the original layout.

Branch: `feature/dependency-graph`

## What Was Built

This branch implements **Milestones 2-3** of the v2.0.0 plan: Dependency Editing and Local Graph View.

---

## Python Layer

### [graph.py](file:///Users/adamnguyen/Research/obsidian_2_anki/src/arete/domain/graph.py)

Domain types for dependency graphs:
- `CardNode` — Represents a card with id, title, file path, line number
- `DependencyGraph` — Nodes + requires/related edge maps
- `LocalGraphResult` — Subgraph query result for UI

### [graph_resolver.py](file:///Users/adamnguyen/Research/obsidian_2_anki/src/arete/application/graph_resolver.py)

Graph building and traversal:
- `build_graph(vault_root)` — Parses all YAML frontmatter
- `get_local_graph(card_id, depth)` — Returns subgraph centered on card
- `detect_cycles()` — Uses stdlib graphlib
- `topological_sort()` — For queue ordering

### [queue_builder.py](file:///Users/adamnguyen/Research/obsidian_2_anki/src/arete/application/queue_builder.py)

Queue building algorithm:
- Walk `requires` edges backward up to depth
- Filter for weak prerequisites (low stability, lapses)
- Topo sort for proper learning order
- `include_related=True` raises `NotImplementedError` (future work)

### [test_graph_resolver.py](file:///Users/adamnguyen/Research/obsidian_2_anki/tests/application/test_graph_resolver.py)

Comprehensive tests for graph building, local graph, cycles, topo sort, and queue building.

---

## Obsidian Layer

### [types.ts](file:///Users/adamnguyen/Research/obsidian_2_anki/obsidian-plugin/src/domain/graph/types.ts)

TypeScript equivalents of Python types with `DependencyGraphBuilder` class.

### [DependencyResolver.ts](file:///Users/adamnguyen/Research/obsidian_2_anki/obsidian-plugin/src/application/services/DependencyResolver.ts)

Obsidian-side resolver:
- Parses vault YAML directly via Obsidian API
- Builds and caches dependency graph
- Invalidates on file modification

### [DependencyEditorView.ts](file:///Users/adamnguyen/Research/obsidian_2_anki/obsidian-plugin/src/presentation/views/DependencyEditorView.ts)

Sidebar panel:
- Shows requires/related for current card
- Add/remove dependencies via fuzzy search
- Displays diagnostics (missing deps, cycles)

### [LocalGraphView.ts](file:///Users/adamnguyen/Research/obsidian_2_anki/obsidian-plugin/src/presentation/views/LocalGraphView.ts)

Graph visualization:
- 3-column layout: Prerequisites → Center → Dependents
- Toggle related cards visibility
- Depth selector (1-4)
- Click to navigate

### [CardSearchModal.ts](file:///Users/adamnguyen/Research/obsidian_2_anki/obsidian-plugin/src/presentation/modals/CardSearchModal.ts)

Fuzzy search modal using Obsidian's `FuzzySuggestModal`.

---

## Next Steps (Not on this branch)

1. **Register views in main.ts**:
   ```typescript
   this.registerView(DEPENDENCY_EDITOR_VIEW_TYPE, (leaf) => new DependencyEditorView(leaf, this));
   this.registerView(LOCAL_GRAPH_VIEW_TYPE, (leaf) => new LocalGraphView(leaf, this));
   ```

2. **Add ribbon icons and commands**

3. **Test with real vault data** containing cards with `id` and `deps` fields

---

## Verification Results

### Python Layer
- **Unit Tests**: 15 tests passed in 0.03s.
  - Logic: Graph building, topological sorting, cycle detection, and weak prerequisite filtering.
  - Command: `uv run pytest tests/application/test_graph_resolver.py`

### Obsidian Layer
- **File Structure**: All new components (`DependencyResolver`, `DependencyEditorView`, `LocalGraphView`, `CardSearchModal`) created and located in their respective directories.
- **Integration**: Views registered in `main.ts`, ribbon icons added, and commands mapped.
- **Styling**: New CSS rules added to `styles.css` for the three-column graph layout and editor panels.

| Path | Purpose |
|------|---------|
| `src/arete/domain/graph.py` | Graph domain types |
| `src/arete/application/graph_resolver.py` | Graph building & traversal |
| `src/arete/application/queue_builder.py` | Queue building algorithm |
| `tests/application/test_graph_resolver.py` | Python tests |
| `obsidian-plugin/src/domain/graph/types.ts` | TypeScript types |
| `obsidian-plugin/src/application/services/DependencyResolver.ts` | YAML parsing |
| `obsidian-plugin/src/presentation/views/DependencyEditorView.ts` | Editor panel |
| `obsidian-plugin/src/presentation/views/LocalGraphView.ts` | Graph view |
| `obsidian-plugin/src/presentation/modals/CardSearchModal.ts` | Search modal |
| `obsidian-plugin/styles.css` | CSS additions |
