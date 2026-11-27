import '../../test-setup';
import { App } from 'obsidian';
import { StatsService } from '@application/services/StatsService';
import { AretePluginSettings } from '@domain/settings';
import { CardStatsSource } from '@/domain/ports';

describe('StatsService Integration', () => {
	let app: App;
	let settings: AretePluginSettings;
	let service: StatsService;
	let source: { getCardStats: jest.Mock };

	beforeEach(() => {
		app = new App();
		settings = {
			python_path: 'python3',
			backend: 'auto',
			anki_connect_url: 'http://localhost:8765',
			stats_algorithm: 'fsrs',
			stats_lapse_threshold: 3,
			stats_ease_threshold: 2100,
			stats_difficulty_threshold: 0.9,
		} as any;

		source = { getCardStats: jest.fn() };
		service = new StatsService(app, settings, source as CardStatsSource);
	});

	test('fetchAnkiCardStats maps snake_case CLI rows including FSRS metrics', async () => {
		const nids = [101, 102];

		source.getCardStats.mockResolvedValue([
			{
				card_id: 1,
				note_id: 101,
				lapses: 0,
				ease: 2500,
				difficulty: 0.85,
				stability: 14.5,
				current_retrievability: 0.91,
				deck_name: 'Deck A',
				interval: 10,
				due: 0,
				reps: 5,
				average_time_ms: 1000,
				front: 'Front 1',
			},
			{
				card_id: 2,
				note_id: 102,
				lapses: 0,
				ease: 2500,
				difficulty: 0.3,
				deck_name: 'Deck A',
				interval: 10,
				due: 0,
				reps: 5,
				average_time_ms: 1200,
				front: 'Front 2',
			},
		]);

		const stats = await service.fetchAnkiCardStats(nids);

		expect(stats).toHaveLength(2);
		expect(stats[0].cardId).toBe(1);
		expect(stats[0].noteId).toBe(101);
		expect(stats[0].ease).toBe(2500);
		expect(stats[0].difficulty).toBe(0.85);
		expect(stats[0].stability).toBe(14.5);
		expect(stats[0].retrievability).toBe(0.91);
		expect(stats[0].averageTime).toBe(1000);

		expect(stats[1].cardId).toBe(2);
		expect(stats[1].difficulty).toBe(0.3);
		expect(stats[1].averageTime).toBe(1200);

		expect(source.getCardStats).toHaveBeenCalledWith(nids);
	});

	test('fetchAnkiCardStats leaves FSRS fields undefined when omitted by backend', async () => {
		const nids = [101];

		source.getCardStats.mockResolvedValue([
			{
				card_id: 1,
				note_id: 101,
				lapses: 2,
				ease: 2500,
				deck_name: 'Deck A',
				interval: 10,
				due: 0,
				reps: 5,
				average_time_ms: 1000,
				front: 'Front 1',
			},
		]);

		const stats = await service.fetchAnkiCardStats(nids);

		expect(stats).toHaveLength(1);
		expect(stats[0].ease).toBe(2500);
		expect(stats[0].difficulty).toBeUndefined();
		expect(stats[0].stability).toBeUndefined();
		expect(stats[0].retrievability).toBeUndefined();
	});
});
