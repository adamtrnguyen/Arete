import { App, setIcon, setTooltip } from 'obsidian';
import {
	ConceptStats,
	StatsNode,
	difficultyOutOfTen,
	metricStates,
	uniqueCardStats,
} from '@/domain/stats';
import { AretePluginSettings } from '@/domain/settings';
import { flagClass } from '@/presentation/cardStateClass';
import { CardStatsModal } from '@/presentation/modals/CardStatsModal';
import { StatsService } from '@application/services/StatsService';

export interface OverviewTabContext {
	app: App;
	settings: AretePluginSettings;
	statsService: StatsService;
	expandedDecks: Set<string>;
	expandedConcepts: Set<string>;
	isCacheCurrent: () => boolean;
	rebuildOverview: (overviewContainer: HTMLElement) => void;
	persistExpansion: () => void;
	openFile: (filePath: string) => void;
	goToCard: (filePath: string, frontText: string, noteId?: number) => void;
}

export function renderDeckNodeView(
	ctx: OverviewTabContext,
	container: HTMLElement,
	node: StatsNode,
	depth: number,
): void {
	const deckContainer = container.createDiv();
	const header = deckContainer.createDiv({ cls: 'arete-deck-header' });
	// The indent grows with the depth, so it is the one runtime-computed style.
	header.style.paddingLeft = `${8 + depth * 16}px`;
	// A row takes its state from its cards: tinted when any card of it is in error.
	if (node.stateCounts.error > 0) header.addClass('mod-error');

	const icon = header.createSpan({ cls: 'arete-deck-icon' });
	const expansionKey = node.isLeaf ? node.filePath : node.deckName;

	if (!node.isLeaf) {
		setIcon(icon, ctx.expandedDecks.has(expansionKey) ? 'chevron-down' : 'chevron-right');
	} else {
		setIcon(icon, 'file-text');
		icon.addClass('mod-leaf');
	}

	const title = header.createSpan({ text: node.title, cls: 'arete-deck-title' });
	if (node.isLeaf) title.addClass('mod-leaf');

	const statsContainer = header.createDiv({ cls: 'arete-deck-stats' });

	// A row shows totals and averages as plain figures. The cut-offs are per card, so a
	// sum or a mean is never compared with them; the row's state comes from its cards.
	const stat = (text: string, column: string) =>
		statsContainer.createSpan({ text, cls: `arete-deck-stat mod-${column}` });

	const countSpan = stat(`${node.count} cards`, 'count');
	const { success, warning, error, nodata } = node.stateCounts;
	setTooltip(
		countSpan,
		`${success} healthy, ${warning} warning, ${error} error, ${nodata} no data`,
	);

	stat(
		node.difficulty != null ? `${difficultyOutOfTen(node.difficulty).toFixed(1)} D` : '-',
		'difficulty',
	);
	stat(node.stability != null ? `${node.stability.toFixed(1)}d stab` : '-', 'stability');
	stat(
		node.retrievability != null ? `${(node.retrievability * 100).toFixed(0)}% ret` : '-',
		'retrievability',
	);
	stat(`${node.lapses} laps`, 'lapses');

	if (node.problematicCount > 0) {
		statsContainer.createSpan({
			text: `${node.problematicCount} issues`,
			cls: 'arete-badge mod-error',
		});
	}

	// Children go in their own element, so a toggle rebuilds only this subtree.
	const body = deckContainer.createDiv();

	header.onclick = () => {
		const expanded = node.isLeaf ? ctx.expandedConcepts : ctx.expandedDecks;
		if (expanded.has(expansionKey)) {
			expanded.delete(expansionKey);
		} else {
			expanded.add(expansionKey);
		}
		ctx.persistExpansion();

		if (!ctx.isCacheCurrent()) {
			const overviewContainer = container.closest<HTMLElement>('.arete-dashboard-content');
			if (overviewContainer) ctx.rebuildOverview(overviewContainer);
			return;
		}

		if (!node.isLeaf) {
			setIcon(icon, expanded.has(expansionKey) ? 'chevron-down' : 'chevron-right');
		}
		body.empty();
		renderNodeBody(ctx, body, node, depth);
	};

	if (node.isLeaf) {
		title.addClass('arete-clickable');
		title.onclick = (e) => {
			e.stopPropagation();
			ctx.openFile(node.filePath);
		};
	}

	renderNodeBody(ctx, body, node, depth);
}

function renderNodeBody(
	ctx: OverviewTabContext,
	body: HTMLElement,
	node: StatsNode,
	depth: number,
): void {
	if (!node.isLeaf && ctx.expandedDecks.has(node.deckName)) {
		Object.values(node.children).forEach((child) =>
			renderDeckNodeView(ctx, body, child, depth + 1),
		);
	} else if (node.isLeaf && ctx.expandedConcepts.has(node.filePath)) {
		const concept = ctx.statsService.getCache().concepts[node.filePath];
		if (concept) {
			const cardListWrapper = body.createDiv({ cls: 'arete-card-list-wrapper' });
			cardListWrapper.style.paddingLeft = `${24 + depth * 16}px`;
			renderCardListView(ctx, cardListWrapper, concept);
		}
	}
}

export function renderCardListView(
	ctx: OverviewTabContext,
	container: HTMLElement,
	concept: ConceptStats,
): void {
	const cardList = container.createDiv({ cls: 'arete-card-list' });
	const cards = uniqueCardStats(concept.cardStats);

	if (cards.length === 0) {
		cardList.createDiv({ text: 'No card stats synced.', cls: 'arete-card-list-empty' });
		return;
	}

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
				if (card.front) ctx.goToCard(concept.filePath, card.front, card.noteId);
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
					if (card.front) ctx.goToCard(concept.filePath, card.front, card.noteId);
				};

				const sGroup = cRow.createDiv({ cls: 'arete-card-stats-group' });
				sGroup.onclick = (e) => {
					e.stopPropagation();
					new CardStatsModal(ctx.app, card, ctx.settings).open();
				};

				const states = metricStates(card, ctx.settings);
				const col = (column: string, text: string, flag?: string | null) => {
					const span = sGroup.createSpan({
						text,
						cls: `arete-card-col mod-${column}`,
					});
					if (flag) span.addClass(flag);
					return span;
				};

				col(
					'difficulty',
					card.difficulty != null ? difficultyOutOfTen(card.difficulty).toFixed(1) : '-',
					card.difficulty != null ? flagClass(states.difficulty) : null,
				);
				col(
					'stability',
					card.stability != null ? card.stability.toFixed(0) : '-',
					card.stability != null ? flagClass(states.stability) : null,
				);
				col(
					'growth',
					card.intervalGrowth != null ? `x${card.intervalGrowth.toFixed(1)}` : '-',
					card.intervalGrowth != null
						? (flagClass(states.intervalGrowth) ?? 'mod-success')
						: null,
				);
				col(
					'retrievability',
					card.retrievability != null
						? `${(card.retrievability * 100).toFixed(0)}%`
						: '-',
					card.retrievability != null ? flagClass(states.retrievability) : null,
				);
				col('lapses', card.lapses.toString(), flagClass(states.lapses));

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
