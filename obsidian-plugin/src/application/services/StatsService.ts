import { App, TFile } from 'obsidian';
import { AretePluginSettings } from '@/domain/settings';
import { CardStatsSource } from '@/domain/ports';

import type {
	AnkiCardStats,
	ConceptStats,
	StatsNode,
	StatsCache,
	SyncedCardState,
} from '@/domain/stats';
import {
	cardState,
	difficultyOutOfTen,
	emptyCardStateCounts,
	metricStates,
	uniqueCardStats,
} from '@/domain/stats';
import { debugLog } from '@/domain/debugLog';

export class StatsService {
	app: App;
	settings: AretePluginSettings;
	cache: StatsCache;
	private client: CardStatsSource;

	constructor(
		app: App,
		settings: AretePluginSettings,
		client: CardStatsSource,
		initialCache?: StatsCache,
	) {
		this.app = app;
		this.settings = settings;
		this.client = client;
		this.cache = initialCache || { concepts: {}, lastFetched: 0 };
	}

	getCache(): StatsCache {
		return this.cache;
	}

	clearCache(): void {
		this.cache = { concepts: {}, lastFetched: 0 };
	}

	async refreshStats(): Promise<ConceptStats[]> {
		const files = this.app.vault.getMarkdownFiles();
		const nidMap = new Map<
			number,
			{ file: TFile; index: number; front: string; back: string }
		>();
		const filesWithCards: Map<string, TFile> = new Map();
		const conceptMap: Record<string, ConceptStats> = {};
		const conceptDeckCounts: Record<string, Record<string, number>> = {}; // filePath -> { deckName: count }

		// 1. Scan Vault
		for (const file of files) {
			const cache = this.app.metadataCache.getFileCache(file);
			if (cache?.frontmatter?.arete === true && cache.frontmatter.cards) {
				const cards = cache.frontmatter.cards;
				const yamlDeck = cache.frontmatter.deck; // Custom YAML field

				if (Array.isArray(cards) && cards.length > 0) {
					// Initialize map entry with YAML deck if present
					filesWithCards.set(file.path, file);
					conceptMap[file.path] = {
						filePath: file.path,
						fileName: file.name,
						primaryDeck: yamlDeck || 'Unknown', // Prioritize YAML
						totalCards: 0,
						problematicCardsCount: 0,
						problematicCards: [],
						cardStats: {}, // Init empty
						averageEase: 0,
						averageDifficulty: null, // Init as null
						difficultyCount: 0, // NEW: Track valid difficulty
						totalLapses: 0,
						score: 0,
						lastUpdated: Date.now(),
						// Init raw sums
						sumDifficulty: 0,
						countDifficulty: 0,
						sumStability: 0,
						countStability: 0,
						sumRetrievability: 0,
						countRetrievability: 0,
					};
					conceptDeckCounts[file.path] = {};

					cards.forEach((card: any, index: number) => {
						if (!card) return; // Skip null/malformed cards

						const rawNid = card.anki?.nid;

						if (rawNid) {
							const nid = parseInt(rawNid);
							if (!isNaN(nid)) {
								nidMap.set(nid, {
									file,
									index,
									front: card.Front || card.Text || 'Unknown',
									back: card.Back || card['Back Extra'] || '',
								});
							}
						}
					});
				}
			}
		}

		if (nidMap.size === 0) {
			debugLog('[Arete] No linked Anki cards found in vault.');
			this.cache.concepts = {};
			this.cache.lastFetched = Date.now();
			return [];
		}

		// 2. Fetch from Anki
		const nids = Array.from(nidMap.keys());
		const cardStats = await this.fetchAnkiCardStats(nids);

		// 3. Process fetched stats
		for (const stat of cardStats) {
			const meta = nidMap.get(stat.noteId);
			if (!meta) continue;

			const concept = conceptMap[meta.file.path];
			if (!concept) continue;

			// Store by Card ID (Precise)
			stat.front = meta.front;
			concept.cardStats[stat.cardId] = stat;

			// Store by Note ID (Fallback/Merged) - handle multiple cards per Note ID
			const existing = concept.cardStats[stat.noteId];
			if (existing) {
				let replace = false;

				// 1. FSRS Difficulty: Prefer defined, then higher
				if (this.settings.stats_algorithm === 'fsrs') {
					if (stat.difficulty !== undefined) {
						if (existing.difficulty === undefined) replace = true;
						else if (stat.difficulty > existing.difficulty) replace = true;
					}
				}
				// 2. SM-2 Ease: Prefer Lower (harder)
				else {
					if (stat.ease < existing.ease) replace = true;
				}

				// 3. Lapses: If stats are equal/comparable, prefer higher lapses
				if (!replace && stat.lapses > existing.lapses) {
					if (this.settings.stats_algorithm === 'fsrs') {
						// Only override if difficulty logic didn't already decide (e.g. both undefined or equal)
						if (stat.difficulty === existing.difficulty) replace = true;
					} else {
						// Only override if ease is equal
						if (stat.ease === existing.ease) replace = true;
					}
				}

				if (replace) {
					concept.cardStats[stat.noteId] = stat;
				}
			} else {
				concept.cardStats[stat.noteId] = stat;
			}

			concept.totalCards++;
			concept.totalLapses += stat.lapses;
			// Accumulate metrics
			concept.averageEase += stat.ease;
			if (stat.difficulty !== undefined && stat.difficulty !== null) {
				// If averageDifficulty is null, initialize it to 0 before adding
				if (concept.averageDifficulty === null) {
					concept.averageDifficulty = 0;
				}
				concept.averageDifficulty += stat.difficulty;
				concept.difficultyCount = (concept.difficultyCount || 0) + 1;

				// Extended agg
				concept.sumDifficulty = (concept.sumDifficulty || 0) + stat.difficulty;
				concept.countDifficulty = (concept.countDifficulty || 0) + 1;
			}
			if (stat.stability !== undefined && stat.stability !== null) {
				concept.sumStability = (concept.sumStability || 0) + stat.stability;
				concept.countStability = (concept.countStability || 0) + 1;
			}
			if (stat.retrievability !== undefined && stat.retrievability !== null) {
				concept.sumRetrievability = (concept.sumRetrievability || 0) + stat.retrievability;
				concept.countRetrievability = (concept.countRetrievability || 0) + 1;
			}

			// Track Deck (pick primary later if not set by YAML)
			const deck = stat.deckName || 'Default';
			if (!conceptDeckCounts[meta.file.path][deck]) {
				conceptDeckCounts[meta.file.path][deck] = 0;
			}
			conceptDeckCounts[meta.file.path][deck]++;

			// Check if problematic using the shared domain thresholds
			const states = metricStates(stat, this.settings);
			const issues: string[] = [];

			if (states.lapses === 'error') {
				issues.push(`Lapses: ${stat.lapses}`);
			}

			if (this.settings.stats_algorithm === 'fsrs') {
				if (states.difficulty === 'error' && stat.difficulty != null) {
					issues.push(`Diff: ${difficultyOutOfTen(stat.difficulty).toFixed(1)}/10`);
				}
			} else if (states.ease === 'warning') {
				issues.push(`Ease: ${(stat.ease / 10).toFixed(0)}%`);
			}

			if (issues.length > 0) {
				concept.problematicCardsCount++;
				concept.problematicCards.push({
					front: meta.front,
					back: meta.back,
					cardId: stat.cardId,
					noteId: stat.noteId,
					lapses: stat.lapses,
					ease: stat.ease,
					difficulty: stat.difficulty,
					deckName: deck,
					issue: issues.join(', '),
				});
			}
		}

		// Finalize Averages, Scores, and Primary Deck
		const results: ConceptStats[] = [];
		for (const key in conceptMap) {
			const c = conceptMap[key];
			if (c.totalCards > 0) {
				c.averageEase = Math.round(c.averageEase / c.totalCards);

				if (c.difficultyCount && c.difficultyCount > 0) {
					c.averageDifficulty = parseFloat(
						(c.averageDifficulty! / c.difficultyCount).toFixed(2),
					);
				} else {
					c.averageDifficulty = null; // Explicitly null if no valid difficulty stats
				}

				c.score = c.problematicCardsCount / c.totalCards;

				// Determine Primary Deck if not already set by YAML
				if (c.primaryDeck === 'Unknown') {
					const decks = conceptDeckCounts[key];
					let maxCount = 0;
					let primary = 'Unknown';
					for (const deck in decks) {
						if (decks[deck] > maxCount) {
							maxCount = decks[deck];
							primary = deck;
						}
					}
					c.primaryDeck = primary;
				}
			}
			results.push(c);
		}

		// Update Cache
		this.cache.concepts = conceptMap;
		this.cache.lastFetched = Date.now();

		return results.sort((a, b) => b.score - a.score); // Sort by problematic score desc
	}

	getAggregatedStats(concepts: ConceptStats[]): StatsNode {
		// 1. Create Root
		const root: StatsNode = {
			title: 'Vault',
			filePath: '',
			deckName: 'All',
			isLeaf: false,
			children: [],
			count: 0,
			lapses: 0,
			difficulty: null,
			stability: null,
			retrievability: null,
			stateCounts: emptyCardStateCounts(),
			problematicCount: 0,
			score: 0,
		};

		// 2. Build Tree Structure
		// Map deck names to nodes for quick lookup
		const deckMap = new Map<string, StatsNode>();
		const leafMetricCounts = new WeakMap<
			StatsNode,
			{ diff: number; stab: number; ret: number }
		>();
		deckMap.set('All', root); // 'All' concept is abstract root

		for (const c of concepts) {
			// Determine deck hierarchy
			const deckPath =
				c.primaryDeck && c.primaryDeck !== 'Unknown' ? c.primaryDeck : 'Default';
			const parts = deckPath.split('::');

			let currentPath = '';
			let parent = root;

			// Traverse/Create Deck Nodes
			for (const part of parts) {
				const fullPath = currentPath ? `${currentPath}::${part}` : part;

				if (!deckMap.has(fullPath)) {
					const deckNode: StatsNode = {
						title: part,
						filePath: '', // Folder
						deckName: fullPath,
						isLeaf: false,
						children: [],
						count: 0,
						lapses: 0,
						difficulty: null,
						stability: null,
						retrievability: null,
						stateCounts: emptyCardStateCounts(),
						problematicCount: 0,
						score: 0,
					};
					parent.children.push(deckNode);
					deckMap.set(fullPath, deckNode);
					parent = deckNode;
				} else {
					parent = deckMap.get(fullPath)!;
				}
				currentPath = fullPath;
			}

			// Add File Node (Leaf) to the specific deck
			const diffCount = c.countDifficulty || 0;
			const stabCount = c.countStability || 0;
			const retCount = c.countRetrievability || 0;
			const diff = diffCount > 0 ? (c.sumDifficulty || 0) / diffCount : null;
			const stab = stabCount > 0 ? (c.sumStability || 0) / stabCount : null;
			const ret = retCount > 0 ? (c.sumRetrievability || 0) / retCount : null;

			// Each card is judged once, with the same function the gutter uses.
			const stateCounts = emptyCardStateCounts();
			for (const card of uniqueCardStats(c.cardStats)) {
				const { state } = cardState(card, true, this.settings);
				if (state !== 'muted') stateCounts[state]++;
			}

			const leaf: StatsNode = {
				title: c.fileName.replace(/\.md$/, ''),
				filePath: c.filePath,
				deckName: currentPath,
				isLeaf: true,
				children: [],
				count: c.totalCards,
				lapses: c.totalLapses,
				difficulty: diff,
				stability: stab,
				retrievability: ret,
				stateCounts,
				problematicCount: c.problematicCardsCount,
				score: c.score,
			};
			leafMetricCounts.set(leaf, { diff: diffCount, stab: stabCount, ret: retCount });
			parent.children.push(leaf);
		}

		// 3. Aggregate Metrics (Post-Order Traversal)
		this.aggregateNode(root, leafMetricCounts);

		// 4. Sort (Optional: by score or name)
		this.sortTree(root);

		return root;
	}

	private aggregateNode(
		node: StatsNode,
		leafMetricCounts: WeakMap<StatsNode, { diff: number; stab: number; ret: number }>,
	): { diff: number; stab: number; ret: number } {
		if (node.isLeaf) {
			return leafMetricCounts.get(node) ?? { diff: 0, stab: 0, ret: 0 };
		}

		let sumDiff = 0,
			countDiff = 0;
		let sumStab = 0,
			countStab = 0;
		let sumRet = 0,
			countRet = 0;

		for (const child of node.children) {
			const childCounts = this.aggregateNode(child, leafMetricCounts);

			node.count += child.count;
			node.lapses += child.lapses;
			for (const key of Object.keys(node.stateCounts) as SyncedCardState[]) {
				node.stateCounts[key] += child.stateCounts[key];
			}
			node.problematicCount += child.problematicCount;

			if (child.difficulty !== null && childCounts.diff > 0) {
				sumDiff += child.difficulty * childCounts.diff;
				countDiff += childCounts.diff;
			}
			if (child.stability !== null && childCounts.stab > 0) {
				sumStab += child.stability * childCounts.stab;
				countStab += childCounts.stab;
			}
			if (child.retrievability !== null && childCounts.ret > 0) {
				sumRet += child.retrievability * childCounts.ret;
				countRet += childCounts.ret;
			}
		}

		if (countDiff > 0) node.difficulty = sumDiff / countDiff;
		if (countStab > 0) node.stability = sumStab / countStab;
		if (countRet > 0) node.retrievability = sumRet / countRet;

		// Score based on density of issues
		if (node.count > 0) node.score = node.problematicCount / node.count;

		return { diff: countDiff, stab: countStab, ret: countRet };
	}

	private sortTree(node: StatsNode) {
		if (node.isLeaf) return;
		// Sort by Score Descending
		node.children.sort((a, b) => b.score - a.score);

		for (const child of node.children) {
			this.sortTree(child);
		}
	}

	async fetchAnkiCardStats(nids: number[]): Promise<AnkiCardStats[]> {
		let data: unknown;
		try {
			data = await this.client.getCardStats(nids);
		} catch (e) {
			console.error('[Arete] Failed to fetch stats via AreteClient:', e);
			throw e;
		}

		if (!Array.isArray(data)) {
			console.error('[Arete] Unexpected stats response format:', data);
			throw new Error('Unexpected stats response format.');
		}

		// Map snake_case (Python) to camelCase (TS)
		// difficulty arrives normalized 0..1; views scale it with difficultyOutOfTen
		return data.map((d: any) => ({
			cardId: d.card_id,
			noteId: d.note_id,
			lapses: d.lapses,
			ease: d.ease,
			difficulty: d.difficulty, // 0..1
			deckName: d.deck_name,
			interval: d.interval,
			due: d.due,
			reps: d.reps,
			averageTime: d.average_time_ms, // Updated field name
			front: d.front,
			// Enriched fields mapping (only for FSRS)
			stability: d.stability,
			retrievability: d.current_retrievability,
			daysOverdue: d.days_overdue,
			volatility: d.volatility,
			lapseRate: d.lapse_rate,
			intervalGrowth: d.interval_growth,
			pressFatigue: d.press_fatigue,
			retAtReview: d.ret_at_review,
			scheduleAdherence: d.schedule_adherence,
			isOverlearning: d.is_overlearning,
			answerDistribution: d.answer_distribution,
			desiredRetention: d.desired_retention,
			weights: d.weights,
			fsrsHistoryMissing: d.fsrs_history_missing,
		}));
	}
}
