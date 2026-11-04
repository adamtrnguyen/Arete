# Study-queue and dependency-graph fixes (2026-09-25)

From the 2026-09-25 bug hunt: commits `bfbdaf8` (queue, G1-G4, G8), `aeb5382` (graph,
G6-G7) and `ceb0c16` (plugin reads the graph from Python, G5). Each fix
came with a test that failed before it. The source comments keep the rule and cite the
label (G1 to G8); this file keeps what was wrong. Moved here from the comments in
`application/queue/builder.py` and `application/queue/graph_resolver.py` on 2026-11-04.

- **G1, cycles.** A cycle anywhere made `topological_sort` return set order, so
  prerequisites could follow their dependents, differently on each run (it failed under
  2 of 3 hash seeds). Cycles now collapse into groups (`nx.condensation`); only a cycle's
  own cards share a position.
- **G2, prerequisite walk.** Collection was depth-first with one shared visited set, so a
  card first reached by a long path was never expanded from a shorter one, and its own
  prerequisites inside the depth limit were lost. Now breadth-first by shortest distance.
- **G3, the `max_cards` cap.** It kept the alphabetically-first prerequisites and
  dropped the rest silently. Due cards are never dropped; prerequisites fill the room
  nearest-first and the rest are reported as `dropped_prereqs`.
- **G4, deck filter.** It was a substring test ("Math" kept "Applied Mathematics"), and a
  matching file deck overrode the card's own deck, the reverse of sync. Now exact or
  subdeck, and the card's deck wins.
- **G5/G6, references.** The Obsidian plugin resolved references itself and drifted from
  Python; it now reads `GraphExport`. `requires: Algebra` (a scalar) and `[2024]` (YAML
  reads it as an int) were dropped without a word; both are now read, and anything that
  cannot be a reference is reported.
- **G7, duplicate ids.** A second card with the same Arete id overwrote the first
  silently. The first is kept and the duplicate recorded in `duplicate_ids`.
- **G8, graph health on a deck.** Analysed on the filtered graph, a card whose
  prerequisite sat in another deck lost that edge and was reported isolated. The full
  graph is analysed and only what touches the deck is reported.
