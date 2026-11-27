import { ButtonComponent, Notice, setTooltip } from 'obsidian';
import { AretePluginSettings } from '@/domain/settings';
import { difficultyOutOfTen, metricStates } from '@/domain/stats';
import { flagClass } from '@/presentation/cardStateClass';
import { LeechService } from '@application/services/LeechService';
import { StatsService } from '@application/services/StatsService';

export interface LeechesTabContext {
	settings: AretePluginSettings;
	statsService: StatsService;
	leechService: LeechService;
	renderEmptyState: (container: HTMLElement, message: string) => void;
	goToCard: (filePath: string, frontText: string, noteId?: number) => void;
}

export async function renderLeechesTab(
	ctx: LeechesTabContext,
	container: HTMLElement,
): Promise<void> {
	const leeches = ctx.leechService.getLeeches(ctx.statsService.getCache());

	if (leeches.length === 0) {
		ctx.renderEmptyState(container, 'No leeches found! Your deck is healthy.');
		return;
	}

	const isSm2 = ctx.settings.stats_algorithm === 'sm2';
	const table = container.createEl('table', { cls: 'arete-leech-table' });

	const thead = table.createEl('thead');
	const headerRow = thead.createEl('tr');
	['Card (front)', isSm2 ? 'Ease' : 'Difficulty', 'Lapses', 'Actions'].forEach((text) => {
		headerRow.createEl('th', { text });
	});

	const tbody = table.createEl('tbody');
	leeches.forEach((leech) => {
		const row = tbody.createEl('tr');

		const cellFront = row.createEl('td', { cls: 'arete-leech-cell-front' });
		const frontDiv = cellFront.createDiv({ cls: 'arete-leech-front arete-clickable' });
		frontDiv.textContent = leech.front.replace(/<[^>]*>?/gm, '');
		setTooltip(frontDiv, leech.front);
		frontDiv.onclick = () => ctx.goToCard(leech.filePath, leech.front, leech.noteId);

		const states = metricStates(
			{ lapses: leech.lapses, difficulty: leech.difficulty, ease: leech.ease },
			ctx.settings,
		);

		const cellDiff = row.createEl('td');
		if (isSm2) {
			cellDiff.textContent = leech.ease > 0 ? `${(leech.ease / 10).toFixed(0)}% ease` : '-';
		} else {
			cellDiff.textContent =
				leech.difficulty != null
					? `${difficultyOutOfTen(leech.difficulty).toFixed(1)} diff`
					: '-';
		}
		const diffFlag = flagClass(
			isSm2 ? states.ease : leech.difficulty != null ? states.difficulty : 'none',
		);
		if (diffFlag) cellDiff.addClass(diffFlag);

		const cellLapses = row.createEl('td');
		cellLapses.textContent = leech.lapses.toString();
		const lapseFlag = flagClass(states.lapses);
		if (lapseFlag) cellLapses.addClass(lapseFlag);

		const cellActions = row.createEl('td', { cls: 'arete-leech-actions' });
		const btnSuspend = new ButtonComponent(cellActions).setButtonText('Suspend');
		btnSuspend.onClick(async () => {
			btnSuspend.setButtonText('...');
			btnSuspend.setDisabled(true);
			let suspended = false;
			try {
				suspended = await ctx.leechService.suspendCard(leech.cardId);
				if (suspended) {
					new Notice('Card suspended.');
					row.addClass('is-suspended');
					btnSuspend.setButtonText('Suspended');
				} else {
					new Notice('Failed to suspend card.');
				}
			} catch (e) {
				console.error('[Arete] Failed to suspend card:', e);
				new Notice('Failed to suspend card.');
			} finally {
				if (!suspended) {
					btnSuspend.setButtonText('Suspend');
					btnSuspend.setDisabled(false);
				}
			}
		});
	});
}
