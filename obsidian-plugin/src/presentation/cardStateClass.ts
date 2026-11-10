/**
 * The class a view puts on an element for a card or metric state. CSS maps the class to a
 * colour (`--text-*` for text, `--color-*` for bars and dots).
 */
import type { CardState, MetricState } from '@/domain/stats';

/** `mod-warning` or `mod-error` for a flagged metric; nothing for `ok` or `none`. */
export function flagClass(state: MetricState): 'mod-warning' | 'mod-error' | null {
	return state === 'warning' ? 'mod-warning' : state === 'error' ? 'mod-error' : null;
}

/**
 * The class for a card state: `mod-success`, `mod-warning`, `mod-error`, `mod-nodata`.
 * An unsynced (`muted`) card has none; the gutter styles it by the missing `is-synced`.
 */
export function cardStateClass(state: CardState): string | null {
	switch (state) {
		case 'success':
		case 'warning':
		case 'error':
		case 'nodata':
			return `mod-${state}`;
		case 'muted':
			return null;
		default: {
			const unhandled: never = state;
			return unhandled;
		}
	}
}
