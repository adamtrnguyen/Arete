import { AreteClient } from '../../infrastructure/arete/AreteClient';
import { StatsCache, ProblematicCard } from '@/domain/stats';

export interface LeechCard extends ProblematicCard {
	filePath: string;
	fileName: string;
	deck: string;
}

export class LeechService {
	private areteClient: AreteClient;

	constructor(areteClient: AreteClient) {
		this.areteClient = areteClient;
	}

	/**
	 * Flattens the StatsCache into a list of specific Leech cards, sorted by severity (lapses).
	 */
	getLeeches(cache: StatsCache): LeechCard[] {
		const leeches: LeechCard[] = [];
		const concepts = Object.values(cache.concepts);

		for (const concept of concepts) {
			if (concept.problematicCards && concept.problematicCards.length > 0) {
				for (const card of concept.problematicCards) {
					// Optionally filter further? For now, we trust 'problematicCards' logic from StatsService
					// which already picks high lapses / low ease.
					leeches.push({
						...card,
						filePath: concept.filePath,
						fileName: concept.fileName,
						deck: concept.primaryDeck,
					});
				}
			}
		}

		// Sort by Lapses Descending
		return leeches.sort((a, b) => b.lapses - a.lapses);
	}

	async suspendCard(cardId: number): Promise<boolean> {
		return this.areteClient.suspendCards([cardId]);
	}

	async unsuspendCard(cardId: number): Promise<boolean> {
		return this.areteClient.unsuspendCards([cardId]);
	}
}
