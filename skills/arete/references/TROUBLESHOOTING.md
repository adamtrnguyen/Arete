# Arete troubleshooting — symptom, cause, fix

Found while driving the live vault on 2026-09-25. Versions matter: the fixes are in
arete / plugin **2.5.0 or later** and vault-mcp `f94dc61`.

## Cards and rendering

| Symptom | Cause | Fix |
|---|---|---|
| Math hover or Anki shows `mid r ▶ in mathbbQ` | Double-escaped LaTeX (`\\mid`) | Single backslashes in a `\|-` block (FORMAT.md). |
| A `▶` inside the math hover | **Your cursor** — Obsidian marks the caret position in the formula | Nothing to fix. |
| Lines run together in Anki, fine in the preview | One newline = space in Anki (no nl2br) | Blank lines between paragraphs and before lists. |
| `- item` lines are not a list in Anki | No blank line before the list | Add one. |
| Cloze preview shows raw `{{c1::…}}` | Plugin < 2.5.0 (Mustache cannot render `{{cloze:}}`) | Update the plugin. |

## Obsidian plugin views

| Symptom | Cause | Fix |
|---|---|---|
| Local Graph: "These cards have no Arete IDs yet" | Draft cards; the graph is keyed by id | Sync the note. |
| Local Graph shows "Loading graph…" for ~3 s | First fetch asks the Python side (`arete graph export`) | Wait; later opens are cached. |
| Global Graph blank | Plugin < 2.5.0 had no stylesheet for it | Update the plugin. |
| Dashboard numbers impossible after a plugin reload (difficulty 92.8) | A view left from before the reload, still running old code | Close and reopen the view (`detachLeavesOfType`) after reloading. |
| Card Editor one edit behind an agent's edit | Plugin < 2.5.0 reloaded before Obsidian re-parsed | Update the plugin. |
| Due badge "20721d ago" | Plugin < 2.5.0 read Anki day numbers as timestamps | Update the plugin. |

## Agent tooling

| Symptom | Cause | Fix |
|---|---|---|
| vault MCP tool: bare "Error executing tool X" | vault-mcp before `f94dc61` hid reasons and lost CLI replies | Restart the agent session so it runs the fixed server. |
| `obsidian` CLI: "unable to find Obsidian" while it runs | Another Obsidian instance took `~/.obsidian-cli.sock` and deleted it on exit | Quit and reopen Obsidian. Never start a second instance with `--user-data-dir`. |
| Obsidian CLI `eval` returns nothing | The CLI exits when its stdin hits EOF | Keep stdin open: `(sleep 5) \| obsidian eval "code=…"`. |
| Screenshot cropped by selector is offset | vault-mcp crop ignores Retina pixel ratio | Take the full window. |
| New build not visible in Obsidian | No deploy step: the build lands in the repo, not the vault | Copy `main.js`, `styles.css`, `manifest.json` into `.obsidian/plugins/arete/`, then reload the plugin. |
