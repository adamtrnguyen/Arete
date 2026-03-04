import { ItemView, WorkspaceLeaf, setIcon, Notice, MarkdownView, TFile } from 'obsidian';
import AretePlugin from '@/main';
import { ConceptStats, StatsNode } from '@/domain/stats';
import { CardStatsModal } from '@/presentation/modals/CardStatsModal';
import { BrokenReference } from '@application/services/LinkCheckerService';

export const DASHBOARD_VIEW_TYPE = 'arete-stats-view';

type DashboardTab = 'overview' | 'leeches' | 'integrity' | 'queue-builder';

export class DashboardView extends ItemView {
	plugin: AretePlugin;
	activeTab: DashboardTab = 'overview';

	// Overview State
	expandedConcepts: Set<string>;
	expandedDecks: Set<string>;
	// Integrity State
	brokenRefs: BrokenReference[] | null = null;
	isScanning = false;

	constructor(leaf: WorkspaceLeaf, plugin: AretePlugin) {
		super(leaf);
		this.plugin = plugin;
		this.expandedDecks = new Set(this.plugin.settings.ui_expanded_decks || []);
		this.expandedConcepts = new Set(this.plugin.settings.ui_expanded_concepts || []);
	}

	getViewType() {
		return DASHBOARD_VIEW_TYPE;
	}

	getDisplayText() {
		return 'Arete Dashboard';
	}

	getIcon() {
		return 'layout-dashboard';
	}

	async onOpen() {
		// Auto-load stats if cache is empty
		if (Object.keys(this.plugin.statsService.getCache().concepts).length === 0) {
			// Don't await here directly if we want instant UI, but we want to avoid empty state.
			// Let's show a loading notice and render, then re-render when done?
			// Or just await. Ensuring data on open is better UX than empty state.
			new Notice('Syncing Anki stats...');
			await this.plugin.statsService.refreshStats();
		}
		this.render();
	}

	async render() {
		const container = this.containerEl.children[1] as HTMLElement;
		container.empty();
		container.addClass('arete-dashboard-container');

		// Use flex column for full height
		container.style.display = 'flex';
		container.style.flexDirection = 'column';
		container.style.height = '100%';
		container.style.overflow = 'hidden';

		this.renderHeader(container as HTMLElement);
		this.renderTabs(container as HTMLElement);

		const contentEl = container.createDiv({ cls: 'arete-dashboard-content' });
		contentEl.style.flex = '1';
		contentEl.style.overflowY = 'auto';
		contentEl.style.padding = '1rem';

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
		header.style.padding = '1rem';
		header.style.borderBottom = '1px solid var(--background-modifier-border)';
		header.style.display = 'flex';
		header.style.justifyContent = 'space-between';
		header.style.alignItems = 'center';
		header.style.background = 'var(--background-secondary)';

		const titleMsg = header.createEl('h3', { text: 'Arete Dashboard' });
		titleMsg.style.margin = '0 auto 0 0'; // Left align

		// Global Stats (Quick Summary)
		const stats = this.plugin.statsService.getCache();
		// naive global aggregation
		let totalCards = 0;
		let totalLeeches = 0;
		Object.values(stats.concepts).forEach((c) => {
			totalCards += c.totalCards;
			// For now, let's say leeches are cards with > 8 lapses OR manually tagged
			// Actually we can count problematic cards from cache
			totalLeeches += c.problematicCardsCount;
		});

		const statsGroup = header.createDiv({ cls: 'arete-header-stats' });
		statsGroup.style.display = 'flex';
		statsGroup.style.gap = '1rem';
		statsGroup.style.fontSize = '0.9em';
		statsGroup.style.color = 'var(--text-muted)';

		statsGroup.createSpan({ text: `${totalCards} Cards` });

		const leechSpan = statsGroup.createSpan({ text: `${totalLeeches} Issues` });
		if (totalLeeches > 0) leechSpan.style.color = 'var(--color-red)';

		// Refresh Button
		const refreshBtn = header.createEl('button', { cls: 'arete-icon-btn' });
		setIcon(refreshBtn, 'refresh-cw');
		refreshBtn.title = 'Sync & Refresh Stats';
		refreshBtn.onclick = async () => {
			refreshBtn.addClass('arete-spin');
			await this.plugin.statsService.refreshStats();
			await this.plugin.saveStats();
			this.render();
			refreshBtn.removeClass('arete-spin');
		};
	}

	renderTabs(container: HTMLElement) {
		const tabBar = container.createDiv({ cls: 'arete-tab-bar' });
		tabBar.style.display = 'flex';
		tabBar.style.gap = '2px';
		tabBar.style.padding = '0 1rem';
		tabBar.style.background = 'var(--background-secondary)';
		tabBar.style.borderBottom = '1px solid var(--background-modifier-border)';

		const tabs: { id: DashboardTab; label: string; icon: string }[] = [
			{ id: 'overview', label: 'Overview', icon: 'bar-chart-2' },
			{ id: 'leeches', label: 'Leeches', icon: 'flame' },
			{ id: 'integrity', label: 'Integrity', icon: 'link' },
			{ id: 'queue-builder', label: 'Queue Builder', icon: 'list-ordered' },
		];

		tabs.forEach((tab) => {
			const btn = tabBar.createDiv({ cls: 'arete-tab-btn' });
			btn.style.padding = '8px 16px';
			btn.style.cursor = 'pointer';
			btn.style.borderBottom = '2px solid transparent';
			btn.style.display = 'flex';
			btn.style.gap = '6px';
			btn.style.alignItems = 'center';
			btn.style.fontSize = '0.9em';
			btn.style.fontWeight = '500';
			btn.style.color = 'var(--text-muted)';

			if (this.activeTab === tab.id) {
				btn.style.borderBottomColor = 'var(--interactive-accent)';
				btn.style.color = 'var(--text-normal)';
				btn.addClass('is-active');
			}

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
	// --- 1. OVERVIEW TAB ---
	async renderOverview(container: HTMLElement) {
		container.empty(); // Clear previous render to prevent duplication

		// FIX: Do NOT await refreshStats() here. It causes re-fetching on every UI click (expand/collapse).
		// Use cached data.
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
		const root = this.plugin.statsService.getAggregatedStats(concepts);

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
		const header = deckContainer.createDiv();
		header.style.padding = `8px 16px 8px ${8 + depth * 16}px`;
		header.style.cursor = 'pointer';
		header.style.display = 'flex';
		header.style.alignItems = 'center';
		header.style.borderBottom = '1px solid var(--background-modifier-border)';
		// Highlight if issues
		if (node.problematicCount > 0) header.style.background = 'rgba(var(--color-red-rgb), 0.05)';

		const icon = header.createSpan();
		icon.style.marginRight = '8px';
		// If leaf, key is filePath, else deckName. Expanded set keys match this logic.
		const expansionKey = node.isLeaf ? node.filePath : node.deckName;

		if (!node.isLeaf) {
			setIcon(icon, this.expandedDecks.has(expansionKey) ? 'chevron-down' : 'chevron-right');
		} else {
			setIcon(icon, 'file-text');
			icon.style.color = 'var(--text-muted)';
		}

		const title = header.createSpan({ text: node.title });
		title.style.fontWeight = '600';
		if (node.isLeaf) title.style.fontWeight = 'normal';

		const statsContainer = header.createDiv({ cls: 'arete-deck-stats' });
		statsContainer.style.marginLeft = 'auto';
		statsContainer.style.display = 'flex';
		statsContainer.style.gap = '20px';
		statsContainer.style.fontSize = '0.9em';
		statsContainer.style.color = 'var(--text-muted)';

		// 0. Card Count
		const countSpan = statsContainer.createSpan({ text: `${node.count} cards` });
		countSpan.style.width = '80px';
		countSpan.style.whiteSpace = 'nowrap';
		countSpan.style.textAlign = 'right';

		// 1. Difficulty (Avg)
		const diffSpan = statsContainer.createSpan();
		diffSpan.style.width = '70px';
		diffSpan.style.whiteSpace = 'nowrap';
		diffSpan.style.textAlign = 'right';
		if (node.difficulty != null) {
			// difficulty is already 1-10 scale from backend
			diffSpan.textContent = `${node.difficulty.toFixed(1)} D`;
			if (node.difficulty > 6) diffSpan.style.color = 'var(--color-orange)';
		} else {
			diffSpan.textContent = '-';
		}

		// 1b. Stability (Avg)
		const stabSpan = statsContainer.createSpan();
		stabSpan.style.width = '90px';
		stabSpan.style.whiteSpace = 'nowrap';
		stabSpan.style.textAlign = 'right';
		if (node.stability != null) {
			stabSpan.textContent = `${node.stability.toFixed(1)}d Stab`;
			if (node.stability < 7) stabSpan.style.color = 'var(--color-orange)';
		} else {
			stabSpan.textContent = '-';
		}

		// 1c. Retrievability (Avg)
		const retSpan = statsContainer.createSpan();
		retSpan.style.width = '70px';
		retSpan.style.whiteSpace = 'nowrap';
		retSpan.style.textAlign = 'right';
		if (node.retrievability != null) {
			retSpan.textContent = `${(node.retrievability * 100).toFixed(0)}% Ret`;
			if (node.retrievability < 0.85) retSpan.style.color = 'var(--color-red)';
		} else {
			retSpan.textContent = '-';
		}

		// 2. Lapses
		const lapseSpan = statsContainer.createSpan({ text: `${node.lapses} laps` });
		lapseSpan.style.width = '60px';
		lapseSpan.style.whiteSpace = 'nowrap';
		lapseSpan.style.textAlign = 'right';
		if (node.lapses > 0) lapseSpan.style.color = 'var(--text-error)';

		// 3. Score (Issues)
		if (node.problematicCount > 0) {
			const scoreBadge = statsContainer.createSpan({
				text: `${node.problematicCount} issues`,
			});
			scoreBadge.style.background = 'var(--color-red)';
			scoreBadge.style.color = 'var(--text-on-accent)';
			scoreBadge.style.padding = '2px 6px';
			scoreBadge.style.borderRadius = '4px';
			scoreBadge.style.fontSize = '0.8em';
			scoreBadge.style.marginLeft = '10px';
		}

		header.onclick = async () => {
			// Find the correct container by looking for .arete-dashboard-content
			const overviewContainer = container.closest('.arete-dashboard-content') as HTMLElement;
			if (!overviewContainer) return;

			if (!node.isLeaf) {
				if (this.expandedDecks.has(expansionKey)) {
					this.expandedDecks.delete(expansionKey);
				} else {
					this.expandedDecks.add(expansionKey);
				}
				this.plugin.saveSettings();
				// Re-render whole view to refresh tree state
				this.renderOverview(overviewContainer);
			} else {
				// Leaf click: Toggle file expansion
				if (this.expandedConcepts.has(node.filePath)) {
					this.expandedConcepts.delete(node.filePath);
				} else {
					this.expandedConcepts.add(node.filePath);
				}
				this.plugin.saveSettings();
				// Re-render using correct container
				this.renderOverview(overviewContainer);
			}
		};

		// Leaf name click action (go to file)
		if (node.isLeaf) {
			title.addClass('arete-clickable');
			title.onclick = (e) => {
				e.stopPropagation();
				this.openFile(node.filePath);
			};
		}

		// Children Rendering
		if (!node.isLeaf && this.expandedDecks.has(expansionKey)) {
			// Render children nodes
			Object.values(node.children).forEach((child) =>
				this.renderDeckNode(deckContainer, child, depth + 1),
			);
		} else if (node.isLeaf && this.expandedConcepts.has(node.filePath)) {
			// This is a file, find ConceptStats to render individual cards
			const concepts = this.plugin.statsService.getCache().concepts[node.filePath];
			if (concepts) {
				const cardListWrapper = deckContainer.createDiv({ cls: 'arete-card-list-wrapper' });
				cardListWrapper.style.paddingLeft = `${24 + depth * 16}px`;
				this.renderCardList(cardListWrapper, concepts);
			}
		}
	}

	renderCardList(container: HTMLElement, concept: ConceptStats) {
		const cardList = container.createDiv();
		cardList.style.background = 'var(--background-primary-alt)';
		cardList.style.borderBottom = '1px solid var(--background-modifier-border)';
		cardList.style.borderRadius = '4px';
		cardList.style.marginBottom = '8px';

		// Get all cards for this concept
		const cards = Object.values(concept.cardStats);

		if (cards.length === 0) {
			cardList.createDiv({ text: 'No card stats synced.' }).style.padding = '8px 32px';
		} else {
			// Header
			const header = cardList.createDiv();
			header.style.display = 'flex';
			header.style.padding = '4px 8px 4px 12px';
			header.style.fontSize = '0.75em';
			header.style.color = 'var(--text-muted)';
			header.style.borderBottom = '1px solid var(--background-modifier-border)';

			const h1 = header.createSpan({ text: 'Card Question' });
			h1.style.flex = '1';
			const h2 = header.createSpan({ text: 'Diff' });
			h2.style.width = '50px';
			const hStab = header.createSpan({ text: 'Stab' });
			hStab.style.width = '50px';
			const hGain = header.createSpan({ text: 'Gain' });
			hGain.style.width = '40px';
			const hRet = header.createSpan({ text: 'Ret' });
			hRet.style.width = '50px';
			const h3 = header.createSpan({ text: 'Lapses' });
			h3.style.width = '60px';
			const hFlags = header.createSpan({ text: '!' });
			hFlags.style.width = '20px';
			hFlags.style.textAlign = 'center';
			hFlags.title = 'Flags (e.g. Overlearning)';

			cards
				.sort((a, b) => b.lapses - a.lapses)
				.forEach((card) => {
					const cRow = cardList.createDiv();
					cRow.style.display = 'flex';
					cRow.style.alignItems = 'center';
					cRow.style.padding = '4px 8px 4px 12px';
					cRow.style.fontSize = '0.8em';
					cRow.style.borderBottom = '1px solid var(--background-modifier-border)';

					// Hover effect
					cRow.addEventListener(
						'mouseenter',
						() => (cRow.style.backgroundColor = 'var(--background-modifier-hover)'),
					);
					cRow.addEventListener(
						'mouseleave',
						() => (cRow.style.backgroundColor = 'transparent'),
					);

					cRow.onclick = (e) => {
						e.stopPropagation();
						if (card.front) this.goToCard(concept.filePath, card.front);
					};

					try {
						const frontText = card.front
							? card.front.replace(/<[^>]*>?/gm, '')
							: `#${card.cardId}`;
						const qSpan = cRow.createSpan({ text: frontText, cls: 'arete-clickable' });
						qSpan.title = frontText;
						qSpan.style.flex = '1';
						qSpan.style.whiteSpace = 'nowrap';
						qSpan.style.overflow = 'hidden';
						qSpan.style.textOverflow = 'ellipsis';
						qSpan.style.cursor = 'pointer';

						qSpan.addEventListener(
							'mouseenter',
							() => (qSpan.style.textDecoration = 'underline'),
						);
						qSpan.addEventListener(
							'mouseleave',
							() => (qSpan.style.textDecoration = 'none'),
						);

						qSpan.onclick = (e) => {
							e.stopPropagation();
							if (card.front) this.goToCard(concept.filePath, card.front);
						};

						// --- Stats Group (Click for Modal) ---
						const sGroup = cRow.createDiv({ cls: 'arete-card-stats-group' });
						sGroup.style.display = 'flex';
						sGroup.style.alignItems = 'center';
						sGroup.style.cursor = 'help';

						sGroup.addEventListener(
							'mouseenter',
							() => (sGroup.style.background = 'var(--background-modifier-hover)'),
						);
						sGroup.addEventListener(
							'mouseleave',
							() => (sGroup.style.background = 'transparent'),
						);

						sGroup.onclick = (e) => {
							e.stopPropagation();
							new CardStatsModal(this.plugin.app, card).open();
						};

						// 1. Difficulty
						const dSpan = sGroup.createSpan();
						dSpan.style.width = '50px';
						dSpan.style.textAlign = 'right';
						if (card.difficulty) {
							// difficulty is already 1-10 scale from backend
							dSpan.textContent = card.difficulty.toFixed(1);
							if (card.difficulty > 7) dSpan.style.color = 'var(--color-orange)';
						} else {
							dSpan.textContent = '-';
						}

						// 2. Stability
						const stSpan = sGroup.createSpan();
						stSpan.style.width = '50px';
						stSpan.style.textAlign = 'right';
						if (card.stability) {
							stSpan.textContent = card.stability.toFixed(0);
						} else {
							stSpan.textContent = '-';
						}

						// 3. Interval Growth (was Stability Gain)
						const growthSpan = sGroup.createSpan();
						growthSpan.style.width = '50px';
						growthSpan.style.textAlign = 'right';
						if (card.intervalGrowth != null) {
							growthSpan.textContent = `x${card.intervalGrowth.toFixed(1)}`;
							if (card.intervalGrowth < 1.0)
								growthSpan.style.color = 'var(--color-red)';
							else growthSpan.style.color = 'var(--color-green)';
						} else {
							growthSpan.textContent = '-';
						}

						// 4. Retrievability
						const rSpan = sGroup.createSpan();
						rSpan.style.width = '50px';
						rSpan.style.textAlign = 'right';
						if (card.retrievability) {
							rSpan.textContent = `${(card.retrievability * 100).toFixed(0)}%`;
							if (card.retrievability < 0.85) rSpan.style.color = 'var(--color-red)';
						} else {
							rSpan.textContent = '-';
						}

						// 5. Lapses
						const lSpan = sGroup.createSpan();
						lSpan.style.width = '60px'; // Matching header
						lSpan.style.textAlign = 'right';
						lSpan.textContent = card.lapses.toString();
						if (card.lapses > 5) lSpan.style.color = 'var(--color-red)';

						// 6. Flags
						const fSpan = sGroup.createSpan();
						fSpan.style.width = '20px';
						fSpan.style.textAlign = 'center';
						if (card.isOverlearning) {
							fSpan.textContent = 'OL';
							fSpan.style.fontSize = '0.7em';
							fSpan.style.fontWeight = 'bold';
							fSpan.style.color = 'var(--color-blue)';
							fSpan.title = 'Overlearning Detected';
						}
					} catch (e) {
						console.error('Error rendering card row', e);
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
		table.style.width = '100%';
		table.style.borderCollapse = 'collapse';

		const thead = table.createEl('thead');
		const headerRow = thead.createEl('tr');
		['Card (Front)', 'Difficulty', 'Lapses', 'Actions'].forEach((text) => {
			headerRow.createEl('th', { text }).style.textAlign = 'left';
		});

		const tbody = table.createEl('tbody');
		leeches.forEach((leech) => {
			const row = tbody.createEl('tr');
			row.style.borderBottom = '1px solid var(--background-modifier-border)';

			// Front
			const cellFront = row.createEl('td');
			cellFront.style.padding = '8px';
			// Truncate
			const frontDiv = cellFront.createDiv();
			frontDiv.style.maxWidth = '300px';
			frontDiv.style.overflow = 'hidden';
			frontDiv.style.textOverflow = 'ellipsis';
			frontDiv.style.whiteSpace = 'nowrap';
			frontDiv.style.cursor = 'pointer';
			frontDiv.style.fontWeight = '500';
			frontDiv.textContent = leech.front.replace(/<[^>]*>?/gm, '');
			frontDiv.title = leech.front;
			frontDiv.onclick = () => this.goToCard(leech.filePath, leech.front);

			// Diff/Ease (use 'issue' precalc or raw)
			const cellDiff = row.createEl('td');
			cellDiff.textContent = leech.ease
				? `${(leech.ease / 10).toFixed(0)}% Ease`
				: leech.difficulty != null
					? `${leech.difficulty.toFixed(1)} Diff`
					: '-';

			// Lapses
			const cellLapses = row.createEl('td');
			cellLapses.textContent = leech.lapses.toString();
			if (leech.lapses > 10) cellLapses.style.color = 'var(--color-red)';

			// Actions
			const cellActions = row.createEl('td');
			cellActions.style.display = 'flex';
			cellActions.style.gap = '0.5rem';

			const btnSuspend = cellActions.createEl('button', { text: 'Suspend' });
			btnSuspend.style.fontSize = '0.8em';
			btnSuspend.onclick = async () => {
				btnSuspend.textContent = '...';
				const success = await this.plugin.leechService.suspendCard(leech.cardId);
				if (success) {
					new Notice('Card suspended.');
					row.style.opacity = '0.5';
					btnSuspend.textContent = 'Suspended';
					btnSuspend.disabled = true;
				} else {
					new Notice('Failed to suspend card.');
					btnSuspend.textContent = 'Suspend';
				}
			};
		});
	}

	// --- 3. INTEGRITY TAB ---
	renderIntegrity(container: HTMLElement) {
		const actionArea = container.createDiv();
		actionArea.style.padding = '1rem';
		actionArea.style.background = 'var(--background-secondary-alt)';
		actionArea.style.marginBottom = '1rem';
		actionArea.style.borderRadius = '8px';
		actionArea.style.textAlign = 'center';

		const desc = actionArea.createDiv();
		desc.style.marginBottom = '1rem';
		desc.style.color = 'var(--text-muted)';
		desc.style.fontSize = '0.9em';
		desc.innerHTML = `
			<p style="margin-bottom: 0.5rem;">Scans your synced files to find <b>broken image embeds</b> and <b>invalid YAML</b>.</p>
			<p style="margin: 0;">Use this to ensure your Anki cards don't have broken media or syntax errors.</p>
		`;

		const checkBtn = actionArea.createEl('button', {
			cls: 'mod-cta',
			text: 'Run Integrity Check',
		});
		if (this.isScanning) {
			checkBtn.disabled = true;
			checkBtn.textContent = 'Scanning Vault...';
		}

		checkBtn.onclick = async () => {
			this.isScanning = true;
			this.render(); // Update button state
			try {
				// Scan all markdown files to ensure we catch those with broken YAML (which won't be in stats cache)
				const targetFiles = this.app.vault.getMarkdownFiles();

				this.brokenRefs = await this.plugin.linkCheckerService.checkIntegrity(targetFiles);
				new Notice(`Integrity check complete. Found ${this.brokenRefs.length} issues.`);
			} catch (e) {
				console.error(e);
				new Notice('Integrity check failed.');
			} finally {
				this.isScanning = false;
				this.render();
			}
		};

		if (this.brokenRefs) {
			if (this.brokenRefs.length === 0) {
				this.renderSuccess(container, 'No broken links or missing images found!');
			} else {
				const list = container.createDiv();

				this.brokenRefs.forEach((ref) => {
					const item = list.createDiv();
					item.style.padding = '0.5rem';
					item.style.borderBottom = '1px solid var(--background-modifier-border)';
					item.style.display = 'flex';
					item.style.justifyContent = 'space-between';
					item.style.alignItems = 'center';

					const left = item.createDiv();
					const fileLink = left.createSpan({
						text: ref.sourceFile.basename,
						cls: 'arete-clickable',
					});
					fileLink.style.fontWeight = 'bold';
					fileLink.onclick = () => this.app.workspace.getLeaf().openFile(ref.sourceFile);

					left.createSpan({ text: ` → ` });
					const targetSpan = left.createSpan({ text: ref.linkPath });
					targetSpan.style.color = 'var(--color-red)';
					// Tooltip for original text (debug aid)
					targetSpan.title = `Original text: "${ref.linkText}"`;

					const right = item.createDiv();
					right.style.display = 'flex';
					right.style.alignItems = 'center';
					right.style.gap = '8px';

					// Show original text if it differs significantly from linkPath
					if (ref.type === 'invalid-yaml' && ref.errorMessage) {
						const errSpan = right.createSpan({ text: ref.errorMessage });
						errSpan.style.fontSize = '0.8em';
						errSpan.style.color = 'var(--color-red)';
						errSpan.style.marginRight = '8px';
					} else if (
						ref.type !== 'invalid-yaml' &&
						ref.linkText !== `[[${ref.linkPath}]]` &&
						ref.linkText !== ref.linkPath
					) {
						const span = right.createSpan({ text: `"${ref.linkText}"` });
						span.style.fontSize = '0.8em';
						span.style.color = 'var(--text-muted)';
					}

					const typeBadge = right.createSpan({ text: ref.type.toUpperCase() });
					typeBadge.style.fontSize = '0.7em';
					typeBadge.style.padding = '2px 4px';
					typeBadge.style.borderRadius = '4px';

					if (ref.type === 'invalid-yaml') {
						typeBadge.style.background = 'var(--color-red)';
						typeBadge.style.color = 'var(--text-on-accent)';
						typeBadge.title =
							ref.errorMessage ||
							'Obsidian failed to parse frontmatter. Check for syntax errors.';
					} else {
						typeBadge.style.background = 'var(--background-modifier-border)';
					}

					// Navigation Badge/Button
					const gotoBtn = right.createSpan({ cls: 'arete-icon-btn' });
					setIcon(gotoBtn, 'external-link');
					gotoBtn.title = 'Go to location';
					gotoBtn.style.cursor = 'pointer';
					gotoBtn.style.marginLeft = '8px';
					gotoBtn.onclick = (e) => {
						e.stopPropagation();
						this.goToIssue(ref.sourceFile, ref.linkText);
					};
				});
			}
		}
	}

	// --- 4. QUEUE BUILDER TAB ---
	renderQueueBuilder(container: HTMLElement) {
		container.empty();

		// Header
		container.createEl('h3', { text: 'Queue Builder' });
		container.createEl('p', {
			text: 'Build a study queue that includes prerequisite cards before your due cards.',
			cls: 'setting-item-description',
		});

		// Controls
		const controls = container.createDiv();
		controls.style.padding = '1rem';
		controls.style.background = 'var(--background-secondary)';
		controls.style.borderRadius = '8px';
		controls.style.marginBottom = '1rem';

		// Deck selector
		const deckRow = controls.createDiv({ cls: 'setting-item' });
		deckRow.createDiv({ text: 'Deck Filter', cls: 'setting-item-name' });
		const deckSelect = deckRow.createEl('select');
		deckSelect.style.marginLeft = 'auto';
		deckSelect.createEl('option', { value: '', text: 'All Decks' });

		// Load decks async
		this.plugin.areteClient.getDeckNames().then((decks) => {
			decks.forEach((d) => deckSelect.createEl('option', { value: d, text: d }));
		});

		// Depth control
		const depthRow = controls.createDiv({ cls: 'setting-item' });
		depthRow.createDiv({ text: 'Prerequisite Depth', cls: 'setting-item-name' });
		const depthInput = depthRow.createEl('input', { type: 'number', value: '2' });
		depthInput.style.width = '60px';
		depthInput.style.marginLeft = 'auto';
		depthInput.min = '1';
		depthInput.max = '5';

		// Max cards control
		const maxRow = controls.createDiv({ cls: 'setting-item' });
		maxRow.createDiv({ text: 'Max Cards', cls: 'setting-item-name' });
		const maxInput = maxRow.createEl('input', { type: 'number', value: '50' });
		maxInput.style.width = '60px';
		maxInput.style.marginLeft = 'auto';
		maxInput.min = '10';
		maxInput.max = '200';

		// Build button
		const buildBtn = controls.createEl('button', { text: 'Build Queue', cls: 'mod-cta' });
		buildBtn.style.marginTop = '1rem';

		// Results area
		const resultsArea = container.createDiv({ cls: 'arete-queue-results' });

		buildBtn.onclick = async () => {
			buildBtn.textContent = 'Building...';
			buildBtn.disabled = true;

			try {
				const result = await this.plugin.areteClient.buildStudyQueue(
					deckSelect.value || null,
					parseInt(depthInput.value) || 2,
					parseInt(maxInput.value) || 50,
				);

				resultsArea.empty();

				// Summary
				const summary = resultsArea.createDiv();
				summary.style.padding = '0.5rem';
				summary.style.background = 'var(--background-primary-alt)';
				summary.style.borderRadius = '4px';
				summary.style.marginBottom = '0.5rem';
				summary.createEl('strong', { text: result.deck });
				summary.createSpan({
					text: ` — ${result.dueCount} due, ${result.totalWithPrereqs} total`,
				});

				// Send button
				const sendBtn = resultsArea.createEl('button', { text: 'Send to Anki' });
				sendBtn.style.marginBottom = '0.5rem';
				sendBtn.onclick = async () => {
					const ok = await this.plugin.areteClient.createQueueDeck(
						result.queue.map((c) => c.id),
					);
					new Notice(ok ? 'Queue sent to Anki!' : 'Failed to create deck.');
				};

				// Queue list
				const list = resultsArea.createDiv();
				list.style.maxHeight = '300px';
				list.style.overflowY = 'auto';

				result.queue.forEach((card) => {
					const item = list.createDiv();
					item.style.display = 'flex';
					item.style.padding = '4px 8px';
					item.style.borderBottom = '1px solid var(--background-modifier-border)';

					const pos = item.createSpan({ text: String(card.position) });
					pos.style.width = '30px';
					pos.style.fontWeight = 'bold';

					if (card.isPrereq) {
						const badge = item.createSpan({ text: 'PREREQ' });
						badge.style.fontSize = '0.7em';
						badge.style.padding = '2px 4px';
						badge.style.background = 'var(--interactive-accent)';
						badge.style.color = 'var(--text-on-accent)';
						badge.style.borderRadius = '4px';
						badge.style.marginRight = '8px';
					}

					const title = item.createSpan({ text: card.title });
					title.style.flex = '1';
					title.style.cursor = 'pointer';
					title.onclick = () => this.openFile(card.file);
				});

				new Notice(`Queue built: ${result.totalWithPrereqs} cards`);
			} catch (e) {
				console.error('[QueueBuilder] Error:', e);
				new Notice('Failed to build queue.');
			} finally {
				buildBtn.textContent = 'Build Queue';
				buildBtn.disabled = false;
			}
		};
	}

	// --- Helpers ---
	renderEmptyState(container: HTMLElement, message: string) {
		const empty = container.createDiv({ cls: 'arete-empty-state' });
		empty.style.textAlign = 'center';
		empty.style.padding = '3rem';
		empty.style.color = 'var(--text-muted)';
		setIcon(empty.createDiv(), 'search');
		empty.createDiv({ text: message }).style.marginTop = '1rem';
	}

	renderSuccess(container: HTMLElement, message: string) {
		const box = container.createDiv();
		box.style.padding = '2rem';
		box.style.textAlign = 'center';
		box.style.color = 'var(--color-green)';
		setIcon(box.createDiv(), 'check-circle');
		box.createDiv({ text: message }).style.marginTop = '1rem';
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
