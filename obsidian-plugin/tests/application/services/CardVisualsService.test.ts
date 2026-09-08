import { CardVisualsService } from '@/application/services/CardVisualsService';
import type { AnkiCardStats } from '@/domain/stats';

describe('CardVisualsService', () => {
	const healthyStats: AnkiCardStats = {
		cardId: 101,
		noteId: 123,
		deckName: 'Default',
		difficulty: 0.3, // stored 0..1, shown as 3.0/10
		lapses: 0,
		ease: 250,
		reps: 10,
		interval: 100,
		due: 1762277751000,
		averageTime: 5,
	};

	const redStats: AnkiCardStats = {
		cardId: 102,
		noteId: 456,
		deckName: 'Default',
		difficulty: 0.95, // stored 0..1, shown as 9.5/10
		lapses: 10,
		ease: 130,
		reps: 50,
		interval: 1,
		due: 1762277751000,
		averageTime: 20,
	};

	it('should return green visuals for healthy cards', () => {
		const visuals = CardVisualsService.getGutterVisuals(healthyStats, 'fsrs', true);
		expect(visuals.barColor).toBe('var(--color-green)');
		expect(visuals.diffText).toBe('3.0');
	});

	it('should return red visuals for high difficulty/lapses', () => {
		const visuals = CardVisualsService.getGutterVisuals(redStats, 'fsrs', true);
		expect(visuals.barColor).toBe('var(--color-red)');
		expect(visuals.shadowColor).toBe('var(--color-red)');
	});

	it('should return gray visuals for unsynced cards', () => {
		const visuals = CardVisualsService.getGutterVisuals(null, 'fsrs', false);
		expect(visuals.barColor).toBe('var(--text-muted)');
	});

	it('should return accent visuals for synced cards with no stats', () => {
		const visuals = CardVisualsService.getGutterVisuals(null, 'fsrs', true);
		expect(visuals.barColor).toBe('var(--interactive-accent)');
	});

	it('should format SM2 ease correctly', () => {
		const sm2Stats: AnkiCardStats = { ...healthyStats, ease: 2500 };
		const visuals = CardVisualsService.getGutterVisuals(sm2Stats, 'sm2', true);
		expect(visuals.diffText).toBe('E:250%');
	});
});
