import {
	Editor,
	MarkdownView,
	Notice,
	Plugin,
	FileSystemAdapter,
	TFile,
	editorInfoField,
} from 'obsidian';
import { EditorView } from '@codemirror/view';
import * as path from 'path';

import { AretePluginSettings, DEFAULT_SETTINGS } from '@domain/settings';

import { CardYamlEditorView, YAML_EDITOR_VIEW_TYPE } from '@presentation/views/CardYamlEditorView';
import { DashboardView, DASHBOARD_VIEW_TYPE } from '@presentation/views/DashboardView';
import { AreteSettingTab } from '@presentation/settings/SettingTab';
import { SyncService } from '@application/services/SyncService';
import { CheckService } from '@application/services/CheckService';
import { TemplateRenderer } from '@application/services/TemplateRenderer';
import { StatsService } from '@application/services/StatsService';
import { StatsCache, ConceptStats } from '@/domain/stats';
import { GraphService } from '@application/services/GraphService';
import { LinkCheckerService } from '@application/services/LinkCheckerService';
import { LeechService } from '@application/services/LeechService';
import { ServerManager } from '@application/services/ServerManager';
import { AreteClient } from '@infrastructure/arete/AreteClient';

import { LocalGraphView, LOCAL_GRAPH_VIEW_TYPE } from '@presentation/views/LocalGraphView';
import { GlobalGraphView, GLOBAL_GRAPH_VIEW_TYPE } from '@presentation/views/GlobalGraphView';
import { DependencyResolver } from '@application/services/DependencyResolver';
import {
	createCardGutter,
	highlightCardEffect,
} from '@presentation/extensions/CardGutterExtension';

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

	private syncOnSaveTimeout: ReturnType<typeof setTimeout> | null = null;

	async onload() {
		console.log('[Arete] Plugin Loading...');

		try {
			await this.loadSettings();
			console.log('[Arete] Settings loaded:', this.settings);

			// Initialize Services
			this.areteClient = new AreteClient(this.settings);
			this.templateRenderer = new TemplateRenderer(this.app, this.areteClient);
			this.templateRenderer.setMode(this.settings.renderer_mode);
			this.syncService = new SyncService(this.app, this.settings, this.manifest);
			this.checkService = new CheckService(this.app, this.settings);
			this.statsService = new StatsService(this.app, this.settings, this.areteClient, this.statsCache);
			this.graphService = new GraphService(this.app, this.settings);

			// Initialize New Dashboard Services
			this.linkCheckerService = new LinkCheckerService(this.app, this);
			this.leechService = new LeechService(this.areteClient);
			this.serverManager = new ServerManager(this.app, this.settings);
			this.dependencyResolver = new DependencyResolver(this.app, this.settings);

			// Start Server (background) if enabled
			this.serverManager.start(true);

			// Auto-refresh stats on startup
			this.app.workspace.onLayoutReady(async () => {
				if (this.settings.execution_mode === 'server') {
					await this.serverManager.start();
				}
				console.log('[Arete] Refreshing stats on startup...');
				this.statsService
					.refreshStats()
					.then((results) => this.onStatsRefreshed(results))
					.catch((err) => {
						console.error('[Arete] Failed to auto-refresh stats:', err);
					});
			});

			console.log('[Arete] Services initialized');

			// Register Views
			this.registerView(YAML_EDITOR_VIEW_TYPE, (leaf) => new CardYamlEditorView(leaf, this));
			this.registerView(DASHBOARD_VIEW_TYPE, (leaf) => new DashboardView(leaf, this));
			this.registerView(LOCAL_GRAPH_VIEW_TYPE, (leaf) => new LocalGraphView(leaf, this));
			this.registerView(GLOBAL_GRAPH_VIEW_TYPE, (leaf) => new GlobalGraphView(leaf, this));
		} catch (e) {
			console.error('[Arete] Failed to initialize plugin services:', e);
			new Notice('Arete Plugin failed to initialize! Check console.');
		}

		// 1. Status Bar Setup
		this.statusBarItem = this.addStatusBarItem();
		this.statusBarItem.addClass('mod-clickable');
		this.statusBarItem.addEventListener('click', () => this.runSync());
		this.updateStatusBar('idle');

		// 2. Ribbon Icon
		this.addRibbonIcon('sheets-in-box', 'Sync to Anki (Arete)', (_evt: MouseEvent) => {
			this.runSync();
		});

		this.addRibbonIcon('refresh-cw', 'Force Sync All (Arete)', (_evt: MouseEvent) => {
			this.runSync(false, null, true);
		});

		this.addRibbonIcon('layout-dashboard', 'Arete Dashboard', (_evt: MouseEvent) => {
			this.activateDashboardView();
		});

		this.addRibbonIcon('network', 'Arete Local Graph', (_evt: MouseEvent) => {
			this.activateLocalGraphView();
		});

		this.addRibbonIcon('globe', 'Arete Global Graph', (_evt: MouseEvent) => {
			this.activateGlobalGraphView();
		});

		// 3. Commands
		this.addCommand({
			id: 'arete-sync',
			name: 'Sync',
			hotkeys: [{ modifiers: ['Mod', 'Shift'], key: 'A' }],
			callback: () => {
				this.runSync();
			},
		});

		this.addCommand({
			id: 'arete-check-file',
			name: 'Check Current File',
			editorCallback: (_editor: Editor, view: MarkdownView) => {
				if (view.file) {
					const vaultAdapter = this.app.vault.adapter as FileSystemAdapter;
					const basePath = vaultAdapter.getBasePath ? vaultAdapter.getBasePath() : null;
					if (basePath) {
						const fullPath = path.join(basePath, view.file.path);
						this.runCheck(fullPath);
					} else {
						new Notice('Error: Cannot determine vault path.');
					}
				}
			},
		});

		this.addCommand({
			id: 'arete-check-integrity',
			name: 'Debug: Vault Integrity Check',
			callback: () => {
				this.checkVaultIntegrity();
			},
		});

		this.addCommand({
			id: 'arete-sync-current-file',
			name: 'Sync Current File',
			callback: () => {
				const activeFile = this.app.workspace.getActiveFile();
				if (activeFile) {
					const vaultAdapter = this.app.vault.adapter as FileSystemAdapter;
					const basePath = vaultAdapter.getBasePath ? vaultAdapter.getBasePath() : null;
					if (basePath) {
						const maxPath = path.join(basePath, activeFile.path);
						this.runSync(false, maxPath, true);
					} else {
						new Notice('Error: Cannot resolve file path.');
					}
				} else {
					new Notice('No active file to sync.');
				}
			},
		});

		this.addCommand({
			id: 'arete-sync-prune',
			name: 'Sync (Prune Deleted Cards)',
			callback: () => {
				this.runSync(true);
			},
		});

		this.addCommand({
			id: 'arete-sync-force-all',
			name: 'Sync (Force Re-upload All)',
			callback: () => {
				this.runSync(false, null, true);
			},
		});

		this.addCommand({
			id: 'arete-open-dashboard',
			name: 'Open Dashboard',
			callback: () => {
				this.activateDashboardView();
			},
		});

		this.addCommand({
			id: 'arete-graph-clear',
			name: 'Graph: Clear Retention Tags',
			callback: async () => {
				await this.graphService.clearAllTags();
			},
		});

		this.addCommand({
			id: 'arete-open-local-graph',
			name: 'Open Local Graph',
			callback: () => {
				this.activateLocalGraphView();
			},
		});

		this.addCommand({
			id: 'arete-open-global-graph',
			name: 'Open Global Graph',
			callback: () => {
				this.activateGlobalGraphView();
			},
		});

		this.addCommand({
			id: 'arete-sync-stats',
			name: 'Sync Stats (Refresh Anki Data)',
			callback: async () => {
				new Notice('Refreshing Arete stats...');
				const results = await this.statsService.refreshStats();
				await this.onStatsRefreshed(results);
				new Notice('Stats refreshed.');
			},
		});

		// 4. Settings
		this.addSettingTab(new AreteSettingTab(this.app, this));

		// 5. Ribbon Icon and Commands
		this.addRibbonIcon('file-code', 'Open YAML Editor', () => {
			this.activateYamlEditorView();
		});

		this.addCommand({
			id: 'open-yaml-editor',
			name: 'Open YAML Editor',
			callback: () => {
				this.activateYamlEditorView();
			},
		});

		this.registerEditorExtension(
			createCardGutter(
				(cardIndex) => {
					this.highlightCardLines(cardIndex);
					// improved: only sync if open, don't force focus
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
						// Only log if we really can't find the file and it's unexpected
						// console.warn('[Arete Gutter] No file in editorInfoField');
						return null;
					}

					const cache = this.statsService.getCache();
					const conceptStats = cache.concepts[file.path];

					// DEBUG: Log first 10 cards and the requested NID
					if (nid) {
						console.log(
							`[Arete Debug] Looking up NID: ${nid} (type: ${typeof nid}) for file: ${file.name}`,
						);
						if (conceptStats?.cardStats) {
							const keys = Object.keys(conceptStats.cardStats);
							if (!conceptStats.cardStats[nid]) {
								console.warn(
									`[Arete Debug] KEY NOT FOUND. Available keys sample: ${keys.slice(0, 5).join(', ')}`,
								);
								console.warn(
									`[Arete Debug] Type of search key: ${typeof nid}, Type of first available key: ${typeof keys[0]}`,
								);
							}
						}
					}

					if (!conceptStats || !conceptStats.cardStats) return null;

					if (cid && conceptStats.cardStats[cid]) return conceptStats.cardStats[cid];
					if (nid && conceptStats.cardStats[nid]) return conceptStats.cardStats[nid];

					return null;
				},
				this.settings.stats_algorithm,
			),
		);

		// 7. Sync on Save (Debounced)
		this.registerEvent(
			this.app.vault.on('modify', (file) => {
				if (!this.settings.sync_on_save) return;
				if (!file.path.endsWith('.md')) return;

				// Clear existing timeout
				if (this.syncOnSaveTimeout) {
					clearTimeout(this.syncOnSaveTimeout);
				}

				// Debounce sync
				this.syncOnSaveTimeout = setTimeout(async () => {
					const vaultAdapter = this.app.vault.adapter as FileSystemAdapter;
					const basePath = vaultAdapter.getBasePath ? vaultAdapter.getBasePath() : null;
					if (basePath) {
						const fullPath = path.join(basePath, file.path);
						if (this.settings.debug_mode) {
							console.log('[Arete] Sync on save triggered for:', file.path);
						}
						await this.runSync(false, fullPath, false);
					}
				}, this.settings.sync_on_save_delay);
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
				if (view?.focusCard) {
					view.focusCard(focusCardIndex);
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

		if (state === 'idle') {
			// Show last sync time
			const lastSync = this.settings.last_sync_time;
			if (lastSync) {
				const ago = this.formatTimeAgo(lastSync);
				this.statusBarItem.setText(`🃏 ${ago}`);
				this.statusBarItem.title = 'Click to sync to Anki';
			} else {
				this.statusBarItem.setText('🃏 Arete');
				this.statusBarItem.title = 'Never synced. Click to sync.';
			}
			return;
		}

		if (state === 'syncing') {
			this.statusBarItem.createSpan({ cls: 'arete-sb-icon', text: '🔄 ' });
			this.statusBarItem.createSpan({ text: 'Syncing...' });
		} else if (state === 'success') {
			this.statusBarItem.setText('✅ Synced');
			setTimeout(() => this.updateStatusBar('idle'), 3000);
		} else if (state === 'error') {
			this.statusBarItem.setText('❌ Error');
			this.statusBarItem.title = msg || 'Check logs';
		}
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
			// Import dynamically to avoid circular issues in tests
			const { CheckResultModal } = await import('@presentation/modals/CheckResultModal');
			new CheckResultModal(this.app, this, res, filePath).open();
		} catch (e: any) {
			new Notice(`Error: ${e.message}`);
		}
	}

	async runFix(filePath: string) {
		await this.checkService.runFix(filePath);
	}

	async checkVaultIntegrity() {
		await this.checkService.checkVaultIntegrity();
	}

	async testConfig() {
		await this.checkService.testConfig();
	}

	async loadSettings() {
		const data = await this.loadData();
		this.settings = Object.assign({}, DEFAULT_SETTINGS, data);
		this.statsCache = data?.statsCache;
	}

	async saveSettings() {
		await this.saveData({ ...this.settings, statsCache: this.statsCache });
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
