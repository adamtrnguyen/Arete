import {
	App,
	Command,
	Editor,
	FileSystemAdapter,
	MarkdownFileInfo,
	MarkdownView,
	Notice,
} from 'obsidian';
import * as path from 'path';
import { ConceptStats } from '@/domain/stats';
import { GraphService } from '@application/services/GraphService';
import { StatsService } from '@application/services/StatsService';
import { TemplateRenderer } from '@application/services/TemplateRenderer';

export interface CommandHost {
	app: App;
	graphService: GraphService;
	statsService: StatsService;
	templateRenderer: TemplateRenderer;
	addRibbonIcon(icon: string, title: string, callback: (evt: MouseEvent) => any): HTMLElement;
	addCommand(command: Command): Command;
	runSync(prune?: boolean, targetPath?: string | null, force?: boolean): Promise<void>;
	runCheck(filePath: string): Promise<void>;
	checkVaultIntegrity(): Promise<void>;
	activateDashboardView(): Promise<void>;
	activateLocalGraphView(cardId?: string): Promise<void>;
	activateGlobalGraphView(): Promise<void>;
	activateYamlEditorView(focusCardIndex?: number): Promise<void>;
	onStatsRefreshed(results: ConceptStats[]): Promise<void>;
}

export function registerPluginCommands(plugin: CommandHost): void {
	// Ribbon icons: only the two daily-driver entry points.
	// Force sync, graph views, and YAML editor remain accessible via the
	// command palette; they depend on being in an Arete file.
	plugin.addRibbonIcon('sheets-in-box', 'Sync to Anki (Arete)', (_evt: MouseEvent) => {
		void plugin.runSync();
	});

	plugin.addRibbonIcon('layout-dashboard', 'Arete dashboard', (_evt: MouseEvent) => {
		void plugin.activateDashboardView();
	});

	plugin.addCommand({
		id: 'arete-sync',
		name: 'Sync',
		callback: () => {
			void plugin.runSync();
		},
	});

	plugin.addCommand({
		id: 'arete-check-file',
		name: 'Check current file',
		editorCallback: (_editor: Editor, view: MarkdownView | MarkdownFileInfo) => {
			if (view.file) {
				const vaultAdapter = plugin.app.vault.adapter as FileSystemAdapter;
				const basePath = vaultAdapter.getBasePath ? vaultAdapter.getBasePath() : null;
				if (basePath) {
					const fullPath = path.join(basePath, view.file.path);
					void plugin.runCheck(fullPath);
				} else {
					new Notice('Cannot determine the vault path.');
				}
			}
		},
	});

	plugin.addCommand({
		id: 'arete-check-integrity',
		name: 'Debug: vault integrity check',
		callback: () => {
			void plugin.checkVaultIntegrity();
		},
	});

	plugin.addCommand({
		id: 'arete-sync-current-file',
		name: 'Sync current file',
		callback: () => {
			const activeFile = plugin.app.workspace.getActiveFile();
			if (activeFile) {
				const vaultAdapter = plugin.app.vault.adapter as FileSystemAdapter;
				const basePath = vaultAdapter.getBasePath ? vaultAdapter.getBasePath() : null;
				if (basePath) {
					const maxPath = path.join(basePath, activeFile.path);
					void plugin.runSync(false, maxPath, true);
				} else {
					new Notice('Cannot resolve the file path.');
				}
			} else {
				new Notice('No active file to sync.');
			}
		},
	});

	plugin.addCommand({
		id: 'arete-sync-prune',
		name: 'Sync (prune deleted cards)',
		callback: () => {
			void plugin.runSync(true);
		},
	});

	plugin.addCommand({
		id: 'arete-sync-force-all',
		name: 'Sync (force re-upload all)',
		callback: () => {
			void plugin.runSync(false, null, true);
		},
	});

	plugin.addCommand({
		id: 'arete-open-dashboard',
		name: 'Open dashboard',
		callback: () => {
			void plugin.activateDashboardView();
		},
	});

	plugin.addCommand({
		id: 'arete-graph-clear',
		name: 'Graph: clear retention tags',
		callback: async () => {
			await plugin.graphService.clearAllTags();
		},
	});

	plugin.addCommand({
		id: 'arete-open-local-graph',
		name: 'Open local graph',
		callback: () => {
			void plugin.activateLocalGraphView();
		},
	});

	plugin.addCommand({
		id: 'arete-open-global-graph',
		name: 'Open global graph',
		callback: () => {
			void plugin.activateGlobalGraphView();
		},
	});

	plugin.addCommand({
		id: 'arete-sync-stats',
		name: 'Sync stats (refresh Anki data)',
		callback: async () => {
			new Notice('Refreshing stats...');
			try {
				const results = await plugin.statsService.refreshStats();
				await plugin.onStatsRefreshed(results);
				new Notice('Stats refreshed.');
			} catch (err) {
				console.error('[Arete] Failed to refresh stats:', err);
				new Notice('Failed to refresh stats, check that Anki is running.');
			}
		},
	});

	plugin.addCommand({
		id: 'arete-reload-note-types',
		name: 'Reload note types (card preview)',
		callback: () => {
			plugin.templateRenderer.clearModelCache();
			new Notice('Note types will be fetched from Anki on the next preview.');
		},
	});

	plugin.addCommand({
		id: 'open-yaml-editor',
		name: 'Open YAML editor',
		callback: () => {
			void plugin.activateYamlEditorView();
		},
	});
}
