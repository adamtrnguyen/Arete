import {
	App,
	ButtonComponent,
	ExtraButtonComponent,
	ItemView,
	WorkspaceLeaf,
	setIcon,
	Notice,
	MarkdownView,
	TFile,
	setTooltip,
} from 'obsidian';
import {
	ConceptStats,
	StatsNode,
	difficultyOutOfTen,
	metricStates,
	uniqueCardStats,
} from '@/domain/stats';
import type { MetricState } from '@/domain/stats';
import { flagClass } from '@/presentation/cardStateClass';
import { CardStatsModal } from '@/presentation/modals/CardStatsModal';
import { BrokenReference, LinkCheckerService } from '@application/services/LinkCheckerService';
import { AretePluginSettings } from '@/domain/settings';
import { StatsService } from '@application/services/StatsService';
import { LeechService } from '@application/services/LeechService';
import { AreteClient } from '@infrastructure/arete/AreteClient';

/** What this view needs from the plugin (main.ts satisfies it; no import of main). */
export interface DashboardHost {
	app: App;
	settings: AretePluginSettings;
	statsService: StatsService;
	linkCheckerService: LinkCheckerService;
	leechService: LeechService;
	areteClient: AreteClient;
	saveSettings(): Promise<void>;
	saveStats(): Promise<void>;
	/** Persist the `ui_expanded_*` settings soon; repeated calls coalesce into one write. */
	requestSaveUiState(): void;
}

export const DASHBOARD_VIEW_TYPE = 'arete-stats-view';

const INTEGRITY_TYPE_LABEL: Record<BrokenReference['type'], string> = {
	link: 'Broken link',
	image: 'Broken image',
	'invalid-yaml': 'Invalid YAML',
};

type DashboardTab = 'overview' | 'leeches' | 'integrity' | 'queue-builder';

export class DashboardView extends ItemView {
	plugin: DashboardHost;
	activeTab: DashboardTab = 'overview';

	// Overview State
	expandedConcepts: Set<string>;
	expandedDecks: Set<string>;
	// Deck tree for one stats-cache `concepts` map; rebuilt only when the map is replaced.
	private aggregated: { concepts: object; root: StatsNode } | null = null;
	// Integrity State
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
		// Auto-load stats if cache is empty
		if (Object.keys(this.plugin.statsService.getCache().concepts).length === 0) {
			// Awaited, so the first render has data instead of an empty state.
			new Notice('Syncing Anki stats...');
			await this.plugin.statsService.refreshStats();
		}
		this.render();
	}

	async render() {
		const container = this.containerEl.children[1] as HTMLElement;
		container.empty();
		container.addClass('arete-dashboard-container');

		this.renderHeader(container as HTMLElement);
		this.renderTabs(container as HTMLElement);

		const contentEl = container.createDiv({ cls: 'arete-dashboard-content' });

		switch (this.activeTab) {
			case 'overview':
				this.renderOverview(contentEl);
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

		// Global Stats (Quick Summary)
		const stats = this.plugin.statsService.getCache();
		// naive global aggregation
		let totalCards = 0;
		let totalLeeches = 0;
		Object.values(stats.concepts).forEach((c) => {
			totalCards += c.totalCards;
			// Leeches are the problematic cards the stats cache counts.
			totalLeeches += c.problematicCardsCount;
		});

		const statsGroup = header.createDiv({ cls: 'arete-header-stats' });

		statsGroup.createSpan({ text: `${totalCards} cards` });

		const leechSpan = statsGroup.createSpan({ text: `${totalLeeches} issues` });
		if (totalLeeches > 0) leechSpan.addClass('mod-error');

		// Refresh Button
		const refreshBtn = new ExtraButtonComponent(header)
			.setIcon('refresh-cw')
			.setTooltip('Sync and refresh stats');
		refreshBtn.onClick(async () => {
			refreshBtn.setDisabled(true);
			try {
				await this.plugin.statsService.refreshStats();
				await this.plugin.saveStats();
				this.render();
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
				this.render();
			};
		});
	}

	// --- 1. OVERVIEW TAB ---
	async renderOverview(container: HTMLElement) {
		container.empty(); // Clear previous render to prevent duplication

		// Cached data only: refreshStats() here would re-fetch on every expand/collapse click.
		const conceptsMap = this.plugin.statsService.getCache().concepts;
		const concepts = Object.values(conceptsMap);

		if (concepts.length === 0) {
			this.renderEmptyState(
				container,
				'No statistics available. Please click Refresh to sync.',
			);
			return;
		}

		// Use the service to get the pre-calculated tree
		if (this.aggregated?.concepts !== conceptsMap) {
			const root = this.plugin.statsService.getAggregatedStats(concepts);
			this.aggregated = { concepts: conceptsMap, root };
		}
		const root = this.aggregated.root;

		// Render Tree
		const list = container.createDiv({ cls: 'arete-deck-list' });

		// Render children directly to avoid abstract 'Vault' root node
		if (root.children.length === 0) {
			this.renderEmptyState(container, 'No stats available.');
		} else {
			root.children.forEach((child) => {
				this.renderDeckNode(list, child, 0);
			});
		}
	}

	renderDeckNode(container: HTMLElement, node: StatsNode, depth: number) {
		const deckContainer = container.createDiv();
		const header = deckContainer.createDiv({ cls: 'arete-deck-header' });
		// The indent grows with the depth, so it is the one runtime-computed style.
		header.style.paddingLeft = `${8 + depth * 16}px`;
		// Highlight if issues
		if (node.problematicCount > 0) header.addClass('mod-error');

		const icon = header.createSpan({ cls: 'arete-deck-icon' });
		// If leaf, key is filePath, else deckName. Expanded set keys match this logic.
		const expansionKey = node.isLeaf ? node.filePath : node.deckName;

		if (!node.isLeaf) {
			setIcon(icon, this.expandedDecks.has(expansionKey) ? 'chevron-down' : 'chevron-right');
		} else {
			setIcon(icon, 'file-text');
			icon.addClass('mod-leaf');
		}

		const title = header.createSpan({ text: node.title, cls: 'arete-deck-title' });
		if (node.isLeaf) title.addClass('mod-leaf');

		const statsContainer = header.createDiv({ cls: 'arete-deck-stats' });

		// A deck row judges its averages and its lapse total with the same cut-offs as a card.
		const states = metricStates(
			{
				lapses: node.lapses,
				difficulty: node.difficulty,
				stability: node.stability,
				retrievability: node.retrievability,
			},
			this.plugin.settings,
		);
		const stat = (text: string, column: string, state?: MetricState) => {
			const span = statsContainer.createSpan({
				text,
				cls: `arete-deck-stat mod-${column}`,
			});
			const flag = state ? flagClass(state) : null;
			if (flag) span.addClass(flag);
		};

		// 0. Card Count
		stat(`${node.count} cards`, 'count');

		// 1. Difficulty (Avg), stored 0..1; scale only to display it
		stat(
			node.difficulty != null ? `${difficultyOutOfTen(node.difficulty).toFixed(1)} D` : '-',
			'difficulty',
			states.difficulty,
		);

		// 1b. Stability (Avg)
		stat(
			node.stability != null ? `${node.stability.toFixed(1)}d stab` : '-',
			'stability',
			states.stability,
		);

		// 1c. Retrievability (Avg)
		stat(
			node.retrievability != null ? `${(node.retrievability * 100).toFixed(0)}% ret` : '-',
			'retrievability',
			states.retrievability,
		);

		// 2. Lapses
		stat(`${node.lapses} laps`, 'lapses', states.lapses);

		// 3. Score (Issues)
		if (node.problematicCount > 0) {
			statsContainer.createSpan({
				text: `${node.problematicCount} issues`,
				cls: 'arete-badge mod-error',
			});
		}

		// Children go in their own element, so a toggle rebuilds only this subtree.
		const body = deckContainer.createDiv();

		header.onclick = () => {
			const expanded = node.isLeaf ? this.expandedConcepts : this.expandedDecks;
			if (expanded.has(expansionKey)) {
				expanded.delete(expansionKey);
			} else {
				expanded.add(expansionKey);
			}
			this.persistExpansion();

			// Stats refreshed since the tree was built: rebuild it all with the new numbers.
			if (this.aggregated?.concepts !== this.plugin.statsService.getCache().concepts) {
				const overviewContainer = container.closest(
					'.arete-dashboard-content',
				) as HTMLElement | null;
				if (overviewContainer) this.renderOverview(overviewContainer);
				return;
			}

			if (!node.isLeaf) {
				setIcon(icon, expanded.has(expansionKey) ? 'chevron-down' : 'chevron-right');
			}
			body.empty();
			this.renderNodeBody(body, node, depth);
		};

		// Leaf name click action (go to file)
		if (node.isLeaf) {
			title.addClass('arete-clickable');
			title.onclick = (e) => {
				e.stopPropagation();
				this.openFile(node.filePath);
			};
		}

		this.renderNodeBody(body, node, depth);
	}

	/** Render a node's expanded content (child decks, or a file's cards) into `body`. */
	private renderNodeBody(body: HTMLElement, node: StatsNode, depth: number) {
		if (!node.isLeaf && this.expandedDecks.has(node.deckName)) {
			// Render children nodes
			Object.values(node.children).forEach((child) =>
				this.renderDeckNode(body, child, depth + 1),
			);
		} else if (node.isLeaf && this.expandedConcepts.has(node.filePath)) {
			// This is a file, find ConceptStats to render individual cards
			const concepts = this.plugin.statsService.getCache().concepts[node.filePath];
			if (concepts) {
				const cardListWrapper = body.createDiv({ cls: 'arete-card-list-wrapper' });
				cardListWrapper.style.paddingLeft = `${24 + depth * 16}px`;
				this.renderCardList(cardListWrapper, concepts);
			}
		}
	}

	/** Copy the expansion sets into settings and ask the plugin to persist them. */
	private persistExpansion() {
		this.plugin.settings.ui_expanded_decks = [...this.expandedDecks];
		this.plugin.settings.ui_expanded_concepts = [...this.expandedConcepts];
		this.plugin.requestSaveUiState();
	}

	renderCardList(container: HTMLElement, concept: ConceptStats) {
		const cardList = container.createDiv({ cls: 'arete-card-list' });

		const cards = uniqueCardStats(concept.cardStats);

		if (cards.length === 0) {
			cardList.createDiv({ text: 'No card stats synced.', cls: 'arete-card-list-empty' });
		} else {
			// Header
			const header = cardList.createDiv({ cls: 'arete-card-list-header' });
			header.createSpan({ text: 'Card question', cls: 'arete-card-question' });
			header.createSpan({ text: 'Diff', cls: 'arete-card-col mod-difficulty' });
			header.createSpan({ text: 'Stab', cls: 'arete-card-col mod-stability' });
			header.createSpan({ text: 'Gain', cls: 'arete-card-col mod-growth' });
			header.createSpan({ text: 'Ret', cls: 'arete-card-col mod-retrievability' });
			header.createSpan({ text: 'Lapses', cls: 'arete-card-col mod-lapses' });
			const hFlags = header.createSpan({ text: '!', cls: 'arete-card-col mod-flags' });
			setTooltip(hFlags, 'Flags, such as overlearning');

			cards
				.sort((a, b) => b.lapses - a.lapses)
				.forEach((card) => {
					const cRow = cardList.createDiv({ cls: 'arete-card-row' });

					cRow.onclick = (e) => {
						e.stopPropagation();
						if (card.front) this.goToCard(concept.filePath, card.front);
					};

					try {
						const frontText = card.front
							? card.front.replace(/<[^>]*>?/gm, '')
							: `#${card.cardId}`;
						const qSpan = cRow.createSpan({
							text: frontText,
							cls: 'arete-card-question arete-clickable',
						});
						setTooltip(qSpan, frontText);

						qSpan.onclick = (e) => {
							e.stopPropagation();
							if (card.front) this.goToCard(concept.filePath, card.front);
						};

						// --- Stats Group (Click for Modal) ---
						const sGroup = cRow.createDiv({ cls: 'arete-card-stats-group' });

						sGroup.onclick = (e) => {
							e.stopPropagation();
							new CardStatsModal(this.plugin.app, card, this.plugin.settings).open();
						};

						const states = metricStates(card, this.plugin.settings);
						const col = (column: string, text: string, flag?: string | null) => {
							const span = sGroup.createSpan({
								text,
								cls: `arete-card-col mod-${column}`,
							});
							if (flag) span.addClass(flag);
							return span;
						};

						// 1. Difficulty, stored 0..1; scale only to display it
						col(
							'difficulty',
							card.difficulty ? difficultyOutOfTen(card.difficulty).toFixed(1) : '-',
							card.difficulty ? flagClass(states.difficulty) : null,
						);

						// 2. Stability
						col(
							'stability',
							card.stability ? card.stability.toFixed(0) : '-',
							card.stability ? flagClass(states.stability) : null,
						);

						// 3. Interval growth: green unless it is below the cut-off
						col(
							'growth',
							card.intervalGrowth != null
								? `x${card.intervalGrowth.toFixed(1)}`
								: '-',
							card.intervalGrowth != null
								? (flagClass(states.intervalGrowth) ?? 'mod-success')
								: null,
						);

						// 4. Retrievability
						col(
							'retrievability',
							card.retrievability
								? `${(card.retrievability * 100).toFixed(0)}%`
								: '-',
							card.retrievability ? flagClass(states.retrievability) : null,
						);

						// 5. Lapses
						col('lapses', card.lapses.toString(), flagClass(states.lapses));

						// 6. Flags
						const fSpan = col('flags', '');
						if (card.isOverlearning) {
							setIcon(fSpan, 'zap');
							fSpan.addClass('mod-overlearning');
							setTooltip(fSpan, 'Overlearning detected');
						}
					} catch (e) {
						console.error('[Arete] Failed to render a card row:', e);
					}
				});
		}
	}

	// --- 2. LEECHES TAB ---
	async renderLeeches(container: HTMLElement) {
		const leeches = this.plugin.leechService.getLeeches(this.plugin.statsService.getCache());

		if (leeches.length === 0) {
			this.renderEmptyState(container, 'No leeches found! Your deck is healthy.');
			return;
		}

		const table = container.createEl('table', { cls: 'arete-leech-table' });

		const thead = table.createEl('thead');
		const headerRow = thead.createEl('tr');
		['Card (front)', 'Difficulty', 'Lapses', 'Actions'].forEach((text) => {
			headerRow.createEl('th', { text });
		});

		const tbody = table.createEl('tbody');
		leeches.forEach((leech) => {
			const row = tbody.createEl('tr');

			// Front
			const cellFront = row.createEl('td', { cls: 'arete-leech-cell-front' });
			// Truncate
			const frontDiv = cellFront.createDiv({ cls: 'arete-leech-front arete-clickable' });
			frontDiv.textContent = leech.front.replace(/<[^>]*>?/gm, '');
			setTooltip(frontDiv, leech.front);
			frontDiv.onclick = () => this.goToCard(leech.filePath, leech.front);

			const states = metricStates(
				{ lapses: leech.lapses, difficulty: leech.difficulty, ease: leech.ease },
				this.plugin.settings,
			);

			// Diff/Ease (use 'issue' precalc or raw), flagged like the metric it shows
			const cellDiff = row.createEl('td');
			cellDiff.textContent = leech.ease
				? `${(leech.ease / 10).toFixed(0)}% ease`
				: leech.difficulty != null
					? `${difficultyOutOfTen(leech.difficulty).toFixed(1)} diff`
					: '-';
			const diffFlag = flagClass(
				leech.ease ? states.ease : leech.difficulty != null ? states.difficulty : 'none',
			);
			if (diffFlag) cellDiff.addClass(diffFlag);

			// Lapses
			const cellLapses = row.createEl('td');
			cellLapses.textContent = leech.lapses.toString();
			const lapseFlag = flagClass(states.lapses);
			if (lapseFlag) cellLapses.addClass(lapseFlag);

			// Actions
			const cellActions = row.createEl('td', { cls: 'arete-leech-actions' });

			const btnSuspend = new ButtonComponent(cellActions).setButtonText('Suspend');
			btnSuspend.onClick(async () => {
				btnSuspend.setButtonText('...');
				const success = await this.plugin.leechService.suspendCard(leech.cardId);
				if (success) {
					new Notice('Card suspended.');
					row.addClass('is-suspended');
					btnSuspend.setButtonText('Suspended');
					btnSuspend.setDisabled(true);
				} else {
					new Notice('Failed to suspend card.');
					btnSuspend.setButtonText('Suspend');
				}
			});
		});
	}

	// --- 3. INTEGRITY TAB ---
	renderIntegrity(container: HTMLElement) {
		const actionArea = container.createDiv({ cls: 'arete-integrity-actions' });

		const desc = actionArea.createDiv({ cls: 'arete-integrity-desc' });
		const scans = desc.createEl('p');
		scans.createSpan({ text: 'Scans your synced files to find ' });
		scans.createEl('strong', { text: 'broken image embeds' });
		scans.createSpan({ text: ' and ' });
		scans.createEl('strong', { text: 'invalid YAML' });
		scans.createSpan({ text: '.' });
		desc.createEl('p', {
			text: "Use this to ensure your Anki cards don't have broken media or syntax errors.",
		});

		const checkBtn = new ButtonComponent(actionArea)
			.setButtonText(this.isScanning ? 'Scanning vault...' : 'Run integrity check')
			.setCta()
			.setDisabled(this.isScanning);

		checkBtn.onClick(async () => {
			this.isScanning = true;
			this.render(); // Update button state
			try {
				// Scan all markdown files to ensure we catch those with broken YAML (which won't be in stats cache)
				const targetFiles = this.app.vault.getMarkdownFiles();

				this.brokenRefs = await this.plugin.linkCheckerService.checkIntegrity(targetFiles);
				new Notice(`Integrity check complete. Found ${this.brokenRefs.length} issues.`);
			} catch (e) {
				console.error('[Arete] Integrity check failed:', e);
				new Notice('Integrity check failed.');
			} finally {
				this.isScanning = false;
				this.render();
			}
		});

		if (this.brokenRefs) {
			if (this.brokenRefs.length === 0) {
				this.renderSuccess(container, 'No broken links or missing images found!');
			} else {
				const list = container.createDiv();

				this.brokenRefs.forEach((ref) => {
					const item = list.createDiv({ cls: 'arete-integrity-item' });

					const left = item.createDiv();
					const fileLink = left.createSpan({
						text: ref.sourceFile.basename,
						cls: 'arete-integrity-file arete-clickable',
					});
					fileLink.onclick = () => this.app.workspace.getLeaf().openFile(ref.sourceFile);

					left.createSpan({ text: ` → ` });
					const targetSpan = left.createSpan({
						text: ref.linkPath,
						cls: 'arete-integrity-target',
					});
					// Tooltip for original text (debug aid)
					setTooltip(targetSpan, `Original text: "${ref.linkText}"`);

					const right = item.createDiv({ cls: 'arete-integrity-meta' });

					// Show original text if it differs significantly from linkPath
					if (ref.type === 'invalid-yaml' && ref.errorMessage) {
						right.createSpan({
							text: ref.errorMessage,
							cls: 'arete-integrity-error',
						});
					} else if (
						ref.type !== 'invalid-yaml' &&
						ref.linkText !== `[[${ref.linkPath}]]` &&
						ref.linkText !== ref.linkPath
					) {
						right.createSpan({
							text: `"${ref.linkText}"`,
							cls: 'arete-integrity-text',
						});
					}

					const typeBadge = right.createSpan({
						text: INTEGRITY_TYPE_LABEL[ref.type],
						cls: 'arete-badge',
					});

					if (ref.type === 'invalid-yaml') {
						typeBadge.addClass('mod-error');
						setTooltip(
							typeBadge,
							ref.errorMessage ||
								'Obsidian failed to parse frontmatter. Check for syntax errors.',
						);
					}

					// Navigation button
					new ExtraButtonComponent(right)
						.setIcon('external-link')
						.setTooltip('Go to location')
						.onClick(() => this.goToIssue(ref.sourceFile, ref.linkText));
				});
			}
		}
	}

	// --- 4. QUEUE BUILDER TAB ---
	renderQueueBuilder(container: HTMLElement) {
		container.empty();

		// Header
		container.createEl('h3', { text: 'Queue builder' });
		container.createEl('p', {
			text: 'Build a study queue that includes prerequisite cards before your due cards.',
			cls: 'setting-item-description',
		});

		// Controls
		const controls = container.createDiv({ cls: 'arete-queue-controls' });

		// Deck selector
		const deckRow = controls.createDiv({ cls: 'setting-item' });
		deckRow.createDiv({ text: 'Deck filter', cls: 'setting-item-name' });
		const deckSelect = deckRow.createEl('select', { cls: 'dropdown arete-queue-field' });
		deckSelect.createEl('option', { value: '', text: 'All decks' });

		// Load decks async
		this.plugin.areteClient.getDeckNames().then((decks) => {
			decks.forEach((d) => deckSelect.createEl('option', { value: d, text: d }));
		});

		// Depth control
		const depthRow = controls.createDiv({ cls: 'setting-item' });
		depthRow.createDiv({ text: 'Prerequisite depth', cls: 'setting-item-name' });
		const depthInput = depthRow.createEl('input', {
			type: 'number',
			value: '2',
			cls: 'arete-queue-field mod-number',
		});
		depthInput.min = '1';
		depthInput.max = '5';

		// Max cards control
		const maxRow = controls.createDiv({ cls: 'setting-item' });
		maxRow.createDiv({ text: 'Max cards', cls: 'setting-item-name' });
		const maxInput = maxRow.createEl('input', {
			type: 'number',
			value: '50',
			cls: 'arete-queue-field mod-number',
		});
		maxInput.min = '10';
		maxInput.max = '200';

		// Build button
		const buildBtn = new ButtonComponent(controls).setButtonText('Build queue').setCta();
		buildBtn.buttonEl.addClass('arete-queue-build');

		// Results area
		const resultsArea = container.createDiv({ cls: 'arete-queue-results' });

		buildBtn.onClick(async () => {
			buildBtn.setButtonText('Building...').setDisabled(true);

			try {
				const result = await this.plugin.areteClient.buildStudyQueue(
					deckSelect.value || null,
					parseInt(depthInput.value) || 2,
					parseInt(maxInput.value) || 50,
				);

				resultsArea.empty();

				// Summary
				const summary = resultsArea.createDiv({ cls: 'arete-queue-summary' });
				summary.createEl('strong', { text: result.deck });
				summary.createSpan({
					text: ` — ${result.dueCount} due, ${result.totalWithPrereqs} total`,
				});

				// Send button
				const sendBtn = new ButtonComponent(resultsArea).setButtonText('Send to Anki');
				sendBtn.buttonEl.addClass('arete-queue-send');
				sendBtn.onClick(async () => {
					const ok = await this.plugin.areteClient.createQueueDeck(
						result.queue.map((c) => c.id),
					);
					new Notice(ok ? 'Queue sent to Anki.' : 'Failed to create deck.');
				});

				// Queue list
				const list = resultsArea.createDiv({ cls: 'arete-queue-list' });

				result.queue.forEach((card) => {
					const item = list.createDiv({ cls: 'arete-queue-item' });

					item.createSpan({ text: String(card.position), cls: 'arete-queue-pos' });

					if (card.isPrereq) {
						item.createSpan({ text: 'Prerequisite', cls: 'arete-badge mod-accent' });
					}

					const title = item.createSpan({
						text: card.title,
						cls: 'arete-queue-title arete-clickable',
					});
					title.onclick = () => this.openFile(card.file);
				});

				new Notice(`Queue built: ${result.totalWithPrereqs} cards`);
			} catch (e) {
				console.error('[Arete] Failed to build the queue:', e);
				new Notice('Failed to build queue.');
			} finally {
				buildBtn.setButtonText('Build queue').setDisabled(false);
			}
		});
	}

	// --- Helpers ---
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

	async goToCard(filePath: string, frontText: string) {
		await this.openFile(filePath);
		const view = this.app.workspace.getActiveViewOfType(MarkdownView);
		if (view) {
			const editor = view.editor;
			const content = editor.getValue();
			// Normalize newlines and spaces for robust matching
			const cleanFront = frontText.trim().replace(/\s+/g, ' ');
			const index = content.indexOf(cleanFront.split(' ')[0]); // Try finding first token
			if (index >= 0) {
				const pos = editor.offsetToPos(index);
				editor.setCursor(pos);
				editor.scrollIntoView({ from: pos, to: pos }, true);
			}
		}
	}

	async goToIssue(file: TFile, searchText: string) {
		await this.app.workspace.getLeaf().openFile(file);
		const view = this.app.workspace.getActiveViewOfType(MarkdownView);
		if (view) {
			const editor = view.editor;
			const content = editor.getValue();
			// Remove context prefix like "Card #1: " if present
			const cleanText = searchText.replace(/^Card #\d+: /, '');
			const index = content.indexOf(cleanText);
			if (index >= 0) {
				const pos = editor.offsetToPos(index);
				editor.setCursor(pos);
				editor.scrollIntoView({ from: pos, to: pos }, true);
				// Highlight selection
				editor.setSelection(pos, editor.offsetToPos(index + cleanText.length));
			} else {
				new Notice(`Could not automatically find text: "${cleanText.substring(0, 20)}..."`);
			}
		}
	}
}
