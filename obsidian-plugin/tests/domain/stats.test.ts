import {
	cardState,
	dueEpochSeconds,
	formatGutterStats,
	metricStates,
	normalizeStatsCache,
	serializeStatsCache,
	uniqueCardStats,
} from '@/domain/stats';
import type { CardStateSettings } from '@/domain/stats';
import { DEFAULT_SETTINGS, normalizeSettings } from '@/domain/settings';
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
		[0, 'warning'],
		[0.84, 'warning'],
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

	it('a synced card without stats has no data', () => {
		expect(cardState(null, true, settings).state).toBe('nodata');
		expect(cardState(undefined, true, settings).state).toBe('nodata');
	});

	it('a synced card with no flag is success', () => {
		expect(cardState(stats(), true, settings).state).toBe('success');
	});

	it('a card whose review metrics are all absent has no data, not success', () => {
		const bare = ankiCardStats({
			lapses: 0,
			difficulty: undefined,
			retrievability: undefined,
			stability: undefined,
		});
		expect(cardState(bare, true, settings).state).toBe('nodata');
	});

	it('a card with no review metrics but a flagged lapse count is judged on the lapses', () => {
		const bare = ankiCardStats({
			lapses: 3,
			difficulty: undefined,
			retrievability: undefined,
			stability: undefined,
		});
		expect(cardState(bare, true, settings).state).toBe('error');
	});

	it('one present metric is enough to judge the card', () => {
		const some = ankiCardStats({
			lapses: 0,
			difficulty: 0.3,
			retrievability: undefined,
			stability: undefined,
		});
		expect(cardState(some, true, settings).state).toBe('success');
	});

	it.each([
		['lapses', { lapses: 1 }, 'warning'],
		['lapses', { lapses: 3 }, 'error'],
		['difficulty', { difficulty: 0.7 }, 'warning'],
		['difficulty', { difficulty: 0.9 }, 'error'],
		['retrievability', { retrievability: 0.84 }, 'warning'],
		['stability', { stability: 6 }, 'warning'],
	])('one flagged %s makes the card %s', (_name, over, expected) => {
		expect(cardState(stats(over), true, settings).state).toBe(expected);
	});

	it('the card shows its worst metric', () => {
		const r = cardState(stats({ lapses: 3, retrievability: 0.5 }), true, settings);
		expect(r.state).toBe('error');
		expect(r.metrics.lapses).toBe('error');
		expect(r.metrics.retrievability).toBe('warning');
	});

	it('low retrievability alone never makes a card an error', () => {
		expect(cardState(stats({ retrievability: 0 }), true, settings).state).toBe('warning');
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

describe('formatGutterStats', () => {
	it('returns empty labels for unsynced cards or missing stats', () => {
		expect(formatGutterStats(ankiCardStats({ difficulty: 0.5 }), 'fsrs', false)).toEqual({
			diffText: '',
			lapseText: null,
			tooltip: '',
		});
		expect(formatGutterStats(null, 'fsrs', true)).toEqual({
			diffText: '',
			lapseText: null,
			tooltip: '',
		});
	});

	it('formats FSRS difficulty and lapses for gutter badges and tooltip', () => {
		const s = ankiCardStats({ difficulty: 0.5, lapses: 2 });
		expect(formatGutterStats(s, 'fsrs', true)).toEqual({
			diffText: '5.0',
			lapseText: '2L',
			tooltip: 'Difficulty: 5.0/10\nLapses: 2',
		});
		expect(
			formatGutterStats(ankiCardStats({ difficulty: undefined, lapses: 0 }), 'fsrs', true),
		).toEqual({
			diffText: 'D:?',
			lapseText: null,
			tooltip: '',
		});
	});

	it('formats SM-2 ease and falls back to E:? when ease is zero', () => {
		expect(formatGutterStats(ankiCardStats({ ease: 2500, lapses: 1 }), 'sm2', true)).toEqual({
			diffText: 'E:250%',
			lapseText: '1L',
			tooltip: 'Ease: 250%\nLapses: 1',
		});
		expect(formatGutterStats(ankiCardStats({ ease: 0, lapses: 0 }), 'sm2', true)).toEqual({
			diffText: 'E:?',
			lapseText: null,
			tooltip: '',
		});
	});
});

describe('normalizeSettings', () => {
	it('falls back to defaults for corrupt primitives, invalid enums, out-of-range numbers, and unknown keys', () => {
		expect(normalizeSettings(null)).toEqual(DEFAULT_SETTINGS);
		expect(normalizeSettings(['not', 'an', 'object'])).toEqual(DEFAULT_SETTINGS);

		const normalized = normalizeSettings({
			python_path: 42,
			debug_mode: 'yes',
			backend: 'invalid-backend',
			workers: -5,
			renderer_mode: 'legacy',
			stats_algorithm: 'sm3',
			stats_lapse_threshold: NaN,
			stats_ease_threshold: 0,
			stats_difficulty_threshold: 5,
			sync_on_save_delay: Infinity,
			ui_expanded_decks: ['Math', 123, null],
			ui_expanded_concepts: 'not-an-array',
			last_sync_time: -10,
			removed_legacy_key: true,
		});

		expect(normalized).toEqual({
			...DEFAULT_SETTINGS,
			ui_expanded_decks: ['Math'],
			ui_expanded_concepts: [],
		});
		expect(normalized).not.toHaveProperty('removed_legacy_key');
	});

	it('preserves valid settings', () => {
		const normalized = normalizeSettings({
			backend: 'direct',
			workers: 8,
			stats_algorithm: 'sm2',
			stats_difficulty_threshold: 0.75,
			last_sync_time: 1_700_000_000,
			ui_expanded_concepts: ['A.md'],
		});
		expect(normalized.backend).toBe('direct');
		expect(normalized.workers).toBe(8);
		expect(normalized.stats_algorithm).toBe('sm2');
		expect(normalized.stats_difficulty_threshold).toBe(0.75);
		expect(normalized.last_sync_time).toBe(1_700_000_000);
		expect(normalized.ui_expanded_concepts).toEqual(['A.md']);
	});
});

describe('normalizeStatsCache and serializeStatsCache', () => {
	it('handles corrupt or missing statsCache shapes without throwing', () => {
		expect(normalizeStatsCache(null)).toEqual({ concepts: {}, lastFetched: 0 });
		expect(normalizeStatsCache({ concepts: 'bad', lastFetched: NaN })).toEqual({
			concepts: {},
			lastFetched: 0,
		});
		const partial = normalizeStatsCache({
			lastFetched: 100,
			concepts: {
				'Bad.md': 'not-an-object',
				'Good.md': { totalCards: 'oops', cardStats: null, problematicCards: 'bad' },
			},
		});
		expect(Object.keys(partial.concepts)).toEqual(['Good.md']);
		expect(partial.concepts['Good.md'].totalCards).toBe(0);
		expect(partial.concepts['Good.md'].cardStats).toEqual({});
		expect(partial.concepts['Good.md'].problematicCards).toEqual([]);
	});

	it('deduplicates noteId aliases on save, strips dropped FSRS fields, and rebuilds noteId lookup on load', () => {
		const c1 = ankiCardStats({ cardId: 101, noteId: 900, difficulty: 0.4, lapses: 1 });
		const c2 = ankiCardStats({ cardId: 102, noteId: 900, difficulty: 0.85, lapses: 3 });
		const rawCache: any = {
			lastFetched: 500,
			concepts: {
				'Multi.md': {
					filePath: 'Multi.md',
					fileName: 'Multi.md',
					primaryDeck: 'Math',
					totalCards: 2,
					problematicCardsCount: 1,
					problematicCards: [],
					cardStats: {
						101: { ...c1, weights: [1, 2], volatility: 0.1, retAtReview: 0.9 },
						102: { ...c2, fsrsHistoryMissing: true },
						900: c2,
					},
					averageEase: 2500,
					averageDifficulty: 0.63,
					totalLapses: 4,
					score: 0.5,
					lastUpdated: 500,
				},
			},
		};

		const serialized = serializeStatsCache(rawCache);
		const savedStats = serialized.concepts['Multi.md'].cardStats;
		// Only cardId keys are serialized (no duplicate noteId key 900) and dropped fields are gone.
		expect(Object.keys(savedStats).map(Number).sort()).toEqual([101, 102]);
		expect(savedStats[101]).not.toHaveProperty('weights');
		expect(savedStats[101]).not.toHaveProperty('volatility');
		expect(savedStats[101]).not.toHaveProperty('retAtReview');
		expect(savedStats[102]).not.toHaveProperty('fsrsHistoryMissing');

		// Loading rebuilds the noteId fallback entry using the worst card (c2, difficulty 0.85).
		const hydrated = normalizeStatsCache(serialized, 'fsrs');
		expect(hydrated.concepts['Multi.md'].cardStats[101].cardId).toBe(101);
		expect(hydrated.concepts['Multi.md'].cardStats[102].cardId).toBe(102);
		expect(hydrated.concepts['Multi.md'].cardStats[900].cardId).toBe(102);
	});
});
