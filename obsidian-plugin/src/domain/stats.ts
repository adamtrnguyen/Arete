/**
 * Domain types for card statistics.
 *
 * Pure data structures with no UI or I/O dependencies.
 */

export interface AnkiCardStats {
	cardId: number;
	noteId: number;
	lapses: number;
	ease: number; // Factor (SM-2)
	difficulty?: number; // FSRS Difficulty (0-1 approx, scaled)
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
	lapses: number;
	difficulty: number | null; // Avg
	stability: number | null; // Avg
	retrievability: number | null; // Avg

	// Problematic
	problematicCount: number;
	score: number; // For sorting
}

export interface StatsCache {
	concepts: Record<string, ConceptStats>; // Keyed by filePath
	lastFetched: number;
}
