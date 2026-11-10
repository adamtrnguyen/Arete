import {
	Editor,
	MarkdownView,
	Notice,
	Plugin,
	FileSystemAdapter,
	TFile,
	setIcon,
	setTooltip,
	CachedMetadata,
	editorInfoField,
} from 'obsidian';
import { EditorView } from '@codemirror/view';
import * as path from 'path';
import * as os from 'os';

import { AretePluginSettings, DEFAULT_SETTINGS } from '@domain/settings';
import { resolveCardIndex } from '@domain/cardRef';

import { CardYamlEditorView, YAML_EDITOR_VIEW_TYPE } from '@presentation/views/CardYamlEditorView';
import { DashboardView, DASHBOARD_VIEW_TYPE } from '@presentation/views/DashboardView';
import { AreteSettingTab } from '@presentation/settings/SettingTab';
import { CheckResultModal } from '@presentation/modals/CheckResultModal';
import { SyncService } from '@infrastructure/arete/SyncService';
import { CheckService } from '@infrastructure/arete/CheckService';
import { TemplateRenderer } from '@application/services/TemplateRenderer';
import { StatsService } from '@application/services/StatsService';
import { StatsCache, ConceptStats } from '@/domain/stats';
import { GraphService } from '@application/services/GraphService';
import { LinkCheckerService } from '@application/services/LinkCheckerService';
import { LeechService } from '@application/services/LeechService';
import { ServerManager } from '@infrastructure/arete/ServerManager';
import { AreteClient } from '@infrastructure/arete/AreteClient';

import { LocalGraphView, LOCAL_GRAPH_VIEW_TYPE } from '@presentation/views/LocalGraphView';
import { GlobalGraphView, GLOBAL_GRAPH_VIEW_TYPE } from '@presentation/views/GlobalGraphView';
import { DependencyResolver } from '@application/services/DependencyResolver';
import { cardYamlHighlight } from '@presentation/extensions/CardYamlHighlight';
import {
	createCardGutter,
	highlightCardEffect,
} from '@presentation/extensions/CardGutterExtension';
import { debugLog, setDebugLogSwitch } from '@domain/debugLog';

/**
 * Whether a note with this cached metadata can contribute to the dependency graph. The
 * graph is built from notes with `arete`/`cards` frontmatter; an unknown cache counts.
 */
function mayAffectGraph(cache: CachedMetadata | null | undefined): boolean {
	if (!cache) return true;
	const fm = cache.frontmatter;
	return !!fm && (fm.arete !== undefined || fm.cards !== undefined);
}

const UI_STATE_SAVE_DELAY_MS = 1000;
/** How long the status bar shows "Synced" before it returns to the idle label. */
const STATUS_BAR_RESET_MS = 3000;
/** How long `showCard` waits for a freshly written note to be parsed. */
const PARSE_WAIT_MS = 2000;

export default class AretePlugin extends Plugin {
	settings: AretePluginSettings;
	statsCache: StatsCache;
	statusBarItem: HTMLElement;
	syncService: SyncService;
	checkService: CheckService;
	statsService: StatsService;
	graphService: GraphService;
	linkCheckerService: LinkCheckerService;
	leechService: LeechService;
	serverManager: ServerManager;
	dependencyResolver: DependencyResolver;
	areteClient: AreteClient;
	templateRenderer: TemplateRenderer;

	// Centralized context: filePath -> cardId
	private activeCardContext: Map<string, string> = new Map();

	// One sync-on-save timer per note, so editing B within the delay does not cancel A.
	private syncOnSaveTimers: Map<string, ReturnType<typeof setTimeout>> = new Map();

	// Debounced save of the dashboard's `ui_expanded_*` settings, and what was last written.
	private uiStateSaveTimer: ReturnType<typeof setTimeout> | null = null;
	private savedUiState = '';

	// Returns the status bar to idle after a successful sync.
	private statusBarResetTimer: ReturnType<typeof setTimeout> | null = null;

	async onload() {
		setDebugLogSwitch(() => this.settings?.debug_mode === true);
		debugLog('[Arete] Plugin Loading...');

		try {
			await this.loadSettings();
			debugLog('[Arete] Settings loaded:', this.settings);

			// Initialize Services
			this.areteClient = new AreteClient(this.settings);
			this.templateRenderer = new TemplateRenderer(this.app, this.areteClient);
			this.templateRenderer.setMode(this.settings.renderer_mode);
			this.syncService = new SyncService(this.app, this.settings, this.manifest);
			this.checkService = new CheckService(this.app, this.settings);
			this.statsService = new StatsService(
				this.app,
				this.settings,
				this.areteClient,
				this.statsCache,
			);
			this.graphService = new GraphService(this.app, this.settings);

			// Initialize New Dashboard Services
			this.linkCheckerService = new LinkCheckerService(this.app, this.checkService);
			this.leechService = new LeechService(this.areteClient);
			this.serverManager = new ServerManager(this.app, this.settings);
			const vaultAdapter = this.app.vault.adapter as FileSystemAdapter;
			this.dependencyResolver = new DependencyResolver(
				this.areteClient,
				vaultAdapter.getBasePath ? vaultAdapter.getBasePath() : '',
			);

			// Start the server once the layout is ready, then refresh stats. A server already
			// running is restarted: /health reports only a version, so the plugin cannot tell
			// whether it serves this vault with the configured Python and project root.
			this.app.workspace.onLayoutReady(async () => {
				if (this.settings.execution_mode === 'server') {
					await this.serverManager.start(true);
				}
				debugLog('[Arete] Refreshing stats on startup...');
				this.statsService
					.refreshStats()
					.then((results) => this.onStatsRefreshed(results))
					.catch((err) => {
						console.error('[Arete] Failed to auto-refresh stats:', err);
					});
			});

			debugLog('[Arete] Services initialized');

			// Register Views
			this.registerView(YAML_EDITOR_VIEW_TYPE, (leaf) => new CardYamlEditorView(leaf, this));
			this.registerView(DASHBOARD_VIEW_TYPE, (leaf) => new DashboardView(leaf, this));
			this.registerView(LOCAL_GRAPH_VIEW_TYPE, (leaf) => new LocalGraphView(leaf, this));
			this.registerView(GLOBAL_GRAPH_VIEW_TYPE, (leaf) => new GlobalGraphView(leaf, this));
		} catch (e) {
			console.error('[Arete] Failed to initialize plugin services:', e);
			new Notice('Arete failed to initialize, check the developer console for details.');
		}

		// 1. Status Bar Setup
		this.statusBarItem = this.addStatusBarItem();
		this.statusBarItem.addClass('mod-clickable');
		this.statusBarItem.addEventListener('click', () => this.runSync());
		this.updateStatusBar('idle');

		// 2. Ribbon Icons — only the two daily-driver entry points.
		// Force sync, graph views, and YAML editor remain accessible via the
		// command palette; they're context-dependent on being in an Arete file
		// and don't earn a permanent ribbon slot.
		this.addRibbonIcon('sheets-in-box', 'Sync to Anki (Arete)', (_evt: MouseEvent) => {
			this.runSync();
		});

		this.addRibbonIcon('layout-dashboard', 'Arete dashboard', (_evt: MouseEvent) => {
			this.activateDashboardView();
		});

		// 3. Commands
		this.addCommand({
			id: 'arete-sync',
			name: 'Sync',
			callback: () => {
				this.runSync();
			},
		});

		this.addCommand({
			id: 'arete-check-file',
			name: 'Check current file',
			editorCallback: (_editor: Editor, view: MarkdownView) => {
				if (view.file) {
					const vaultAdapter = this.app.vault.adapter as FileSystemAdapter;
					const basePath = vaultAdapter.getBasePath ? vaultAdapter.getBasePath() : null;
					if (basePath) {
						const fullPath = path.join(basePath, view.file.path);
						this.runCheck(fullPath);
					} else {
						new Notice('Cannot determine the vault path.');
					}
				}
			},
		});

		this.addCommand({
			id: 'arete-check-integrity',
			name: 'Debug: vault integrity check',
			callback: () => {
				this.checkVaultIntegrity();
			},
		});

		this.addCommand({
			id: 'arete-sync-current-file',
			name: 'Sync current file',
			callback: () => {
				const activeFile = this.app.workspace.getActiveFile();
				if (activeFile) {
					const vaultAdapter = this.app.vault.adapter as FileSystemAdapter;
					const basePath = vaultAdapter.getBasePath ? vaultAdapter.getBasePath() : null;
					if (basePath) {
						const maxPath = path.join(basePath, activeFile.path);
						this.runSync(false, maxPath, true);
					} else {
						new Notice('Cannot resolve the file path.');
					}
				} else {
					new Notice('No active file to sync.');
				}
			},
		});

		this.addCommand({
			id: 'arete-sync-prune',
			name: 'Sync (prune deleted cards)',
			callback: () => {
				this.runSync(true);
			},
		});

		this.addCommand({
			id: 'arete-sync-force-all',
			name: 'Sync (force re-upload all)',
			callback: () => {
				this.runSync(false, null, true);
			},
		});

		this.addCommand({
			id: 'arete-open-dashboard',
			name: 'Open dashboard',
			callback: () => {
				this.activateDashboardView();
			},
		});

		this.addCommand({
			id: 'arete-graph-clear',
			name: 'Graph: clear retention tags',
			callback: async () => {
				await this.graphService.clearAllTags();
			},
		});

		this.addCommand({
			id: 'arete-open-local-graph',
			name: 'Open local graph',
			callback: () => {
				this.activateLocalGraphView();
			},
		});

		this.addCommand({
			id: 'arete-open-global-graph',
			name: 'Open global graph',
			callback: () => {
				this.activateGlobalGraphView();
			},
		});

		this.addCommand({
			id: 'arete-sync-stats',
			name: 'Sync stats (refresh Anki data)',
			callback: async () => {
				new Notice('Refreshing stats...');
				const results = await this.statsService.refreshStats();
				await this.onStatsRefreshed(results);
				new Notice('Stats refreshed.');
			},
		});

		this.addCommand({
			id: 'arete-reload-note-types',
			name: 'Reload note types (card preview)',
			callback: () => {
				this.templateRenderer.clearModelCache();
				new Notice('Note types will be fetched from Anki on the next preview.');
			},
		});

		// 4. Settings
		this.addSettingTab(new AreteSettingTab(this.app, this));

		// obsidian://arete?vault=<vault>&file=<path>&card=<arete id | 1-based position>
		// lets a terminal agent put the card it just wrote in front of the user.
		this.registerObsidianProtocolHandler('arete', (params) => {
			void this.showCard(params.file, params.card);
		});

		// 5. YAML Editor command (no ribbon icon — context-dependent)
		this.addCommand({
			id: 'open-yaml-editor',
			name: 'Open YAML editor',
			callback: () => {
				this.activateYamlEditorView();
			},
		});

		this.registerEditorExtension(cardYamlHighlight);
		this.registerEditorExtension(
			createCardGutter(
				(cardIndex) => {
					this.highlightCardLines(cardIndex);
					// Move the Card Editor to this card only if it is open; do not force focus.
					this.syncYamlEditorToCard(cardIndex);

					// Sync with Graph View
					const activeFile = this.app.workspace.getActiveFile();
					if (activeFile) {
						const cache = this.app.metadataCache.getFileCache(activeFile);
						if (cache?.frontmatter?.cards && cache.frontmatter.cards[cardIndex]) {
							const cardId = cache.frontmatter.cards[cardIndex].id;
							if (cardId) {
								// Set central context logic
								this.setCardContext(activeFile.path, cardId);
								// Trigger event for immediate update if graph is open
								this.app.workspace.trigger('arete:card-selected', cardId);
							}
						}
					}
				},
				(nid: number | null, cid: number | null, view: EditorView) => {
					const info = view.state.field(editorInfoField);
					const file = info?.file;
					if (!file) {
						return null;
					}

					const cache = this.statsService.getCache();
					const conceptStats = cache.concepts[file.path];

					if (!conceptStats || !conceptStats.cardStats) return null;

					if (cid && conceptStats.cardStats[cid]) return conceptStats.cardStats[cid];
					if (nid && conceptStats.cardStats[nid]) return conceptStats.cardStats[nid];

					return null;
				},
				this.settings.stats_algorithm,
				() => this.settings,
			),
		);

		// 7. Drop the cached dependency graph when a note that is, or was, in it changes.
		// At `modify` the metadata cache still holds the previous content, so `modify`
		// covers a note that had cards and `changed` covers a note that has them now.
		this.registerEvent(
			this.app.metadataCache.on('changed', (_file, _data, cache) => {
				if (mayAffectGraph(cache)) this.dependencyResolver?.invalidate();
			}),
		);
		this.registerEvent(
			this.app.metadataCache.on('deleted', (file, prevCache) => {
				if (file.path.endsWith('.md') && mayAffectGraph(prevCache)) {
					this.dependencyResolver?.invalidate();
				}
			}),
		);
		this.registerEvent(
			this.app.vault.on('rename', (file, oldPath) => {
				if (!file.path.endsWith('.md') && !oldPath.endsWith('.md')) return;
				if (mayAffectGraph(this.app.metadataCache.getFileCache(file as TFile))) {
					this.dependencyResolver?.invalidate();
				}
			}),
		);

		// 8. Sync on Save (Debounced).
		this.registerEvent(
			this.app.vault.on('modify', (file) => {
				if (
					file.path.endsWith('.md') &&
					mayAffectGraph(this.app.metadataCache.getFileCache(file as TFile))
				) {
					this.dependencyResolver?.invalidate();
				}
				if (!this.settings.sync_on_save) return;
				if (!file.path.endsWith('.md')) return;

				// Debounce per path: a later save of the same note restarts its own timer only.
				const pending = this.syncOnSaveTimers.get(file.path);
				if (pending) clearTimeout(pending);

				const timer = setTimeout(async () => {
					this.syncOnSaveTimers.delete(file.path);
					const vaultAdapter = this.app.vault.adapter as FileSystemAdapter;
					const basePath = vaultAdapter.getBasePath ? vaultAdapter.getBasePath() : null;
					if (basePath) {
						const fullPath = path.join(basePath, file.path);
						debugLog('[Arete] Sync on save triggered for:', file.path);
						await this.runSync(false, fullPath, false);
					}
				}, this.settings.sync_on_save_delay);
				this.syncOnSaveTimers.set(file.path, timer);
			}),
		);
	}

	// Highlight card lines in editor (permanent until different card is clicked)
	highlightCardLines(cardIndex: number) {
		// Use getMostRecentLeaf to find the editor (works when called from sidebar)
		const leaf = this.app.workspace.getMostRecentLeaf();
		if (!leaf || !(leaf.view instanceof MarkdownView)) return;

		// @ts-expect-error - accessing internal editor
		const cm = leaf.view.editor.cm as EditorView;
		if (cm) {
			cm.dispatch({
				effects: highlightCardEffect.of({ cardIndex }),
			});
		}
	}

	async activateDashboardView() {
		const { workspace } = this.app;
		let leaf = workspace.getLeavesOfType(DASHBOARD_VIEW_TYPE)[0];

		if (!leaf) {
			const rightLeaf = workspace.getRightLeaf(false);
			if (rightLeaf) {
				await rightLeaf.setViewState({
					type: DASHBOARD_VIEW_TYPE,
					active: true,
				});
			}
			leaf = workspace.getLeavesOfType(DASHBOARD_VIEW_TYPE)[0];
		}

		if (leaf) {
			workspace.revealLeaf(leaf);
		}
	}

	async activateYamlEditorView(focusCardIndex?: number) {
		const { workspace } = this.app;
		let leaf = workspace.getLeavesOfType(YAML_EDITOR_VIEW_TYPE)[0];

		if (!leaf) {
			const rightLeaf = workspace.getRightLeaf(false);
			if (rightLeaf) {
				await rightLeaf.setViewState({
					type: YAML_EDITOR_VIEW_TYPE,
					active: true,
				});
			}
			leaf = workspace.getLeavesOfType(YAML_EDITOR_VIEW_TYPE)[0];
		}

		if (leaf) {
			workspace.revealLeaf(leaf);
			if (focusCardIndex !== undefined) {
				const view = leaf.view as CardYamlEditorView;
				if (view?.showCardIndex) {
					await view.showCardIndex(focusCardIndex);
				}
			}
		}
	}

	syncYamlEditorToCard(cardIndex: number) {
		const { workspace } = this.app;
		const leaf = workspace.getLeavesOfType(YAML_EDITOR_VIEW_TYPE)[0];
		if (leaf) {
			const view = leaf.view as CardYamlEditorView;
			if (view?.focusCard) {
				view.focusCard(cardIndex);
			}
		}
	}

	/** Open a note and show one card in the Card Editor (see the obsidian://arete handler). */
	async showCard(filePath?: string, card?: string): Promise<void> {
		const path = filePath && !filePath.endsWith('.md') ? `${filePath}.md` : filePath;
		const file = path ? this.app.vault.getFileByPath(path) : null;
		if (!file) {
			new Notice(`No note found at ${filePath}.`);
			return;
		}
		await this.app.workspace.getLeaf(false).openFile(file);
		await this.waitForParse(file);
		const cards = this.app.metadataCache.getFileCache(file)?.frontmatter?.cards;
		const index = resolveCardIndex(Array.isArray(cards) ? cards : [], card);
		if (index === null) {
			new Notice(
				card ? `${file.basename} has no card ${card}.` : `${file.basename} has no cards.`,
			);
			return;
		}
		await this.activateYamlEditorView(index);
	}

	/** A note written a moment ago may not be parsed yet; wait for it, briefly. */
	private waitForParse(file: TFile, timeoutMs = PARSE_WAIT_MS): Promise<void> {
		if (this.app.metadataCache.getFileCache(file)?.frontmatter) return Promise.resolve();
		return new Promise((resolve) => {
			const done = () => {
				this.app.metadataCache.offref(ref);
				clearTimeout(timer);
				resolve();
			};
			const ref = this.app.metadataCache.on('changed', (changed) => {
				if (changed.path === file.path) done();
			});
			const timer = setTimeout(done, timeoutMs);
		});
	}

	async activateLocalGraphView(cardId?: string) {
		const { workspace } = this.app;
		let leaf = workspace.getLeavesOfType(LOCAL_GRAPH_VIEW_TYPE)[0];

		if (!leaf) {
			const rightLeaf = workspace.getRightLeaf(false);
			if (rightLeaf) {
				await rightLeaf.setViewState({
					type: LOCAL_GRAPH_VIEW_TYPE,
					active: true,
				});
			}
			leaf = workspace.getLeavesOfType(LOCAL_GRAPH_VIEW_TYPE)[0];
		}

		if (leaf) {
			workspace.revealLeaf(leaf);
			const activeFile = this.app.workspace.getActiveFile();

			if (leaf.view instanceof LocalGraphView) {
				const view = leaf.view as LocalGraphView;

				if (cardId) {
					// 1. Explicit Card Navigation
					// Use current file from workspace if available, otherwise just try to set card
					if (activeFile) {
						view.setContext(activeFile.path, cardId); // Robust: explicit file + card
					}
					// Also update centralized map
					if (activeFile) this.setCardContext(activeFile.path, cardId);
				} else if (activeFile) {
					// 2. Just opening graph for current file
					view.setContext(activeFile.path, this.getCardContext(activeFile.path));
				}
			}
		}
	}

	async activateGlobalGraphView() {
		const { workspace } = this.app;
		let leaf = workspace.getLeavesOfType(GLOBAL_GRAPH_VIEW_TYPE)[0];
		if (!leaf) {
			leaf = workspace.getLeaf('tab');
			await leaf.setViewState({ type: GLOBAL_GRAPH_VIEW_TYPE, active: true });
		}
		workspace.revealLeaf(leaf);
	}

	onunload() {
		if (this.statusBarResetTimer) clearTimeout(this.statusBarResetTimer);
		for (const timer of this.syncOnSaveTimers.values()) clearTimeout(timer);
		this.syncOnSaveTimers.clear();
		if (this.uiStateSaveTimer) void this.flushUiState();
		if (this.statusBarItem) {
			this.statusBarItem.empty();
		}
		if (this.serverManager) {
			this.serverManager.stop();
		}
	}

	updateStatusBar(state: 'idle' | 'syncing' | 'error' | 'success', msg?: string) {
		if (!this.statusBarItem) return;
		this.statusBarItem.empty();
		if (this.statusBarResetTimer) {
			clearTimeout(this.statusBarResetTimer);
			this.statusBarResetTimer = null;
		}

		if (state === 'idle') {
			// Show last sync time
			const lastSync = this.settings.last_sync_time;
			if (lastSync) {
				this.setStatusBar('layers', this.formatTimeAgo(lastSync));
				setTooltip(this.statusBarItem, 'Click to sync to Anki');
			} else {
				this.setStatusBar('layers', 'Arete');
				setTooltip(this.statusBarItem, 'Never synced. Click to sync.');
			}
			return;
		}

		if (state === 'syncing') {
			this.setStatusBar('refresh-cw', 'Syncing...');
		} else if (state === 'success') {
			this.setStatusBar('check', 'Synced');
			this.statusBarResetTimer = setTimeout(() => {
				this.statusBarResetTimer = null;
				this.updateStatusBar('idle');
			}, STATUS_BAR_RESET_MS);
		} else if (state === 'error') {
			this.setStatusBar('circle-x', 'Error');
			setTooltip(this.statusBarItem, msg || 'Check logs');
		}
	}

	/** One status-bar entry: a Lucide icon followed by its label. */
	private setStatusBar(icon: string, label: string) {
		setIcon(this.statusBarItem.createSpan({ cls: 'status-bar-item-icon' }), icon);
		this.statusBarItem.createSpan({ text: label });
	}

	private formatTimeAgo(timestamp: number): string {
		const now = Date.now();
		const diff = now - timestamp;
		const minutes = Math.floor(diff / 60000);
		const hours = Math.floor(diff / 3600000);
		const days = Math.floor(diff / 86400000);

		if (minutes < 1) return 'Just now';
		if (minutes < 60) return `${minutes}m ago`;
		if (hours < 24) return `${hours}h ago`;
		return `${days}d ago`;
	}

	/**
	 * Post-refresh hook: update graph tags and YAML toolbar after stats refresh.
	 */
	private async onStatsRefreshed(results: ConceptStats[]): Promise<void> {
		if (this.settings.graph_coloring_enabled) {
			for (const concept of results) {
				const file = this.app.vault.getAbstractFileByPath(concept.filePath);
				if (file instanceof TFile) {
					await this.graphService.updateGraphTags(file, concept);
				}
			}
			new Notice('Graph tags updated.');
		}

		const yamlLeaf = this.app.workspace.getLeavesOfType(YAML_EDITOR_VIEW_TYPE)[0];
		if (yamlLeaf) {
			const view = yamlLeaf.view as CardYamlEditorView;
			if (view.renderToolbar) {
				view.renderToolbar();
			}
		}
	}

	// Delegate to SyncService
	async runSync(prune = false, targetPath: string | null = null, force = false) {
		await this.syncService.runSync(prune, targetPath, force, this.updateStatusBar.bind(this));
		// Update last sync time on success
		this.settings.last_sync_time = Date.now();
		await this.saveSettings();
	}

	// Delegate to CheckService, then open modal (DDD Compliant)
	async runCheck(filePath: string) {
		new Notice('Checking file...');
		try {
			const res = await this.checkService.getCheckResult(filePath);
			new CheckResultModal(this.app, this, res, filePath).open();
		} catch (e: any) {
			new Notice(`Check failed: ${e.message}`);
		}
	}

	async runFix(filePath: string) {
		await this.checkService.runFix(filePath);
	}

	/** The "Debug: Vault Integrity Check" command: notes whose YAML Obsidian cannot parse. */
	async checkVaultIntegrity() {
		new Notice('Running integrity check...');
		const files = this.app.vault.getMarkdownFiles();
		// eslint-disable-next-line no-console -- the command's output is a console report
		console.log('[Arete] Integrity check started.');

		const failing = await this.linkCheckerService.findAllUnparsedFrontmatter(files);
		for (const file of failing) {
			console.error(
				`[Arete] ${file.path}: has a YAML block, but Obsidian's cache has no frontmatter (likely invalid properties).`,
			);
		}

		// eslint-disable-next-line no-console -- the command's output is a console report
		console.log(
			`[Arete] Integrity check complete: ${files.length} files checked, ${failing.length} potential issues.`,
		);
		if (failing.length > 0) {
			new Notice(
				`${failing.length} files have invalid properties, check the developer console.`,
			);
		} else {
			new Notice('Integrity check passed, Obsidian parses all YAML.');
		}
	}

	async testConfig() {
		await this.checkService.testConfig();
	}

	async testAnkiConnect() {
		await this.checkService.testAnkiConnect();
	}

	async loadSettings() {
		const data = await this.loadData();
		// Keep only current settings, so keys from removed features are not saved back.
		const known = Object.fromEntries(
			Object.entries(data ?? {}).filter(([k]) => k in DEFAULT_SETTINGS),
		);
		this.settings = Object.assign({}, DEFAULT_SETTINGS, known);
		this.statsCache = data?.statsCache;
		const home = os.homedir();
		if (this.settings.project_root.startsWith('~')) {
			this.settings.project_root = this.settings.project_root.replace('~', home);
		}
		this.savedUiState = this.uiStateKey();
	}

	/** What `data.json` holds: the settings, `project_root` relative to home, and the stats cache. */
	private dataToSave() {
		const toSave = { ...this.settings, statsCache: this.statsCache };
		const home = os.homedir();
		if (toSave.project_root.startsWith(home)) {
			toSave.project_root = toSave.project_root.replace(home, '~');
		}
		return toSave;
	}

	private uiStateKey(): string {
		return JSON.stringify([
			this.settings.ui_expanded_decks,
			this.settings.ui_expanded_concepts,
		]);
	}

	/**
	 * Persist the dashboard's expansion state after a short quiet period. A burst of
	 * toggles writes `data.json` once, and not at all if it ends where it started.
	 */
	requestSaveUiState(): void {
		if (this.uiStateSaveTimer) clearTimeout(this.uiStateSaveTimer);
		this.uiStateSaveTimer = setTimeout(() => void this.flushUiState(), UI_STATE_SAVE_DELAY_MS);
	}

	private async flushUiState(): Promise<void> {
		if (this.uiStateSaveTimer) clearTimeout(this.uiStateSaveTimer);
		this.uiStateSaveTimer = null;
		const state = this.uiStateKey();
		if (state === this.savedUiState) return;
		this.savedUiState = state;
		await this.saveData(this.dataToSave());
	}

	async saveSettings() {
		this.savedUiState = this.uiStateKey();
		await this.saveData(this.dataToSave());
		// Update services with new settings
		this.syncService.settings = this.settings;
		this.checkService.settings = this.settings;
		if (this.statsService) {
			this.statsService.settings = this.settings;
		}
		if (this.graphService) {
			this.graphService.updateSettings(this.settings);
		}
	}

	async saveStats() {
		if (this.statsService) {
			this.statsCache = this.statsService.getCache();
			await this.saveSettings();
		}
	}

	// --- Context Management ---

	setCardContext(filePath: string, cardId: string) {
		this.activeCardContext.set(filePath, cardId);
	}

	getCardContext(filePath: string): string | undefined {
		return this.activeCardContext.get(filePath);
	}
}
