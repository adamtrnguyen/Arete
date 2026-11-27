import { App, ExtraButtonComponent, Notice, setTooltip, TFile } from 'obsidian';
import { difficultyOutOfTen, dueEpochSeconds, metricStates } from '@/domain/stats';
import { AretePluginSettings } from '@/domain/settings';
import { debugLog } from '@/domain/debugLog';
import { StatsService } from '@application/services/StatsService';
import { flagClass } from '@/presentation/cardStateClass';
import { CardStatsModal } from '@/presentation/modals/CardStatsModal';
import { CardData, CardEditorViewMode } from '@/presentation/views/yamlEditor/cardEditorHelpers';

export interface CardToolbarContext {
	app: App;
	activeFile: TFile | null;
	card: CardData | undefined;
	viewMode: CardEditorViewMode;
	settings: AretePluginSettings;
	statsService: StatsService;
	onScrollToCard: () => void;
	onOpenInAnki: () => void;
	onOpenLocalGraph: () => void;
	onSelectMode: (mode: CardEditorViewMode) => void;
}

/** An icon button in the card toolbar: Obsidian's extra-button, with a native tooltip. */
function addToolbarButton(
	group: HTMLElement,
	icon: string,
	tooltip: string,
	onClick: () => void,
	state: { active?: boolean; disabled?: boolean } = {},
): void {
	const button = new ExtraButtonComponent(group)
		.setIcon(icon)
		.setTooltip(tooltip)
		.onClick(onClick);
	button.extraSettingsEl.addClass('arete-toolbar-btn');
	button.extraSettingsEl.toggleClass('is-active', !!state.active);
	button.extraSettingsEl.toggleClass('is-disabled', !!state.disabled);
}

/** A small stat badge; its explanation is a native tooltip. */
function addBadge(parent: HTMLElement, cls: string, text: string, tooltip?: string): void {
	const badge = parent.createDiv({ cls, text });
	if (tooltip) setTooltip(badge, tooltip);
}

export function renderCardToolbar(container: HTMLElement, ctx: CardToolbarContext): void {
	container.empty();

	const leftGroup = container.createDiv({ cls: 'arete-toolbar-group' });
	const { activeFile, card } = ctx;
	const cid = card?.['anki']?.['cid'];
	const nid = card?.['anki']?.['nid'];

	if (activeFile && (cid || nid)) {
		const cache = ctx.statsService.getCache().concepts[activeFile.path];
		const stats = (cid && cache?.cardStats?.[cid]) || (nid && cache?.cardStats?.[nid]) || null;
		if (stats) {
			const statsContainer = leftGroup.createDiv({ cls: 'arete-toolbar-stats' });
			const algo = ctx.settings.stats_algorithm;
			// The same cut-offs as the gutter, dashboard and card stats modal.
			const states = metricStates(stats, ctx.settings);

			let badgeAdded = false;

			if (algo === 'fsrs' && stats.difficulty !== undefined) {
				if (stats.difficulty !== null) {
					// difficulty is stored 0..1; scale only to display it
					addBadge(
						statsContainer,
						`arete-stat-badge ${flagClass(states.difficulty) ?? 'mod-success'}`,
						`D: ${difficultyOutOfTen(stats.difficulty).toFixed(1)}`,
						`FSRS difficulty: ${difficultyOutOfTen(stats.difficulty).toFixed(1)}/10`,
					);
				} else {
					addBadge(
						statsContainer,
						'arete-stat-badge mod-muted',
						'D: ?',
						'FSRS difficulty not available',
					);
				}
				badgeAdded = true;
			} else if (algo === 'sm2' || stats.ease !== undefined) {
				const ease = Math.round(stats.ease / 10);
				addBadge(statsContainer, 'arete-stat-badge', `E: ${ease}%`, `SM-2 ease: ${ease}%`);
				badgeAdded = true;
			}

			if (stats.lapses > 0) {
				const lapseFlag = flagClass(states.lapses);
				addBadge(
					statsContainer,
					lapseFlag ? `arete-stat-badge ${lapseFlag}` : 'arete-stat-badge',
					`${stats.lapses}L`,
					`Lapses: ${stats.lapses}`,
				);
				badgeAdded = true;
			}

			const dueEpoch = dueEpochSeconds(stats.due);
			if (dueEpoch !== null) {
				const dueDate = new Date(dueEpoch * 1000);
				const now = new Date();
				const diffDays = Math.ceil(
					(dueDate.getTime() - now.getTime()) / (1000 * 60 * 60 * 24),
				);
				let dueText: string;
				if (diffDays < 0) dueText = `${Math.abs(diffDays)}d ago`;
				else if (diffDays === 0) dueText = 'Today';
				else dueText = `${diffDays}d`;

				addBadge(
					statsContainer,
					'arete-stat-badge mod-muted',
					dueText,
					`Due: ${dueDate.toLocaleDateString()}`,
				);
				badgeAdded = true;
			}

			if (!badgeAdded) {
				statsContainer.createDiv({
					cls: 'arete-stat-badge mod-muted',
					text: 'No data',
				});
			}
		} else {
			leftGroup.createDiv({
				cls: 'arete-stat-badge mod-muted',
				text: 'No data',
			});
		}
	} else if (activeFile) {
		addBadge(
			leftGroup,
			'arete-stat-badge mod-muted',
			'Unsynced',
			'Card not yet synced to Anki',
		);
	}

	const centerGroup = container.createDiv({ cls: 'arete-toolbar-group' });

	addToolbarButton(centerGroup, 'file-text', 'Go to YAML in note', ctx.onScrollToCard);
	addToolbarButton(centerGroup, 'external-link', 'Open in Anki', ctx.onOpenInAnki);
	addToolbarButton(
		centerGroup,
		'bar-chart-2',
		cid || nid ? 'View card stats' : 'Card not synced (no NID)',
		() => {
			if (!cid && !nid) {
				new Notice('Card not synced to Anki yet (no NID).');
				return;
			}
			debugLog('[Arete] Opening stats modal for card:', cid ?? nid);
			const currentFile = ctx.app.workspace.getActiveFile();
			if (currentFile && (cid || nid)) {
				const cache = ctx.statsService.getCache();
				const conceptStats = cache.concepts[currentFile.path];
				if (!conceptStats) {
					new Notice('No stats synced for this file yet.');
					return;
				}

				const cardStats =
					(cid && conceptStats.cardStats?.[cid]) ||
					(nid && conceptStats.cardStats?.[nid]);

				if (cardStats) {
					new CardStatsModal(ctx.app, cardStats, ctx.settings).open();
				} else {
					new Notice('No stats data available for this specific card.');
				}
			}
		},
		{ disabled: !cid && !nid },
	);
	addToolbarButton(centerGroup, 'share-2', 'Open local graph', ctx.onOpenLocalGraph);

	const rightGroup = container.createDiv({ cls: 'arete-toolbar-group' });

	const modes: Array<[CardEditorViewMode, string, string]> = [
		[CardEditorViewMode.Fields, 'edit-3', 'Card edit mode'],
		[CardEditorViewMode.Source, 'code', 'Source mode'],
		[CardEditorViewMode.Preview, 'eye', 'Preview mode'],
	];
	for (const [mode, icon, tooltip] of modes) {
		addToolbarButton(rightGroup, icon, tooltip, () => ctx.onSelectMode(mode), {
			active: ctx.viewMode === mode,
		});
	}
}
