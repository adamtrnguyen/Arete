import {
	App,
	ExtraButtonComponent,
	ItemView,
	WorkspaceLeaf,
	setIcon,
	Notice,
	MarkdownView,
	TFile,
} from 'obsidian';
import { ConceptStats, StatsNode, cardState, uniqueCardStats } from '@/domain/stats';
import { StudyQueueClient } from '@/domain/ports';
import { AretePluginSettings } from '@/domain/settings';
import { BrokenReference, LinkCheckerService } from '@application/services/LinkCheckerService';
import { StatsService } from '@application/services/StatsService';
import { LeechService } from '@application/services/LeechService';
import {
	OverviewTabContext,
	renderCardListView,
	renderDeckNodeView,
} from '@/presentation/views/dashboard/OverviewTab';
import { renderLeechesTab } from '@/presentation/views/dashboard/LeechesTab';
import { renderIntegrityTab } from '@/presentation/views/dashboard/IntegrityTab';
import { renderQueueBuilderTab } from '@/presentation/views/dashboard/QueueBuilderTab';

/** What this view needs from the plugin (main.ts satisfies it; no import of main). */
export interface DashboardHost {
	app: App;
	settings: AretePluginSettings;
	statsService: StatsService;
	linkCheckerService: LinkCheckerService;
	leechService: LeechService;
	areteClient: StudyQueueClient;
	saveStats(): Promise<void>;
	/** Persist the `ui_expanded_*` settings soon; repeated calls coalesce into one write. */
	requestSaveUiState(): void;
}

export const DASHBOARD_VIEW_TYPE = 'arete-stats-view';

type DashboardTab = 'overview' | 'leeches' | 'integrity' | 'queue-builder';

export class DashboardView extends ItemView {
	plugin: DashboardHost;
	activeTab: DashboardTab = 'overview';

	expandedConcepts: Set<string>;
	expandedDecks: Set<string>;
	private aggregated: { concepts: object; thresholds: string; root: StatsNode } | null = null;
	private queueBuilderRevision = 0;

	brokenRefs: BrokenReference[] | null = null;
	isScanning = false;

	constructor(leaf: WorkspaceLeaf, plugin: DashboardHost) {
		super(leaf);
		this.plugin = plugin;
		this.expandedDecks = new Set(this.plugin.settings.ui_expanded_decks || []);
		this.expandedConcepts = new Set(this.plugin.settings.ui_expanded_concepts || []);
	}

	getViewType() {
		return DASHBOARD_VIEW_TYPE;
	}

	getDisplayText() {
		return 'Arete dashboard';
	}

	getIcon() {
		return 'layout-dashboard';
	}

	async onOpen() {
		if (Object.keys(this.plugin.statsService.getCache().concepts).length === 0) {
			new Notice('Syncing Anki stats...');
			try {
				await this.plugin.statsService.refreshStats();
				await this.plugin.saveStats();
			} catch (e) {
				console.error('[Arete] Failed to sync Anki stats:', e);
				new Notice('Failed to refresh stats, check that Anki is running.');
			}
		}
		await this.render();
	}

	async onClose() {
		this.queueBuilderRevision++;
	}

	private thresholdSnapshot(): string {
		const s = this.plugin.settings;
		return JSON.stringify([
			s.stats_algorithm,
			s.stats_lapse_threshold,
			s.stats_difficulty_threshold,
			s.stats_ease_threshold,
		]);
	}

	async render() {
		const container = this.contentEl;
		container.empty();
		container.addClass('arete-dashboard-container');

		this.renderHeader(container);
		this.renderTabs(container);

		const contentEl = container.createDiv({ cls: 'arete-dashboard-content' });

		switch (this.activeTab) {
			case 'overview':
				await this.renderOverview(contentEl);
				break;
			case 'leeches':
				await this.renderLeeches(contentEl);
				break;
			case 'integrity':
				this.renderIntegrity(contentEl);
				break;
			case 'queue-builder':
				this.renderQueueBuilder(contentEl);
				break;
		}
	}

	renderHeader(container: HTMLElement) {
		const header = container.createDiv({ cls: 'arete-dashboard-header' });
		header.createEl('h3', { text: 'Arete dashboard', cls: 'arete-dashboard-title' });

		const stats = this.plugin.statsService.getCache();
		let totalCards = 0;
		let totalLeeches = 0;
		let totalNoData = 0;
		Object.values(stats.concepts).forEach((c) => {
			for (const card of uniqueCardStats(c.cardStats)) {
				if (cardState(card, true, this.plugin.settings).state === 'nodata') totalNoData++;
			}
			totalCards += c.totalCards;
			totalLeeches += c.problematicCardsCount;
		});

		const statsGroup = header.createDiv({ cls: 'arete-header-stats' });
		statsGroup.createSpan({ text: `${totalCards} cards` });

		const leechSpan = statsGroup.createSpan({ text: `${totalLeeches} issues` });
		if (totalLeeches > 0) leechSpan.addClass('mod-error');

		statsGroup.createSpan({ text: `${totalNoData} no data`, cls: 'mod-nodata' });

		const refreshBtn = new ExtraButtonComponent(header)
			.setIcon('refresh-cw')
			.setTooltip('Sync and refresh stats');
		refreshBtn.onClick(async () => {
			refreshBtn.setDisabled(true);
			try {
				await this.plugin.statsService.refreshStats();
				await this.plugin.saveStats();
				await this.render();
			} catch (e) {
				console.error('[Arete] Failed to refresh stats:', e);
				new Notice('Failed to refresh stats, check that Anki is running.');
			} finally {
				refreshBtn.setDisabled(false);
			}
		});
	}

	renderTabs(container: HTMLElement) {
		const tabBar = container.createDiv({ cls: 'arete-tab-bar' });

		const tabs: { id: DashboardTab; label: string; icon: string }[] = [
			{ id: 'overview', label: 'Overview', icon: 'bar-chart-2' },
			{ id: 'leeches', label: 'Leeches', icon: 'flame' },
			{ id: 'integrity', label: 'Integrity', icon: 'link' },
			{ id: 'queue-builder', label: 'Queue builder', icon: 'list-ordered' },
		];

		tabs.forEach((tab) => {
			const btn = tabBar.createDiv({ cls: 'arete-tab-btn' });
			if (this.activeTab === tab.id) btn.addClass('is-active');

			const iconSpan = btn.createSpan();
			setIcon(iconSpan, tab.icon);
			btn.createSpan({ text: tab.label });

			btn.onclick = () => {
				this.activeTab = tab.id;
				void this.render();
			};
		});
	}

	private overviewContext(): OverviewTabContext {
		return {
			app: this.plugin.app,
			settings: this.plugin.settings,
			statsService: this.plugin.statsService,
			expandedDecks: this.expandedDecks,
			expandedConcepts: this.expandedConcepts,
			isCacheCurrent: () =>
				this.aggregated?.concepts === this.plugin.statsService.getCache().concepts &&
				this.aggregated?.thresholds === this.thresholdSnapshot(),
			rebuildOverview: (el) => {
				void this.renderOverview(el);
			},
			persistExpansion: () => this.persistExpansion(),
			openFile: (filePath) => {
				void this.openFile(filePath);
			},
			goToCard: (filePath, frontText, noteId) => {
				void this.goToCard(filePath, frontText, noteId);
			},
		};
	}

	async renderOverview(container: HTMLElement) {
		container.empty();

		const conceptsMap = this.plugin.statsService.getCache().concepts;
		const concepts = Object.values(conceptsMap);

		if (concepts.length === 0) {
			this.renderEmptyState(
				container,
				'No statistics available. Please click Refresh to sync.',
			);
			return;
		}

		const thresholds = this.thresholdSnapshot();
		if (
			this.aggregated?.concepts !== conceptsMap ||
			this.aggregated.thresholds !== thresholds
		) {
			const root = this.plugin.statsService.getAggregatedStats(concepts);
			this.aggregated = { concepts: conceptsMap, thresholds, root };
		}
		const root = this.aggregated.root;

		const list = container.createDiv({ cls: 'arete-deck-list' });

		if (root.children.length === 0) {
			this.renderEmptyState(container, 'No stats available.');
		} else {
			const ctx = this.overviewContext();
			root.children.forEach((child) => {
				renderDeckNodeView(ctx, list, child, 0);
			});
		}
	}

	renderDeckNode(container: HTMLElement, node: StatsNode, depth: number) {
		renderDeckNodeView(this.overviewContext(), container, node, depth);
	}

	renderCardList(container: HTMLElement, concept: ConceptStats) {
		renderCardListView(this.overviewContext(), container, concept);
	}

	private persistExpansion() {
		this.plugin.settings.ui_expanded_decks = [...this.expandedDecks];
		this.plugin.settings.ui_expanded_concepts = [...this.expandedConcepts];
		this.plugin.requestSaveUiState();
	}

	async renderLeeches(container: HTMLElement) {
		await renderLeechesTab(
			{
				settings: this.plugin.settings,
				statsService: this.plugin.statsService,
				leechService: this.plugin.leechService,
				renderEmptyState: (el, msg) => this.renderEmptyState(el, msg),
				goToCard: (filePath, frontText, noteId) => {
					void this.goToCard(filePath, frontText, noteId);
				},
			},
			container,
		);
	}

	renderIntegrity(container: HTMLElement) {
		renderIntegrityTab(
			{
				app: this.app,
				linkCheckerService: this.plugin.linkCheckerService,
				getBrokenRefs: () => this.brokenRefs,
				setBrokenRefs: (refs) => {
					this.brokenRefs = refs;
				},
				isScanning: () => this.isScanning,
				setScanning: (scanning) => {
					this.isScanning = scanning;
				},
				rerender: () => {
					void this.render();
				},
				renderSuccess: (el, msg) => this.renderSuccess(el, msg),
				goToIssue: (file, searchText) => {
					void this.goToIssue(file, searchText);
				},
			},
			container,
		);
	}

	renderQueueBuilder(container: HTMLElement) {
		const revision = ++this.queueBuilderRevision;
		renderQueueBuilderTab(
			{
				client: this.plugin.areteClient,
				isCurrentRevision: () => revision === this.queueBuilderRevision,
				openFile: (filePath) => {
					void this.openFile(filePath);
				},
			},
			container,
		);
	}

	renderEmptyState(container: HTMLElement, message: string) {
		const empty = container.createDiv({ cls: 'arete-empty-state' });
		setIcon(empty.createDiv(), 'search');
		empty.createDiv({ text: message, cls: 'arete-empty-message' });
	}

	renderSuccess(container: HTMLElement, message: string) {
		const box = container.createDiv({ cls: 'arete-success-state' });
		setIcon(box.createDiv(), 'check-circle');
		box.createDiv({ text: message, cls: 'arete-empty-message' });
	}

	async openFile(filePath: string) {
		const file = this.app.vault.getAbstractFileByPath(filePath);
		if (file instanceof TFile) await this.app.workspace.getLeaf().openFile(file);
	}

	async goToCard(filePath: string, frontText: string, noteId?: number) {
		await this.openFile(filePath);
		const view = this.app.workspace.getActiveViewOfType(MarkdownView);
		if (view) {
			const editor = view.editor;
			const content = editor.getValue();
			const index = this.findCardOffset(content, frontText, noteId);
			if (index >= 0) {
				const pos = editor.offsetToPos(index);
				editor.setCursor(pos);
				editor.scrollIntoView({ from: pos, to: pos }, true);
			}
		}
	}

	private findCardOffset(content: string, frontText: string, noteId?: number): number {
		const cleanFront = frontText.trim().replace(/\s+/g, ' ');
		if (cleanFront) {
			const escapedTokens = cleanFront
				.split(' ')
				.map((tok) => tok.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'));
			const fullRegex = new RegExp(escapedTokens.join('\\s+'));
			const fullMatch = fullRegex.exec(content);
			if (fullMatch) return fullMatch.index;

			const firstLine = frontText
				.split(/\r?\n/)
				.map((line) => line.trim())
				.find(Boolean);
			if (firstLine) {
				const lineIdx = content.indexOf(firstLine);
				if (lineIdx >= 0) return lineIdx;
			}
		}

		if (noteId != null) {
			const nidIdx = content.indexOf(String(noteId));
			if (nidIdx >= 0) return nidIdx;
		}

		if (cleanFront) {
			const firstToken = cleanFront.split(' ')[0];
			if (firstToken) {
				return content.indexOf(firstToken);
			}
		}
		return -1;
	}

	async goToIssue(file: TFile, searchText: string) {
		await this.app.workspace.getLeaf().openFile(file);
		const view = this.app.workspace.getActiveViewOfType(MarkdownView);
		if (view) {
			const editor = view.editor;
			const content = editor.getValue();
			const cleanText = searchText.replace(/^Card #\d+:\s*/, '');
			const idx = content.indexOf(cleanText);
			if (idx !== -1) {
				const pos = editor.offsetToPos(idx);
				editor.setCursor(pos);
				editor.scrollIntoView({ from: pos, to: pos }, true);
				editor.setSelection(pos, editor.offsetToPos(idx + cleanText.length));
			} else {
				new Notice(`Could not automatically find text: "${cleanText.substring(0, 20)}..."`);
			}
		}
	}
}
