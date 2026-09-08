/**
 * The factories must agree with the product, or they are worse than no factory.
 *
 * These assertions encode the two facts that three test files got wrong: ids nest
 * under `anki`, and difficulty is stored 0..1 and only scaled for display.
 */

import { card, noteYaml, ankiCardStats } from './factories';
import { CardParserService } from '@/application/services/CardParserService';
import { difficultyOutOfTen } from '@/domain/stats';

describe('test factories', () => {
	it('nests the anki ids where the parser reads them', () => {
		const c = card({ nid: 1771190868878 });
		expect(c.anki?.nid).toBe('1771190868878');
		expect((c as unknown as Record<string, unknown>).nid).toBeUndefined();
	});

	it('produces YAML that CardParserService can read back', () => {
		const yaml = noteYaml({ cards: [card({ nid: 1762277751241 }), card({ Front: 'Second' })] });
		const result = CardParserService.parseCards(yaml);

		expect(result.hasCards).toBe(true);
		expect(result.ranges.length).toBe(2);
		expect(result.ranges[0].nid).toBe(1762277751241);
		expect(result.ranges[1].nid).toBeNull();
	});

	it('stores difficulty on the scale the backend sends, not the one shown', () => {
		const stats = ankiCardStats({ difficulty: 0.95 });
		expect(stats.difficulty).toBeLessThanOrEqual(1);
		expect(difficultyOutOfTen(stats.difficulty!)).toBeCloseTo(9.5);
	});
});
