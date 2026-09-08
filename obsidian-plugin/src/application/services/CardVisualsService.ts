import type { AnkiCardStats } from '@/domain/stats';
import { difficultyOutOfTen } from '@/domain/stats';

export interface CardGutterVisuals {
	barColor: string;
	shadowColor: string;
	diffText: string;
	diffColor: string;
	lapseText: string | null;
	lapseColor: string;
	tooltip: string;
}

export class CardVisualsService {
	/**
	 * Computes all visual properties for a card in the gutter
	 * based on its stats and the current algorithm.
	 */
	static getGutterVisuals(
		stats: AnkiCardStats | null,
		algorithm: 'fsrs' | 'sm2',
		isSynced: boolean,
	): CardGutterVisuals {
		// Default (Synced but no stats = accent)
		let barColor = 'var(--interactive-accent)';
		let shadowColor = 'var(--interactive-accent)';
		let diffText = '';
		let diffColor = 'var(--text-muted)';
		let lapseText: string | null = null;
		let lapseColor = 'var(--text-muted)';
		const tooltipLines: string[] = [];

		if (!isSynced) {
			barColor = 'var(--text-muted)';
			shadowColor = 'var(--text-muted)';
		} else if (stats) {
			// Health-coded colors
			if (stats.lapses > 5 || (stats.difficulty && stats.difficulty > 0.8)) {
				barColor = 'var(--color-red)';
				shadowColor = 'var(--color-red)';
			} else if (stats.difficulty && stats.difficulty > 0.5) {
				barColor = 'var(--color-orange)';
				shadowColor = 'var(--color-orange)';
			} else {
				barColor = 'var(--color-green)';
				shadowColor = 'var(--color-green)';
			}

			// Sub-elements: Difficulty
			if (algorithm === 'fsrs') {
				if (
					stats.difficulty !== undefined &&
					stats.difficulty !== null &&
					stats.difficulty > 0
				) {
					diffText = difficultyOutOfTen(stats.difficulty).toFixed(1);
					if (stats.difficulty > 0.9) diffColor = 'var(--color-red)';
					else if (stats.difficulty > 0.5) diffColor = 'var(--color-orange)';
					else diffColor = 'var(--color-green)';
					tooltipLines.push(
						`Difficulty: ${difficultyOutOfTen(stats.difficulty).toFixed(1)}/10`,
					);
				} else {
					diffText = 'D:?';
				}
			} else {
				if (stats.ease && stats.ease > 0) {
					diffText = `E:${Math.round(stats.ease / 10)}%`;
					tooltipLines.push(`Ease: ${Math.round(stats.ease / 10)}%`);
				} else {
					diffText = 'E:?';
				}
			}

			// Sub-elements: Lapses
			if (stats.lapses > 0) {
				lapseText = `${stats.lapses}L`;
				lapseColor = stats.lapses > 5 ? 'var(--color-red)' : 'var(--color-orange)';
				tooltipLines.push(`Lapses: ${stats.lapses}`);
			}
		}

		return {
			barColor,
			shadowColor,
			diffText,
			diffColor,
			lapseText,
			lapseColor,
			tooltip: tooltipLines.join('\n'),
		};
	}
}
