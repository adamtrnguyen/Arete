import { cardState, dueEpochSeconds, metricStates, uniqueCardStats } from '@/domain/stats';
import type { CardStateSettings } from '@/domain/stats';
import { DEFAULT_SETTINGS } from '@/domain/settings';
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

describe('metricStates', () => {
	const fsrs: CardStateSettings = {
		stats_algorithm: 'fsrs',
		stats_lapse_threshold: DEFAULT_SETTINGS.stats_lapse_threshold,
		stats_difficulty_threshold: DEFAULT_SETTINGS.stats_difficulty_threshold,
		stats_ease_threshold: DEFAULT_SETTINGS.stats_ease_threshold,
	};
	const sm2: CardStateSettings = { ...fsrs, stats_algorithm: 'sm2' };

	it('uses the documented defaults', () => {
		expect([fsrs.stats_lapse_threshold, fsrs.stats_difficulty_threshold]).toEqual([3, 0.9]);
		expect(fsrs.stats_ease_threshold).toBe(2100);
	});

	it.each([
		[0, 'ok'],
		[1, 'warning'],
		[2, 'warning'],
		[3, 'error'],
		[4, 'error'],
	])('lapses %s is %s at the default threshold of 3', (lapses, expected) => {
		expect(metricStates({ lapses }, fsrs).lapses).toBe(expected);
	});

	it('lapses follow the user threshold, and error wins when it is at or below the warning', () => {
		const at5 = { ...fsrs, stats_lapse_threshold: 5 };
		expect(metricStates({ lapses: 4 }, at5).lapses).toBe('warning');
		expect(metricStates({ lapses: 5 }, at5).lapses).toBe('error');
		const at1 = { ...fsrs, stats_lapse_threshold: 1 };
		expect(metricStates({ lapses: 1 }, at1).lapses).toBe('error');
	});

	it.each([
		[0, 'ok'],
		[0.69, 'ok'],
		[0.7, 'warning'],
		[0.89, 'warning'],
		[0.9, 'error'],
		[1, 'error'],
	])('difficulty %s is %s at the default threshold of 0.9', (difficulty, expected) => {
		expect(metricStates({ difficulty }, fsrs).difficulty).toBe(expected);
	});

	it('difficulty follows the user threshold', () => {
		const at08 = { ...fsrs, stats_difficulty_threshold: 0.8 };
		expect(metricStates({ difficulty: 0.79 }, at08).difficulty).toBe('warning');
		expect(metricStates({ difficulty: 0.8 }, at08).difficulty).toBe('error');
	});

	it.each([
		[0.84, 'error'],
		[0.85, 'ok'],
		[0.86, 'ok'],
		[1, 'ok'],
	])('retrievability %s is %s', (retrievability, expected) => {
		expect(metricStates({ retrievability }, fsrs).retrievability).toBe(expected);
	});

	it.each([
		[6.99, 'warning'],
		[7, 'ok'],
		[0, 'warning'],
		[30, 'ok'],
	])('stability %s days is %s', (stability, expected) => {
		expect(metricStates({ stability }, fsrs).stability).toBe(expected);
	});

	it.each([
		[2099, 'warning'],
		[2100, 'ok'],
		[2500, 'ok'],
	])('SM-2 ease %s is %s at the default threshold of 2100', (ease, expected) => {
		expect(metricStates({ ease }, sm2).ease).toBe(expected);
	});

	it('ease follows the user threshold', () => {
		const at1800 = { ...sm2, stats_ease_threshold: 1800 };
		expect(metricStates({ ease: 1799 }, at1800).ease).toBe('warning');
		expect(metricStates({ ease: 1800 }, at1800).ease).toBe('ok');
	});

	it('ease is judged under SM-2 only, and a zero factor is no data', () => {
		expect(metricStates({ ease: 1300 }, fsrs).ease).toBe('none');
		expect(metricStates({ ease: 0 }, sm2).ease).toBe('none');
	});

	it('an absent stat is none, never ok', () => {
		const m = metricStates({}, sm2);
		expect(Object.values(m).every((s) => s === 'none')).toBe(true);
		expect(metricStates({ lapses: null, difficulty: NaN }, fsrs).lapses).toBe('none');
		expect(metricStates({ difficulty: NaN }, fsrs).difficulty).toBe('none');
	});

	it.each([
		['intervalGrowth', 1.19, 'warning'],
		['intervalGrowth', 1.2, 'ok'],
		['pressFatigue', 0.3, 'ok'],
		['pressFatigue', 0.31, 'error'],
		['daysOverdue', 7, 'ok'],
		['daysOverdue', 8, 'error'],
	])('modal-only metric %s %s is %s', (key, value, expected) => {
		expect(metricStates({ [key]: value }, fsrs)[key as 'intervalGrowth']).toBe(expected);
	});
});

describe('cardState', () => {
	const settings: CardStateSettings = {
		stats_algorithm: 'fsrs',
		stats_lapse_threshold: 3,
		stats_difficulty_threshold: 0.9,
		stats_ease_threshold: 2100,
	};
	const stats = (over = {}) =>
		ankiCardStats({
			lapses: 0,
			difficulty: 0.3,
			retrievability: 0.95,
			stability: 30,
			...over,
		});

	it('an unsynced card is muted, with or without stats', () => {
		expect(cardState(null, false, settings).state).toBe('muted');
		expect(cardState(stats({ lapses: 9 }), false, settings).state).toBe('muted');
	});

	it('a synced card without stats is accent', () => {
		expect(cardState(null, true, settings).state).toBe('accent');
		expect(cardState(undefined, true, settings).state).toBe('accent');
	});

	it('a synced card with no flag is success', () => {
		expect(cardState(stats(), true, settings).state).toBe('success');
	});

	it('a card with stats but none of the five metrics is success', () => {
		const bare = ankiCardStats({ lapses: 0, difficulty: undefined });
		expect(cardState(bare, true, settings).state).toBe('success');
	});

	it.each([
		['lapses', { lapses: 1 }, 'warning'],
		['lapses', { lapses: 3 }, 'error'],
		['difficulty', { difficulty: 0.7 }, 'warning'],
		['difficulty', { difficulty: 0.9 }, 'error'],
		['retrievability', { retrievability: 0.84 }, 'error'],
		['stability', { stability: 6 }, 'warning'],
	])('one flagged %s makes the card %s', (_name, over, expected) => {
		expect(cardState(stats(over), true, settings).state).toBe(expected);
	});

	it('the card shows its worst metric', () => {
		const r = cardState(stats({ lapses: 1, retrievability: 0.5 }), true, settings);
		expect(r.state).toBe('error');
		expect(r.metrics.lapses).toBe('warning');
		expect(r.metrics.retrievability).toBe('error');
	});

	it('SM-2 ease warns the card; FSRS ignores it', () => {
		const low = stats({ ease: 1500, difficulty: undefined });
		expect(cardState(low, true, { ...settings, stats_algorithm: 'sm2' }).state).toBe('warning');
		expect(cardState(low, true, settings).state).toBe('success');
	});

	it('modal-only metrics do not change the card state', () => {
		const r = cardState(stats({ intervalGrowth: 0.5, daysOverdue: 99 }), true, settings);
		expect(r.state).toBe('success');
		expect(r.metrics.daysOverdue).toBe('error');
	});
});
