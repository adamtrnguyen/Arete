---
name: gui-test
description: Test and measure the Arete Obsidian plugin in a running Obsidian - build and deploy a worktree's plugin, reload it, open each view, check behaviour with DOM probes and screenshots, time editor and view work, and profile CPU, heap and listeners. ALWAYS use this skill when asked to GUI-test, screenshot-check, profile or optimise the Arete plugin in a live Obsidian, or to confirm a plugin change works in the real app rather than only in jest.
---

# GUI test: the Arete plugin in a live Obsidian

Jest proves the logic. This proves the plugin in the real app: what is drawn, where, and at
what cost. Every result is one of **seen** (observed in the app), **measured** (timed or
counted there) or **traced** (read in code only). Report which.

Tools: the `obsidian` CLI (`eval`, `open`, `dev:errors`, `dev:cdp`) and, when available, a
vault MCP server's `obsidian_*` tools (workspace, command, click, focus tab, screenshot,
plugin reload). Run one CLI call per shell call.

## 1. Safety first

The Obsidian you test in is someone's working vault, and a sync writes to their Anki.

| Never run in a test | Why |
|---|---|
| `arete:arete-sync`, `arete:arete-sync-current-file`, `arete:arete-sync-prune`, `arete:arete-sync-force-all` | write to Anki |
| `arete:arete-graph-clear` | edits note frontmatter |
| a click on the Arete status-bar item or the "Sync to Anki (Arete)" ribbon icon | runs a sync |

Read-only commands: `arete:arete-sync-stats`, `arete:arete-check-integrity`,
`arete:arete-reload-note-types`, and the three `arete-open-*` view commands plus
`arete:open-yaml-editor`. `arete:arete-check-file` only reads, but the result window it
opens can run a fix that rewrites the note: close it without using the fix.

- **Record state before you start:** the full workspace (leaf ids, active tab, sidebar
  state), the plugin settings in `data.json` without `statsCache`, and
  `git -C "<vault>" status --short`.
- **Back up** `main.js`, `styles.css`, `manifest.json` and `data.json` from
  `<vault>/.obsidian/plugins/arete/` to `/tmp` before deploying.
- **Allowed but real side effects:** expanding a dashboard row rewrites `data.json` one
  second later; the first save by a new build drops settings keys it does not know;
  "Sync Stats" and every plugin load start a Python process in `project_root`.
- **Restore** what you changed (expansion, sidebar size, tabs you opened) and check
  `git status` again at the end. Only the plugin's `main.js` and `styles.css` should differ.
- **Do not bring the window forward** or change settings on disk. Flip a setting in memory
  for one check and put it back.
- **Checks that edit a note or need Anki** require a disposable vault. Without one, report
  them as not run.

## 2. Build, deploy, reload

1. `npm --prefix <worktree>/obsidian-plugin run build`
2. Copy `main.js`, `styles.css` and `manifest.json` into `<vault>/.obsidian/plugins/arete/`.
3. Reload the plugin, then run `obsidian dev:errors`.
4. **Prove the deploy:** `shasum` the three files in the worktree and in the vault.
5. **Prove the load:** eval that `app.plugins.plugins.arete` exists.
6. **Prove views run new code:** eval `leaf.view.plugin === app.plugins.plugins.arete` for
   each open Arete view. Check it; do not assume either way.

A view in a hidden sidebar tab is a placeholder after a reload and loads when revealed. A
reload resets view-local state (editor mode, 2D/3D toggle).

## 3. Open views and reach states

| View | How | Where it opens |
|---|---|---|
| Dashboard | `arete:arete-open-dashboard` | new tab, right sidebar |
| Local graph | `arete:arete-open-local-graph` | right sidebar; first open builds the vault graph |
| Global graph | `arete:arete-open-global-graph` | new main-area tab, becomes active |
| Card Editor | `arete:open-yaml-editor` | reuses the existing leaf |

- **A view of your own:** eval `app.workspace.getRightLeaf(true).setViewState({type})`, and
  `leaf.detach()` when done, rather than driving the user's leaf.
- **Open a note:** `obsidian open path="<rel>"` opens in the ACTIVE leaf. After a split the
  new leaf is not active: focus it first.
- **Selectors:** take class names and `title` attributes from the plugin source at the
  commit under test. Dashboard deck headers have no class, and graph nodes are `g.node`
  identified through d3's `__data__`.
- **Clicks** need exactly one visible match. Scope with `.workspace-leaf.mod-active`, or
  call `el.click()` inside an eval.

## 4. Measure

Keep each probe in a file under `/tmp` and run it with `obsidian eval code="$(cat probe.js)"`.
Log every expression. Async IIFEs are awaited, and console output produced during an eval
is printed above its result, which is how to read plugin logs.

| What | How |
|---|---|
| Cursor move cost | `cm = leaf.view.editor.cm`; time `cm.dispatch({selection:{anchor}})` plus `cm.measure()`; count gutter DOM changes with a MutationObserver |
| Scroll cost | `cm.scrollDOM.scrollTop += 600; cm.measure()` |
| View open time | run the command inside an eval and poll for the first content selector |
| CPU attribution | `obsidian dev:cdp method=Profiler.enable`, `Profiler.start`, run the probe, `Profiler.stop > profile.json`, then `node scripts/profile_summary.cjs profile.json` |
| Heap, DOM, listeners | `bash scripts/heap_listeners.sh <label>` before and after an open/close cycle |
| Hover styling | dispatch `mouseenter`; read computed styles after 300 ms, past the CSS transition |
| d3 node hover | `dispatchEvent(new MouseEvent('mouseover', {bubbles:true, clientX, clientY}))`; compare the tooltip's `getBoundingClientRect()` with `innerWidth` |
| 3D frames drawn | `view.graph3D.renderer().info.render.frame`; `graphData().nodes.length` is 0 after the graph is destroyed |
| Card preview | the preview iframe is same-origin: read `iframe.contentDocument` |

Run each timing at least twice and report both numbers. Take "before" numbers on the old
build before deploying the new one.

## 5. Traps

- **Hidden window:** when `document.visibilityState` is `hidden`, animation frames stop and
  timers are throttled. Check it before any timing, frame count or layout check, and report
  those as not checked rather than waiting.
- **A hung eval cannot be cancelled.** Its promise keeps running inside Obsidian and can act
  later. Never `await` a timer or frame in a probe without checking visibility first.
- **A command returns before its async work ends.** Poll a state, not a fixed delay.
- **Stale drawing:** a revealed view shows its previous drawing for about 100 ms. Match the
  expected content, not "any node".
- **Screenshots:** full-window screenshots are reliable; crops by selector can be offset or
  clipped. An element with `display: block` can still be off screen: check its rect.
- **`obsidian dev:errors`** reports thrown errors only, not `console.error` or warnings.
- **The gutter is positional:** CodeMirror rebuilds gutter elements when the active line
  changes, so that rebuild is not a regression.
- **All events are synthetic.** Trusted mouse and keyboard input, and drag and drop, cannot
  be tested this way.

## 6. Fix and report

- **One fix per commit**, each with a jest test that fails without it, and `just qa` green.
- **Redeploy and re-run the failing check** on the fixed build before calling it fixed.
- **Report** a table of check, result (pass, fail then fixed, could not check) and the
  evidence with its kind (seen, measured, traced); then the deployed commit, what changed in
  the vault, and what was restored.
