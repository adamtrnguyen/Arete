/**
 * Domain types for card statistics.
 *
 * Pure data structures with no UI or I/O dependencies.
 */

import type { AretePluginSettings } from '@/domain/settings';

export interface AnkiCardStats {
	cardId: number;
	noteId: number;
	lapses: number;
	ease: number; // Factor (SM-2)
	difficulty?: number; // FSRS difficulty, normalized 0..1 (see difficultyOutOfTen)
	deckName: string;
	interval: number;
	due: number; // Epoch
	reps: number;
	averageTime: number; // ms
	front?: string; // Content from Obsidian

	// FSRS Enriched Metrics
	stability?: number;
	retrievability?: number;
	daysOverdue?: number;
	lapseRate?: number;
	intervalGrowth?: number;
	pressFatigue?: number;
	scheduleAdherence?: number;
	isOverlearning?: boolean;
	answerDistribution?: Record<number, number>;
	desiredRetention?: number;
}

export interface ProblematicCard {
	front: string;
	back: string;
	cardId: number;
	noteId: number;
	lapses: number;
	ease: number;
	difficulty?: number;
	deckName: string;
	issue: string; // e.g. "High Lapses (5)" or "Ease Hell (130%)"
}

export interface ConceptStats {
	filePath: string;
	fileName: string;
	primaryDeck: string;
	totalCards: number;
	problematicCardsCount: number;
	problematicCards: ProblematicCard[];
	cardStats: Record<number, AnkiCardStats>; // Store all stats by Note ID
	averageEase: number;
	averageDifficulty: number | null; // Null if no FSRS data found
	difficultyCount?: number; // Internal tracking
	totalLapses: number;
	score: number; // 0.0 to 1.0 (Problematic Ratio)
	lastUpdated: number;
	// Raw sums for aggregation
	sumDifficulty?: number;
	countDifficulty?: number;
	sumStability?: number;
	countStability?: number;
	sumRetrievability?: number;
	countRetrievability?: number;
}

export interface StatsNode {
	title: string;
	filePath: string;
	deckName: string; // If leaf, primary deck. If node, aggregation.
	isLeaf: boolean;
	children: StatsNode[];

	// Aggregated Metrics
	count: number;
	lapses: number; // Sum, shown as a total; never judged against a per-card cut-off
	difficulty: number | null; // Avg
	stability: number | null; // Avg
	retrievability: number | null; // Avg

	// Cards of this node by state; the four counts add up to `count`
	stateCounts: CardStateCounts;

	// Problematic
	problematicCount: number;
	score: number; // For sorting
}

export interface StatsCache {
	concepts: Record<string, ConceptStats>; // Keyed by filePath
	lastFetched: number;
}

/**
 * FSRS difficulty for display.
 *
 * The backend normalizes Anki's native 1..10 difficulty to 0..1, which is what
 * `AnkiCardStats.difficulty` and `settings.stats_difficulty_threshold` both hold.
 * Anki users read it out of ten, so every view scales it here and nowhere else.
 * Thresholds stay on the stored 0..1 scale.
 */
export function difficultyOutOfTen(difficulty: number): number {
	return difficulty * 10;
}

/**
 * A card's due time in epoch seconds, or null when Anki's `due` is not a timestamp.
 * Anki overloads the field: epoch seconds only for (re)learning cards; a day number
 * since collection creation for review cards; a queue position for new cards. Read as
 * a timestamp, those land in 1970 ("20721d ago"). The plugin lacks the collection's
 * creation date, so it shows no due date rather than a wrong one.
 */
export function dueEpochSeconds(due: number | null | undefined): number | null {
	return due != null && due >= 1_000_000_000 ? due : null;
}

/**
 * Each card of a concept once. `cardStats` holds a card under its card id and, as a
 * fallback for the gutter, under its note id too, so its values repeat cards.
 */
export function uniqueCardStats(cardStats: Record<number, AnkiCardStats>): AnkiCardStats[] {
	const byCard = new Map<number, AnkiCardStats>();
	for (const stat of Object.values(cardStats)) {
		if (!byCard.has(stat.cardId)) byCard.set(stat.cardId, stat);
	}
	return [...byCard.values()];
}

/**
 * Whether `candidate` should replace `existing` as the worst-card fallback stored under
 * a shared `noteId` (e.g. a multi-cloze note whose gutter shows one marker per note).
 */
export function shouldReplaceNoteFallback(
	existing: AnkiCardStats,
	candidate: AnkiCardStats,
	algorithm: 'fsrs' | 'sm2',
): boolean {
	if (algorithm === 'fsrs') {
		if (candidate.difficulty !== undefined) {
			if (existing.difficulty === undefined) return true;
			if (candidate.difficulty > existing.difficulty) return true;
		}
		return candidate.lapses > existing.lapses && candidate.difficulty === existing.difficulty;
	}
	if (candidate.ease < existing.ease) return true;
	return candidate.lapses > existing.lapses && candidate.ease === existing.ease;
}

const DROPPED_CARD_STAT_KEYS = new Set([
	'weights',
	'volatility',
	'retAtReview',
	'fsrsHistoryMissing',
]);

function stripLegacyCardStatFields(stat: AnkiCardStats): AnkiCardStats {
	const entries = Object.entries(stat).filter(([k]) => !DROPPED_CARD_STAT_KEYS.has(k));
	return Object.fromEntries(entries) as unknown as AnkiCardStats;
}

function isPlainObject(value: unknown): value is Record<string, unknown> {
	return Boolean(value) && typeof value === 'object' && !Array.isArray(value);
}

/**
 * Coerce raw `statsCache` data loaded from `data.json` into a safe in-memory cache and
 * rebuild the `noteId` fallback entries that `serializeStatsCache` strips on save.
 */
export function normalizeStatsCache(raw: unknown, algorithm: 'fsrs' | 'sm2' = 'fsrs'): StatsCache {
	if (!isPlainObject(raw)) return { concepts: {}, lastFetched: 0 };
	const lastFetched =
		typeof raw.lastFetched === 'number' &&
		Number.isFinite(raw.lastFetched) &&
		raw.lastFetched >= 0
			? raw.lastFetched
			: 0;
	const concepts: Record<string, ConceptStats> = {};
	if (isPlainObject(raw.concepts)) {
		for (const [filePath, entry] of Object.entries(raw.concepts)) {
			if (!isPlainObject(entry)) continue;
			const rawStats = isPlainObject(entry.cardStats) ? entry.cardStats : {};
			const rebuilt: Record<number, AnkiCardStats> = {};
			const noteFallback: Record<number, AnkiCardStats> = {};
			for (const [key, stat] of Object.entries(rawStats)) {
				if (!isPlainObject(stat)) continue;
				const clean = stripLegacyCardStatFields(stat as unknown as AnkiCardStats);
				if (typeof clean.cardId === 'number' && Number.isFinite(clean.cardId)) {
					rebuilt[clean.cardId] = clean;
					if (typeof clean.noteId === 'number' && Number.isFinite(clean.noteId)) {
						const prev = noteFallback[clean.noteId];
						if (!prev || shouldReplaceNoteFallback(prev, clean, algorithm)) {
							noteFallback[clean.noteId] = clean;
						}
					}
				} else {
					const numKey = Number(key);
					if (Number.isFinite(numKey)) rebuilt[numKey] = clean;
				}
			}
			for (const [nidStr, stat] of Object.entries(noteFallback)) {
				const nid = Number(nidStr);
				if (!(nid in rebuilt)) rebuilt[nid] = stat;
			}
			concepts[filePath] = {
				...(entry as unknown as ConceptStats),
				filePath: typeof entry.filePath === 'string' ? entry.filePath : filePath,
				fileName: typeof entry.fileName === 'string' ? entry.fileName : filePath,
				primaryDeck: typeof entry.primaryDeck === 'string' ? entry.primaryDeck : 'Unknown',
				totalCards:
					typeof entry.totalCards === 'number' && Number.isFinite(entry.totalCards)
						? entry.totalCards
						: 0,
				problematicCardsCount:
					typeof entry.problematicCardsCount === 'number' &&
					Number.isFinite(entry.problematicCardsCount)
						? entry.problematicCardsCount
						: 0,
				problematicCards: Array.isArray(entry.problematicCards)
					? entry.problematicCards
					: [],
				cardStats: rebuilt,
				averageEase:
					typeof entry.averageEase === 'number' && Number.isFinite(entry.averageEase)
						? entry.averageEase
						: 0,
				averageDifficulty:
					typeof entry.averageDifficulty === 'number' &&
					Number.isFinite(entry.averageDifficulty)
						? entry.averageDifficulty
						: null,
				totalLapses:
					typeof entry.totalLapses === 'number' && Number.isFinite(entry.totalLapses)
						? entry.totalLapses
						: 0,
				score:
					typeof entry.score === 'number' && Number.isFinite(entry.score)
						? entry.score
						: 0,
				lastUpdated:
					typeof entry.lastUpdated === 'number' && Number.isFinite(entry.lastUpdated)
						? entry.lastUpdated
						: 0,
			};
		}
	}
	return { concepts, lastFetched };
}

/**
 * Prepare `statsCache` for `data.json`: store each card once under its `cardId` instead
 * of duplicating every card object under both `cardId` and `noteId`.
 */
export function serializeStatsCache(cache: StatsCache | undefined): StatsCache {
	if (!cache) return { concepts: {}, lastFetched: 0 };
	const concepts: Record<string, ConceptStats> = {};
	for (const [filePath, concept] of Object.entries(cache.concepts ?? {})) {
		if (!concept || !isPlainObject(concept.cardStats)) {
			concepts[filePath] = concept;
			continue;
		}
		const deduped: Record<number, AnkiCardStats> = {};
		for (const stat of uniqueCardStats(concept.cardStats)) {
			if (stat && typeof stat.cardId === 'number' && Number.isFinite(stat.cardId)) {
				deduped[stat.cardId] = stripLegacyCardStatFields(stat);
			}
		}
		concepts[filePath] = { ...concept, cardStats: deduped };
	}
	return { ...cache, concepts };
}

/** A metric against its cut-offs. `none`: the stat is absent, so there is nothing to judge. */
export type MetricState = 'none' | 'ok' | 'warning' | 'error';

/**
 * A card as the views show it. `muted`: not synced to Anki. `nodata`: synced, but nothing
 * to judge (no stats yet, or no review metric present). `success`, `warning`, `error`: the
 * worst state among the five metrics below.
 */
export type CardState = 'muted' | 'nodata' | 'success' | 'warning' | 'error';

/** The states of a synced card, for counting. Their counts add up to the number of cards. */
export type SyncedCardState = Exclude<CardState, 'muted'>;
export type CardStateCounts = Record<SyncedCardState, number>;

export function emptyCardStateCounts(): CardStateCounts {
	return { success: 0, warning: 0, error: 0, nodata: 0 };
}

/** The settings that decide a card's state. */
export type CardStateSettings = Pick<
	AretePluginSettings,
	| 'stats_algorithm'
	| 'stats_lapse_threshold'
	| 'stats_difficulty_threshold'
	| 'stats_ease_threshold'
>;

/** Raw values to judge. A deck row passes its aggregates here; a card passes its stats. */
export interface CardMetricValues {
	lapses?: number | null;
	difficulty?: number | null; // 0..1
	retrievability?: number | null; // 0..1
	stability?: number | null; // days
	ease?: number | null; // SM-2 factor, 2100 = 210%
	intervalGrowth?: number | null;
	pressFatigue?: number | null;
	daysOverdue?: number | null;
}

export interface CardMetricStates {
	// The five metrics that decide `CardStateResult.state`.
	lapses: MetricState;
	difficulty: MetricState;
	retrievability: MetricState;
	stability: MetricState;
	ease: MetricState;
	// Shown by the card stats modal only; they never change a card's state.
	intervalGrowth: MetricState;
	pressFatigue: MetricState;
	daysOverdue: MetricState;
}

export interface CardStateResult {
	state: CardState;
	metrics: CardMetricStates;
}

/** Warning cut-offs that are not settings. Each warns at the value or beyond it, or below it where noted. */
const LAPSES_WARNING_AT = 1;
const DIFFICULTY_WARNING_AT = 0.7;
const RETRIEVABILITY_WARNING_BELOW = 0.85;
const STABILITY_WARNING_BELOW = 7;
const INTERVAL_GROWTH_WARNING_BELOW = 1.2;
const PRESS_FATIGUE_ERROR_ABOVE = 0.3;
const DAYS_OVERDUE_ERROR_ABOVE = 7;

function present(value: number | null | undefined): value is number {
	return value != null && !Number.isNaN(value);
}

function atLeast(
	value: number | null | undefined,
	warningAt: number | null,
	errorAt: number | null,
): MetricState {
	if (!present(value)) return 'none';
	if (errorAt !== null && value >= errorAt) return 'error';
	if (warningAt !== null && value >= warningAt) return 'warning';
	return 'ok';
}

/**
 * Each metric against the cut-offs. Lapses and FSRS difficulty warn from a fixed value and
 * err at the user's threshold; retrievability warns below 0.85, because a card nearing
 * its due date sits there in normal use and is no fault; stability warns below 7 days; SM-2 ease warns below the user's threshold and only under the SM-2 algorithm
 * (a zero factor is a card Anki has not scheduled, so it is `none`).
 */
export function metricStates(
	values: CardMetricValues,
	settings: CardStateSettings,
): CardMetricStates {
	const { ease, retrievability, stability, intervalGrowth, pressFatigue, daysOverdue } = values;
	return {
		lapses: atLeast(values.lapses, LAPSES_WARNING_AT, settings.stats_lapse_threshold),
		difficulty: atLeast(
			values.difficulty,
			DIFFICULTY_WARNING_AT,
			settings.stats_difficulty_threshold,
		),
		retrievability: !present(retrievability)
			? 'none'
			: retrievability < RETRIEVABILITY_WARNING_BELOW
				? 'warning'
				: 'ok',
		stability: !present(stability)
			? 'none'
			: stability < STABILITY_WARNING_BELOW
				? 'warning'
				: 'ok',
		ease:
			settings.stats_algorithm !== 'sm2' || !present(ease) || ease <= 0
				? 'none'
				: ease < settings.stats_ease_threshold
					? 'warning'
					: 'ok',
		intervalGrowth: !present(intervalGrowth)
			? 'none'
			: intervalGrowth < INTERVAL_GROWTH_WARNING_BELOW
				? 'warning'
				: 'ok',
		pressFatigue: !present(pressFatigue)
			? 'none'
			: pressFatigue > PRESS_FATIGUE_ERROR_ABOVE
				? 'error'
				: 'ok',
		daysOverdue: !present(daysOverdue)
			? 'none'
			: daysOverdue > DAYS_OVERDUE_ERROR_ABOVE
				? 'error'
				: 'ok',
	};
}

/**
 * A card's state, for every view that colours a card. An unsynced card is `muted`. A synced
 * card is `nodata` when it has no stats, or when difficulty, retrievability, stability and
 * ease are all absent and lapses are not flagged: a zero lapse count alone is no evidence.
 * Otherwise the state is the worst of the five metrics that are present, or `success`
 * when none is flagged.
 */
export function cardState(
	stats: AnkiCardStats | null | undefined,
	isSynced: boolean,
	settings: CardStateSettings,
): CardStateResult {
	const metrics = metricStates(stats ?? {}, settings);
	if (!isSynced) return { state: 'muted', metrics };
	if (!stats) return { state: 'nodata', metrics };
	const judged = [metrics.difficulty, metrics.retrievability, metrics.stability, metrics.ease];
	if (
		judged.every((m) => m === 'none') &&
		(metrics.lapses === 'none' || metrics.lapses === 'ok')
	) {
		return { state: 'nodata', metrics };
	}
	const worst = [
		metrics.lapses,
		metrics.difficulty,
		metrics.retrievability,
		metrics.stability,
		metrics.ease,
	];
	const state = worst.includes('error')
		? 'error'
		: worst.includes('warning')
			? 'warning'
			: 'success';
	return { state, metrics };
}

export interface GutterStatLabels {
	diffText: string;
	lapseText: string | null;
	tooltip: string;
}

/**
 * Formats the text badges and tooltip shown in the editor gutter for one card.
 * Colouring comes exclusively from `cardState()`.
 */
export function formatGutterStats(
	stats: AnkiCardStats | null,
	algorithm: 'fsrs' | 'sm2',
	isSynced: boolean,
): GutterStatLabels {
	if (!isSynced || !stats) {
		return { diffText: '', lapseText: null, tooltip: '' };
	}

	let diffText: string;
	let lapseText: string | null = null;
	const tooltipLines: string[] = [];

	if (algorithm === 'fsrs') {
		if (stats.difficulty != null && stats.difficulty > 0) {
			const scaled = difficultyOutOfTen(stats.difficulty).toFixed(1);
			diffText = scaled;
			tooltipLines.push(`Difficulty: ${scaled}/10`);
		} else {
			diffText = 'D:?';
		}
	} else if (stats.ease > 0) {
		const easePct = Math.round(stats.ease / 10);
		diffText = `E:${easePct}%`;
		tooltipLines.push(`Ease: ${easePct}%`);
	} else {
		diffText = 'E:?';
	}

	if (stats.lapses > 0) {
		lapseText = `${stats.lapses}L`;
		tooltipLines.push(`Lapses: ${stats.lapses}`);
	}

	return {
		diffText,
		lapseText,
		tooltip: tooltipLines.join('\n'),
	};
}
