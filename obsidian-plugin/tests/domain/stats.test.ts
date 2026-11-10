import { dueEpochSeconds, uniqueCardStats } from '@/domain/stats';
import { ankiCardStats } from '../factories';

describe('dueEpochSeconds', () => {
	it('keeps a (re)learning card due time, which Anki stores as epoch seconds', () => {
		expect(dueEpochSeconds(1762228377)).toBe(1762228377);
	});

	it('refuses a review-card day number or a new-card position ("20721d ago")', () => {
		expect(dueEpochSeconds(4213)).toBeNull(); // days since collection creation
		expect(dueEpochSeconds(3)).toBeNull(); // new-card queue position
		expect(dueEpochSeconds(0)).toBeNull();
		expect(dueEpochSeconds(undefined)).toBeNull();
	});
});

describe('uniqueCardStats', () => {
	const card = (cardId: number, noteId: number) => ankiCardStats({ cardId, noteId });

	it('lists a card stored under its card id and its note id once', () => {
		const a = card(1762277781742, 1762277781741);
		expect(uniqueCardStats({ [a.cardId]: a, [a.noteId]: a })).toEqual([a]);
	});

	it('lists it once after a JSON round trip, where the two entries are copies', () => {
		const a = card(1762277781742, 1762277781741);
		const stored = JSON.parse(JSON.stringify({ [a.cardId]: a, [a.noteId]: a }));
		expect(uniqueCardStats(stored)).toHaveLength(1);
	});

	it('keeps every card of a note with several cards', () => {
		const c1 = card(11, 10);
		const c2 = card(12, 10);
		const ids = uniqueCardStats({ 11: c1, 12: c2, 10: c2 }).map((s) => s.cardId);
		expect(ids.sort()).toEqual([11, 12]);
	});
});
