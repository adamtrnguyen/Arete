import { resolveCardIndex } from '@/domain/cardRef';

describe('resolveCardIndex', () => {
	const cards = [{ id: 'arete_A' }, { Front: 'drafted, no id yet' }, { id: 'arete_C' }];

	it('finds a card by Arete id', () => {
		expect(resolveCardIndex(cards, 'arete_C')).toBe(2);
	});

	it('finds a card by 1-based position, for cards not synced yet', () => {
		expect(resolveCardIndex(cards, '2')).toBe(1);
	});

	it('defaults to the first card', () => {
		expect(resolveCardIndex(cards)).toBe(0);
		expect(resolveCardIndex([])).toBeNull();
	});

	it('reports a reference that names nothing instead of guessing', () => {
		for (const ref of ['arete_missing', '0', '4', '1.5', 'abc']) {
			expect(resolveCardIndex(cards, ref)).toBeNull();
		}
	});
});
