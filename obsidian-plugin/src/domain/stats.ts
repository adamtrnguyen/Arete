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
	volatility?: number;
	lapseRate?: number;
	intervalGrowth?: number;
	pressFatigue?: number;
	retAtReview?: number;
	scheduleAdherence?: number;
	isOverlearning?: boolean;
	answerDistribution?: Record<number, number>;
	desiredRetention?: number;
	weights?: number[];
	fsrsHistoryMissing?: boolean;
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
export const LAPSES_WARNING_AT = 1;
export const DIFFICULTY_WARNING_AT = 0.7;
export const RETRIEVABILITY_WARNING_BELOW = 0.85;
export const STABILITY_WARNING_BELOW = 7;
export const INTERVAL_GROWTH_WARNING_BELOW = 1.2;
export const PRESS_FATIGUE_ERROR_ABOVE = 0.3;
export const DAYS_OVERDUE_ERROR_ABOVE = 7;

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
