# Arete troubleshooting — symptom, cause, fix

## Cards and rendering

| Symptom | Cause | Fix |
|---|---|---|
| Math shows as plain letters, or breaks where a backslash was | Double-escaped LaTeX (`\\alpha`) | Single backslashes in a `\|-` block (FORMAT.md). |
| Lines run together in Anki, fine in the preview | One newline = space in Anki (no nl2br) | Blank lines between paragraphs and before lists. |
| `- item` lines are not a list in Anki | No blank line before the list | Add one. |

## Obsidian plugin views

| Symptom | Cause | Fix |
|---|---|---|
| Local Graph: "These cards have no Arete IDs yet" | Draft cards; the graph is keyed by id | Sync the note. |
| Local Graph shows "Loading graph…" | First fetch asks the Python side (`arete graph export`, or `POST /graph` in server mode) | Wait; later opens are cached. |
| Dashboard numbers look wrong after a plugin reload | A view left from before the reload, still running old code | Close and reopen the view after reloading. |

## Agent tooling

| Symptom | Cause | Fix |
|---|---|---|
| New build not visible in Obsidian | No deploy step: the build lands in the repo, not the vault | Copy `main.js`, `styles.css`, `manifest.json` into `.obsidian/plugins/arete/`, then reload the plugin. |
