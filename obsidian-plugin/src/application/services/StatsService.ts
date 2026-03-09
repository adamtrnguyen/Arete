import { App, TFile } from 'obsidian';
import { AretePluginSettings } from '@/domain/settings';
import { AreteClient } from '@/infrastructure/arete/AreteClient';

import type { AnkiCardStats, ConceptStats, StatsNode, StatsCache } from '@/domain/stats';

export class StatsService {
	app: App;
	settings: AretePluginSettings;
	cache: StatsCache;
	private client: AreteClient;

	constructor(
		app: App,
		settings: AretePluginSettings,
		client: AreteClient,
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
			if (cache?.frontmatter?.cards) {
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
									front: card.front || card.Front || 'Unknown',
									back: card.back || card.Back || '',
								});
							}
						}
					});
				}
			}
		}

		if (nidMap.size === 0) {
			console.warn('[Arete] No linked Anki cards found in vault.');
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

			// Check if problematic
			let isProblematic = false;
			const issues: string[] = [];

			// Common: Lapses
			if (stat.lapses >= this.settings.stats_lapse_threshold) {
				isProblematic = true;
				issues.push(`Lapses: ${stat.lapses}`);
			}

			// Algo Specific
			if (this.settings.stats_algorithm === 'fsrs') {
				if (
					stat.difficulty !== undefined &&
					stat.difficulty > this.settings.stats_difficulty_threshold
				) {
					isProblematic = true;
					issues.push(`Diff: ${(stat.difficulty * 100).toFixed(0)}%`);
				}
			} else {
				// SM-2
				if (stat.ease < this.settings.stats_ease_threshold) {
					isProblematic = true;
					issues.push(`Ease: ${(stat.ease / 10).toFixed(0)}%`);
				}
			}

			if (isProblematic) {
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
			problematicCount: 0,
			score: 0,
		};

		// 2. Build Tree Structure
		// Map deck names to nodes for quick lookup
		const deckMap = new Map<string, StatsNode>();
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
			const diff =
				c.countDifficulty && c.countDifficulty > 0
					? (c.sumDifficulty || 0) / c.countDifficulty
					: null;
			const stab =
				c.countStability && c.countStability > 0
					? (c.sumStability || 0) / c.countStability
					: null;
			const ret =
				c.countRetrievability && c.countRetrievability > 0
					? (c.sumRetrievability || 0) / c.countRetrievability
					: null;

			const leaf: StatsNode = {
				title: c.fileName.replace('.md', ''),
				filePath: c.filePath,
				deckName: currentPath,
				isLeaf: true,
				children: [],
				count: c.totalCards,
				lapses: c.totalLapses,
				difficulty: diff,
				stability: stab,
				retrievability: ret,
				problematicCount: c.problematicCardsCount,
				score: c.score,
			};
			parent.children.push(leaf);
		}

		// 3. Aggregate Metrics (Post-Order Traversal)
		this.aggregateNode(root);

		// 4. Sort (Optional: by score or name)
		this.sortTree(root);

		return root;
	}

	private aggregateNode(node: StatsNode) {
		if (node.isLeaf) return;

		let sumDiff = 0,
			countDiff = 0;
		let sumStab = 0,
			countStab = 0;
		let sumRet = 0,
			countRet = 0;

		for (const child of node.children) {
			this.aggregateNode(child);

			node.count += child.count;
			node.lapses += child.lapses;
			node.problematicCount += child.problematicCount;

			if (child.difficulty !== null) {
				sumDiff += child.difficulty * child.count;
				countDiff += child.count;
			}
			if (child.stability !== null) {
				sumStab += child.stability * child.count;
				countStab += child.count;
			}
			if (child.retrievability !== null) {
				sumRet += child.retrievability * child.count;
				countRet += child.count;
			}
		}

		if (countDiff > 0) node.difficulty = sumDiff / countDiff;
		if (countStab > 0) node.stability = sumStab / countStab;
		if (countRet > 0) node.retrievability = sumRet / countRet;

		// Score based on density of issues
		if (node.count > 0) node.score = node.problematicCount / node.count;
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
		try {
			const data = await this.client.invoke('/anki/stats', { nids });

			if (Array.isArray(data)) {
				// Map snake_case (Python) to camelCase (TS)
				// Note: difficulty is already 1-10 scale from backend
				return data.map((d: any) => ({
					cardId: d.card_id,
					noteId: d.note_id,
					lapses: d.lapses,
					ease: d.ease,
					difficulty: d.difficulty, // 1-10 scale from backend
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
			} else {
				console.warn('[Arete] Unexpected stats response format:', data);
			}
		} catch (e) {
			console.error('[Arete] Failed to fetch stats via AreteClient:', e);
			// Caller should handle UI feedback
		}

		return [];
	}
}
