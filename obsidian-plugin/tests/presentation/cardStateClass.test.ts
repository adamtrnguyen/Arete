import { cardStateClass } from '@/presentation/cardStateClass';
import type { CardState } from '@/domain/stats';

describe('cardStateClass', () => {
	it.each<[CardState, string | null]>([
		['success', 'mod-success'],
		['warning', 'mod-warning'],
		['error', 'mod-error'],
		['nodata', 'mod-nodata'],
		['muted', null],
	])('%s maps to %s', (state, expected) => {
		expect(cardStateClass(state)).toBe(expected);
	});

	it('a card with no data never takes the success class', () => {
		expect(cardStateClass('nodata')).not.toBe('mod-success');
	});
});
