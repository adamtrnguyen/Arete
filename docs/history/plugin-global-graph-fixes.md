# Plugin global graph fixes

Moved from a test and a source comment. Original wording in quotes.

- **Hover erased search dimming.** "The old mouseout wrote opacity 1 / 0.4 everywhere,
  erasing the search dimming." Guard:
  `obsidian-plugin/tests/presentation/views/GlobalGraphViewHover.test.ts`.
- **Fit to view.** "unfitted graph showed one corner; the old 'Fit to View' button only
  re-rendered." Where the rule lives: `obsidian-plugin/src/presentation/views/GlobalGraphView.ts`
  (fit to view).
