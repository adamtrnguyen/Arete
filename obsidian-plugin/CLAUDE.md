# Arete Obsidian plugin: style rules

Arete should look and behave like part of Obsidian. Anything the user sees or touches uses
Obsidian's components, CSS variables, wording and interaction conventions. Where Obsidian has
a convention, it beats the plugin's own habit, however common that habit is.

Rules marked (inferred) have no Obsidian documentation page behind them. "Enforced by: review"
means no check exists yet.

### Native look

| # | Rule | Why | Enforced by |
|---|---|---|---|
| N1 | Write UI text in sentence case: commands, setting names, headings, buttons, tooltips, notices, view titles. Proper nouns (Arete, Anki, Obsidian, FSRS) keep their capitals. | Obsidian's guideline. | review |
| N2 | Build settings from `Setting` rows. Headings: `new Setting(el).setName('Sync').setHeading()`. No top-level heading; general settings come first with no heading; no "settings" in a heading. | Obsidian's guideline; matches core settings. | ESLint (`createEl('h*')` in `settings/`, `modals/`) |
| N3 | Set no default hotkeys and build no hotkey UI. Users bind keys in Settings → Hotkeys. | Obsidian's guideline: defaults conflict. | jest (`tests/main.test.ts`, no command has `hotkeys`) |
| N4 | Show icons with `setIcon(el, '<lucide-name>')` (Lucide up to v0.446.0). No emoji in UI text. | Obsidian's icon set and theme colour. | ESLint (emoji literals) |
| N5 | Use Obsidian's components: `Setting`, `Modal` with `setTitle`, `Menu`, `SuggestModal`, `ButtonComponent`, `ExtraButtonComponent` (or `clickable-icon`) and `setTooltip`. Hand-build only what Obsidian has no component for (the graphs, the card gutter). | Native hover, focus and keyboard behaviour for free (inferred). | ESLint (`<button>`, `title`/`aria-label`, modal `h*`); review |
| N6 | Notices are one plain sentence in sentence case: no `Error:`, `Arete:` or emoji prefix. Say what happened and what to do. | Owner direction; Obsidian's notices carry no prefix (inferred). | ESLint (emoji); review |
| N7 | Style only with Obsidian's CSS variables: text `--text-normal/muted/faint/success/warning/error/accent`; fills `--color-red/orange/yellow/green/…`; tints `color-mix(in oklch, var(--color-x) N%, transparent)`, never `--*-rgb`; spacing `--size-*`; font `--font-ui-*` (UI) and `--font-*` (editor content); radius `--radius-*`; `--border-width`; `z-index: var(--layer-*)`; `--icon-size`; graphs `--graph-*`. Define no plugin tokens. | Themes and user settings reach the plugin. | jest `nativeCss` ratchet; ESLint (literal inline styles) |
| N8 | Mark state with Obsidian's class form on an `arete-` element (`is-active`, `is-selected`, `is-hovered`, `mod-success`, `mod-warning`, `mod-error`), toggled with `el.toggleClass(cls, on)`. No inline colour for state. | One styling path, themable. | review |
| N9 | Interaction matches core: hover with `:hover` and `--background-modifier-hover`; a visible focus ring; `cursor: var(--cursor)` on controls and `var(--cursor-link)` on links; transitions list named properties (never `all`). | Obsidian uses the arrow cursor for interactive elements. | jest `nativeCss` ratchet; ESLint (JS hover) |
| N10 | Keep `minAppVersion` at or above the highest `@since` of any Obsidian API the plugin calls. | Obsidian refuses to install, or the plugin breaks, on older apps. | review |

### Card-state colours (one table, every view)

One domain function in `src/domain/stats.ts` decides a card's state from its stats and the
threshold settings. Views map the state to a class (`mod-success`, `mod-warning`, `mod-error`);
CSS maps the class to a colour. Text uses `--text-*`; bars, dots and badges use `--color-*`. No
view holds its own cut-off. A bar or dot shows the worst state of its card.

### Layers and imports

| # | Rule | Why | Enforced by |
|---|---|---|---|
| L1 | `src/domain` imports only `src/domain`: no `obsidian`, no npm package, no Node builtin. | The core runs in tests without mocks. | depcruise `domain-imports-only-domain` |
| L2 | `src/application` depends on the ports in `src/domain/ports.ts`, never on `src/infrastructure`. | PL3. | depcruise `application-no-infrastructure` |
| L3 | Nothing imports `main.ts`. A view, modal or setting tab declares the `XxxHost` type it needs. | PL2: no import cycles. | depcruise `nothing-imports-main`, `no-circular` at error |
| L4 | `child_process`, `fs` and `requestUrl` appear only in `src/infrastructure/arete`. | One layer talks to processes, disk and HTTP. | ESLint `no-restricted-imports` |
| L5 | Import through a path alias (`@/<layer>/…` or `@domain/…`): no relative paths. | One form. | ESLint `no-restricted-imports` (relative paths only) |

### Talking to the backend

| # | Rule | Why | Enforced by |
|---|---|---|---|
| B1 | Keep logic in Python; the plugin draws what Python resolved. | `CONTRIBUTING.md`; PL1. | review |
| B2 | Python is reached through the classes in `infrastructure/arete` (`AreteClient`, `SyncService`, `ServerManager`, `CheckService`), each spawning through `resolvePythonCommand`. | One place decides interpreter, cwd and env. | review |
| B3 | Every `arete` command line the plugin spawns names a command and options registered in `src/arete/interface/`. Change a command on both sides in one commit. A command the plugin parses prints one JSON value on stdout and nothing else; logs go to stderr. | The command lines are a contract. | jest `cliCommands` |
| B4 | A failure logs `console.error('[Arete] …', err)` with the error object and shows a notice the user can act on (N6). | The console keeps the stack; the notice tells the user. | review |

### Lifecycle

| # | Rule | Why | Enforced by |
|---|---|---|---|
| C1 | Subscribe to `workspace`, `vault` and `metadataCache` events only inside `this.registerEvent(...)`. | Freed on unload. | ESLint |
| C2 | Put window/document listeners in `registerDomEvent` and intervals in `registerInterval`. | Freed on unload. | ESLint |
| C3 | Keep every timer in a field and clear it in `onClose`/`onunload`. Debounce per key when several sources share a handler. | A stale timer fires against a dead view. | review |
| C4 | Disconnect observers through `this.register(...)`. Give `MarkdownRenderer.render` a child `Component` per drawing or preview, and unload it when replaced. Destroy a 3D graph when its drawing is replaced. | Rendered children and WebGL loops outlive their drawing otherwise. | review |
| C5 | `onload` awaits only `loadSettings()`; server start and stats refresh run in `onLayoutReady`. | Startup does not wait on Python. | review |
| C6 | Start every `arete` process through a service's `AreteProcesses`; `onunload` calls each service's `dispose()`, which kills what is running and refuses new starts. | No Python process outlives the plugin. | jest (`main_commands` onunload) |

### Rendering and performance

| # | Rule | Why | Enforced by |
|---|---|---|---|
| R1 | An event-driven refresh compares a key of everything the picture depends on (plus a data version) and returns when nothing changed. | Leaf changes and resizes fire constantly. | review; a test pins the call count |
| R2 | Debounce bursty input with a named per-site `*_MS` constant. | Greppable and tunable. | ESLint (literal delays) |
| R3 | Restyle in place for search, hover and resize; do not rebuild a drawing or restart a simulation. | Keeps the layout; O(changed) work. | review |
| R4 | Read notes with `vault.cachedRead`, and only after `metadataCache` shows the read is needed. | `vault.read` always hits disk. | ESLint |
| R5 | Never call `getMarkdownFiles()` from a keystroke, modify, leaf-change or hover handler. | Cost scales with the vault. | review |
| R6 | Run per-file async work with bounded concurrency. | Overlap without flooding. | review |
| R7 | Load `3d-force-graph` on the first 3D render through `loadForceGraph3D()`, which accepts both module shapes. Never import it statically. | three.js is most of the bundle. | ESLint (3 checks) |
| R8 | Parse a note's cards once, in `cardRangesField`; other extensions read it. `GutterMarker`/`WidgetType` subclasses implement `eq()`. Map decorations through edits outside the frontmatter. | These run on every keystroke and cursor move. | ESLint (parse, `eq`) |
| R9 | Use static imports; `import()` defers nothing in this CJS bundle. | esbuild inlines it. | ESLint |

### Styling mechanics

| # | Rule | Why | Enforced by |
|---|---|---|---|
| S1 | Put constant styles in a CSS class in `src/styles/*.css`. Set inline only runtime-computed geometry (left, top, width, height). | Obsidian: "No hardcoded styling". | ESLint (literal `el.style.x =`), ratchet |
| S2 | Do visual hover with `:hover`; mouse handlers only when a hover changes other elements or loads content. | See N9. | ESLint |
| S3 | Prefix every class the plugin defines with `arete-`, named `arete-<area>-<part>`. State classes follow N8. | No clashes with Obsidian, themes or other plugins. | review |
| S5 | Edit `src/styles/*.css` only. Rebuild with `npm run build` and commit `styles.css` in the same commit. | `styles.css` is tracked build output. | jest `stylesCss` |
| S6 | Build DOM with `createEl`/`createDiv`/`createSpan`/`setText`. `innerHTML` only for card HTML in the sandboxed preview iframe. | Obsidian's security guideline. | ESLint |

### Logging

| # | Rule | Why | Enforced by |
|---|---|---|---|
| G1 | Use `debugLog` (`src/domain/debugLog.ts`) for diagnostics. No `console.log` elsewhere; a command whose output is a console report disables the rule on that line, with a reason. | Obsidian: the console shows errors only by default. | ESLint `no-console` |
| G2 | Start every console and `debugLog` message with `[Arete]`. | One filter finds plugin output. | review |

### Settings and persisted data

| # | Rule | Why | Enforced by |
|---|---|---|---|
| T1 | Add a setting to `AretePluginSettings` and `DEFAULT_SETTINGS` (`src/domain/settings.ts`) in one change; `loadSettings` drops unknown keys. | One source of defaults. | tsc |
| T2 | Persist interaction-driven state with a debounced save that skips unchanged data (`requestSaveUiState`). `saveSettings()` is for discrete changes; text fields save on blur or debounced. | `saveData` rewrites all of `data.json`. | ESLint (`saveSettings` in `addText`) |
| T3 | Never rename a shipped command id or `*_VIEW_TYPE` value. Rename the display name instead (N1). | Hotkeys, saved layouts and `skills/gui-test` refer to the ids. | review |
| T4 | Keep the imperative `display()` settings tab. Move to `getSettingDefinitions()` only together with raising `minAppVersion` to 1.13. | The declarative API needs Obsidian 1.13; typings here are 1.11.4. | review |

### TypeScript and naming

| # | Rule | Why | Enforced by |
|---|---|---|---|
| X1 | Name files camelCase in `src/domain` and PascalCase elsewhere. View types: `FOO_VIEW_TYPE = 'arete-<kebab>'`. Command ids: `arete-<kebab>`. Host types: `<Thing>Host`. Delays: `*_MS`. | The code's practice. | review |
| X2 | Use `@ts-expect-error <reason>`, never `@ts-ignore`. | It fails once the error is gone. | `@typescript-eslint/ban-ts-comment` (existing) |

### Tests

| # | Rule | Why | Enforced by |
|---|---|---|---|
| Q1 | Put a test at the source's path under `tests/`: `src/a/B.ts` → `tests/a/B.test.ts`. | Findable. | review |
| Q2 | Every fix gets a test that fails without it. A performance fix pins a number (calls, renders, reads, spawns) before → after. A guard pins its no-op. | `CONTRIBUTING.md`. | review |
| Q3 | `import … from 'obsidian'` gets the `jest.mock('obsidian', …)` factory in `tests/test-setup.ts`; add API there. That file also mocks `fs` and `child_process` for every test. A test that reads files uses `jest.requireActual('fs')`; a test that runs esbuild or TypeScript calls `jest.unmock('child_process')` and `jest.unmock('fs')` first. Mocks keep the real module's shape. | esbuild throws under the mock. | review |
| Q4 | What jest cannot see (rendering, CSS, layout, timing, WebGL, real input), check in a live Obsidian with `skills/gui-test`, and report it as seen, measured or traced. | Jest runs no layout, paint or WebGL. | review |

### Working rules for agents

| # | Rule | Why | Enforced by |
|---|---|---|---|
| W1 | `just qa` is the gate; push only when it passes. | `CONTRIBUTING.md`. | pre-push hook |
| W2 | Format only the files you touch: `npx prettier --write <files>`. Never `just fix` or `npm run format` for a scoped change. | Both rewrite the whole plugin. | review |
| W3 | One concern per commit. The body gives the reason, before → after numbers and the test command. | Reviewable history. | review |
| W4 | Never `git stash`. | The stash list is shared across worktrees. | review |
| W5 | Comments state the contract and the present-tense reason; no history, no commented-out code. | History belongs in git. | review |
| W6 | Deploying is a manual copy of `main.js`, `styles.css` and `manifest.json`. Reload the plugin and close views opened before the reload. | `CLAUDE.md`. | review |
