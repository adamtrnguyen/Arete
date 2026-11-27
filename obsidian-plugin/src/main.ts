import {
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
import { PruneConfirmModal } from '@presentation/modals/PruneConfirmModal';
import { registerPluginCommands } from '@presentation/registerCommands';
import {
	activateLocalGraph,
	activateSidebarView,
	activateTabView,
	syncYamlEditorToCard,
} from '@presentation/viewActivation';
import { SyncService, SyncStatus } from '@infrastructure/arete/SyncService';
import { SyncRequest, SyncScheduler } from '@application/services/SyncScheduler';
import { CheckService } from '@infrastructure/arete/CheckService';
import { TemplateRenderer } from '@application/services/TemplateRenderer';
import { StatsService } from '@application/services/StatsService';
import { StatsCache, ConceptStats } from '@/domain/stats';
import { GraphService } from '@application/services/GraphService';
import { LinkCheckerService } from '@application/services/LinkCheckerService';
import { LeechService } from '@application/services/LeechService';
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
	settings!: AretePluginSettings;
	statsCache!: StatsCache;
	statusBarItem!: HTMLElement;
	syncService!: SyncService;
	checkService!: CheckService;
	statsService!: StatsService;
	graphService!: GraphService;
	linkCheckerService!: LinkCheckerService;
	leechService!: LeechService;
	dependencyResolver!: DependencyResolver;
	areteClient!: AreteClient;
	templateRenderer!: TemplateRenderer;

	// One sync at a time; saves made during a sync become one follow-up.
	private syncScheduler = new SyncScheduler({
		run: (request) => this.executeSync(request),
		busy: () => new Notice('A sync is already running.'),
		queueChanged: () => this.updateStatusBar('syncing'),
	});

	// Centralized context: filePath -> cardId
	private activeCardContext: Map<string, string> = new Map();

	// One sync-on-save timer per note, so editing B within the delay does not cancel A.
	private syncOnSaveTimers: Map<string, number> = new Map();

	// Debounced save of the dashboard's `ui_expanded_*` settings, and what was last written.
	private uiStateSaveTimer: number | null = null;
	private savedUiState = '';

	// Returns the status bar to idle after a successful sync.
	private statusBarResetTimer: number | null = null;
	// Active waitForParse cleanup callbacks so timers/refs do not outlive plugin unload.
	private parseWaitCleanups: Set<() => void> = new Set();

	async onload() {
		setDebugLogSwitch(() => this.settings?.debug_mode === true);
		debugLog('[Arete] Plugin Loading...');

		try {
			await this.loadSettings();
			debugLog('[Arete] Settings loaded:', this.settings);

			// Initialize Services
			const vaultAdapter = this.app.vault.adapter as FileSystemAdapter;
			const vaultRoot = vaultAdapter.getBasePath ? vaultAdapter.getBasePath() : '';
			this.areteClient = new AreteClient(this.settings, vaultRoot);
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
			this.dependencyResolver = new DependencyResolver(this.areteClient, vaultRoot);

			// Refresh stats once the layout is ready, so startup does not wait on Python.
			this.app.workspace.onLayoutReady(() => {
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
			return;
		}

		// 1. Status Bar Setup
		this.statusBarItem = this.addStatusBarItem();
		this.statusBarItem.addClass('mod-clickable');
		this.statusBarItem.addClass('arete-status-bar');
		this.statusBarItem.addEventListener('click', () => {
			void this.runSync();
		});
		this.updateStatusBar('idle');

		// 2. Ribbon icons and commands
		registerPluginCommands(this);

		// 3. Settings
		this.addSettingTab(new AreteSettingTab(this.app, this));

		// obsidian://arete?vault=<vault>&file=<path>&card=<arete id | 1-based position>
		// lets a terminal agent put the card it just wrote in front of the user.
		this.registerObsidianProtocolHandler('arete', (params) => {
			void this.showCard(params.file, params.card);
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
			this.app.vault.on('delete', (file) => {
				const pending = this.syncOnSaveTimers.get(file.path);
				if (pending !== undefined) {
					window.clearTimeout(pending);
					this.syncOnSaveTimers.delete(file.path);
				}
				this.activeCardContext.delete(file.path);
				const concepts = this.statsService?.getCache().concepts;
				if (concepts && file.path in concepts) {
					delete concepts[file.path];
				}
			}),
		);
		this.registerEvent(
			this.app.vault.on('rename', (file, oldPath) => {
				const pending = this.syncOnSaveTimers.get(oldPath);
				if (pending !== undefined) {
					window.clearTimeout(pending);
					this.syncOnSaveTimers.delete(oldPath);
				}
				const rememberedCard = this.activeCardContext.get(oldPath);
				if (rememberedCard !== undefined) {
					this.activeCardContext.delete(oldPath);
					this.activeCardContext.set(file.path, rememberedCard);
				}
				const concepts = this.statsService?.getCache().concepts;
				if (concepts && oldPath in concepts) {
					const moved = { ...concepts[oldPath], filePath: file.path };
					if (file instanceof TFile) moved.fileName = file.name;
					concepts[file.path] = moved;
					delete concepts[oldPath];
				}
				if (!(file instanceof TFile)) return;
				if (!file.path.endsWith('.md') && !oldPath.endsWith('.md')) return;
				if (mayAffectGraph(this.app.metadataCache.getFileCache(file))) {
					this.dependencyResolver?.invalidate();
				}
			}),
		);

		// 8. Sync on Save (Debounced).
		this.registerEvent(
			this.app.vault.on('modify', (file) => {
				if (!(file instanceof TFile)) return;
				if (
					file.path.endsWith('.md') &&
					mayAffectGraph(this.app.metadataCache.getFileCache(file))
				) {
					this.dependencyResolver?.invalidate();
				}
				if (!this.settings.sync_on_save) return;
				if (!file.path.endsWith('.md')) return;

				// Debounce per path: a later save of the same note restarts its own timer only.
				const pending = this.syncOnSaveTimers.get(file.path);
				if (pending !== undefined) window.clearTimeout(pending);

				const timer = window.setTimeout(() => {
					this.syncOnSaveTimers.delete(file.path);
					const vaultAdapter = this.app.vault.adapter as FileSystemAdapter;
					const basePath = vaultAdapter.getBasePath ? vaultAdapter.getBasePath() : null;
					if (basePath) {
						const fullPath = path.join(basePath, file.path);
						debugLog('[Arete] Sync on save triggered for:', file.path);
						void this.syncScheduler.requestSave(fullPath);
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
		await activateSidebarView(this.app.workspace, DASHBOARD_VIEW_TYPE);
	}

	async activateYamlEditorView(focusCardIndex?: number) {
		const leaf = await activateSidebarView(this.app.workspace, YAML_EDITOR_VIEW_TYPE);
		if (leaf && focusCardIndex !== undefined) {
			const view = leaf.view as CardYamlEditorView;
			if (view?.showCardIndex) {
				await view.showCardIndex(focusCardIndex);
			}
		}
	}

	syncYamlEditorToCard(cardIndex: number) {
		syncYamlEditorToCard(this.app.workspace, cardIndex);
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
			let settled = false;
			const done = () => {
				if (settled) return;
				settled = true;
				this.parseWaitCleanups.delete(done);
				this.app.metadataCache.offref(ref);
				window.clearTimeout(timer);
				resolve();
			};
			const ref = this.app.metadataCache.on('changed', (changed) => {
				if (changed.path === file.path) done();
			});
			const timer = window.setTimeout(done, timeoutMs);
			this.parseWaitCleanups.add(done);
		});
	}

	async activateLocalGraphView(cardId?: string) {
		await activateLocalGraph(this.app, this, cardId);
	}

	async activateGlobalGraphView() {
		await activateTabView(this.app.workspace, GLOBAL_GRAPH_VIEW_TYPE);
	}

	onunload() {
		if (this.statusBarResetTimer !== null) window.clearTimeout(this.statusBarResetTimer);
		for (const timer of this.syncOnSaveTimers.values()) window.clearTimeout(timer);
		this.syncOnSaveTimers.clear();
		for (const cleanup of [...this.parseWaitCleanups]) cleanup();
		this.parseWaitCleanups.clear();
		if (this.uiStateSaveTimer !== null) void this.flushUiState();
		if (this.statusBarItem) {
			this.statusBarItem.empty();
		}
		// No `arete` process outlives the plugin; a call in flight rejects.
		this.syncScheduler.dispose();
		this.areteClient?.dispose();
		this.checkService?.dispose();
		this.syncService?.dispose();
	}

	updateStatusBar(state: SyncStatus, msg?: string) {
		if (!this.statusBarItem) return;
		this.statusBarItem.empty();
		if (this.statusBarResetTimer !== null) {
			window.clearTimeout(this.statusBarResetTimer);
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
			const queued = this.syncScheduler.isQueued;
			this.setStatusBar('refresh-cw', queued ? 'Syncing... (1 queued)' : 'Syncing...');
			setTooltip(
				this.statusBarItem,
				queued ? 'A sync is running. Another runs when it finishes.' : 'A sync is running.',
			);
		} else if (state === 'success') {
			this.setStatusBar('check', 'Synced');
			setTooltip(this.statusBarItem, 'Click to sync to Anki');
			this.statusBarResetTimer = window.setTimeout(() => {
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
	async onStatsRefreshed(results: ConceptStats[]): Promise<void> {
		await this.saveStats();

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

	/** A sync the user asked for. While another runs, it says so and starts nothing. */
	async runSync(prune = false, targetPath: string | null = null, force = false) {
		await this.syncScheduler.requestManual({ prune, targetPath, force });
	}

	/** Run one sync to its end; a successful one becomes the last sync time. */
	private async executeSync(request: SyncRequest): Promise<void> {
		const outcome = await this.syncService.runSync(
			request.prune,
			request.targetPath,
			request.force,
			this.updateStatusBar.bind(this),
			(preview) => new PruneConfirmModal(this.app, preview).ask(),
		);
		if (outcome !== 'success') return;
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
		console.debug('[Arete] Integrity check started.');

		const failing = await this.linkCheckerService.findAllUnparsedFrontmatter(files);
		for (const file of failing) {
			console.error(
				`[Arete] ${file.path}: has a YAML block, but Obsidian's cache has no frontmatter (likely invalid properties).`,
			);
		}

		console.debug(
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
		this.statsCache = data?.statsCache ?? { concepts: {}, lastFetched: 0 };
		const home = os.homedir();
		const root = this.settings.project_root;
		if (root === '~' || root.startsWith('~/') || root.startsWith('~\\')) {
			this.settings.project_root = home + root.slice(1);
		}
		this.savedUiState = this.uiStateKey();
	}

	/** What `data.json` holds: the settings, `project_root` relative to home, and the stats cache. */
	private dataToSave() {
		const toSave = { ...this.settings, statsCache: this.statsCache };
		const home = os.homedir();
		const root = toSave.project_root;
		if (root === home || root.startsWith(`${home}/`) || root.startsWith(`${home}\\`)) {
			toSave.project_root = `~${root.slice(home.length)}`;
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
		if (this.uiStateSaveTimer !== null) window.clearTimeout(this.uiStateSaveTimer);
		this.uiStateSaveTimer = window.setTimeout(
			() => void this.flushUiState(),
			UI_STATE_SAVE_DELAY_MS,
		);
	}

	private async flushUiState(): Promise<void> {
		if (this.uiStateSaveTimer !== null) window.clearTimeout(this.uiStateSaveTimer);
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
