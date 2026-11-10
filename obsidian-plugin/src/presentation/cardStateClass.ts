/**
 * The class a view puts on an element for a card or metric state. CSS maps the class to a
 * colour (`--text-*` for text, `--color-*` for bars and dots).
 */
import type { CardState, MetricState } from '@/domain/stats';

/** `mod-warning` or `mod-error` for a flagged metric; nothing for `ok` or `none`. */
export function flagClass(state: MetricState): 'mod-warning' | 'mod-error' | null {
	return state === 'warning' ? 'mod-warning' : state === 'error' ? 'mod-error' : null;
}

/** `mod-success` / `mod-warning` / `mod-error` for a card; `muted` and `accent` have none. */
export function cardStateClass(state: CardState): string | null {
	return state === 'success' || state === 'warning' || state === 'error' ? `mod-${state}` : null;
}
