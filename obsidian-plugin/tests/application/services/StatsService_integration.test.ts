import '../../test-setup';
import { App } from 'obsidian';
import { StatsService } from '@application/services/StatsService';
import { AretePluginSettings } from '@domain/settings';
import { AreteClient } from '@infrastructure/arete/AreteClient';

// Mock obsidian
jest.mock('obsidian', () => ({
	App: class {},
	TFile: class {},
	Notice: jest.fn(),
	requestUrl: jest.fn(),
	FileSystemAdapter: class {},
}));

// Mock AreteClient
jest.mock('@infrastructure/arete/AreteClient');

describe('StatsService Integration', () => {
	let app: App;
	let settings: AretePluginSettings;
	let service: StatsService;
	let mockClient: jest.Mocked<AreteClient>;

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

		mockClient = new AreteClient(settings) as jest.Mocked<AreteClient>;
		service = new StatsService(app, settings, mockClient);
		mockClient.invoke.mockClear();
	});

	test('fetchAnkiCardStats calls getFSRSStats and merges difficulty', async () => {
		const nids = [101, 102];

		// Mock the unified /anki/stats endpoint
		// The backend returns snake_case
		mockClient.invoke.mockResolvedValue([
			{
				card_id: 1,
				note_id: 101,
				lapses: 0,
				ease: 250,
				difficulty: 0.85,
				deck_name: 'Deck A',
				interval: 10,
				due: 0,
				reps: 5,
				average_time: 1000,
				front: 'Front 1',
			},
			{
				card_id: 2,
				note_id: 102,
				lapses: 0,
				ease: 250,
				difficulty: 0.3,
				deck_name: 'Deck A',
				interval: 10,
				due: 0,
				reps: 5,
				average_time: 1000,
				front: 'Front 2',
			},
		]);

		const stats = await service.fetchAnkiCardStats(nids);

		expect(stats.length).toBe(2);

		// Verify Card 1 (Mapped correctly to camelCase)
		expect(stats[0].cardId).toBe(1);
		expect(stats[0].noteId).toBe(101);
		expect(stats[0].difficulty).toBe(0.85);

		// Verify Card 2
		expect(stats[1].cardId).toBe(2);
		expect(stats[1].difficulty).toBe(0.3);

		// Verify client call
		expect(mockClient.invoke).toHaveBeenCalledWith('/anki/stats', { nids });
	});

	test('fetchAnkiCardStats handles missing FSRS data gracefully', async () => {
		const nids = [101];

		mockClient.invoke.mockResolvedValue([
			{
				card_id: 1,
				note_id: 101,
				lapses: 0,
				ease: 250,
				difficulty: 0.5,
				deck_name: 'Deck A',
				interval: 10,
				due: 0,
				reps: 5,
				average_time: 1000,
				front: 'Front 1',
			},
		]);

		const stats = await service.fetchAnkiCardStats(nids);

		expect(stats.length).toBe(1);
		expect(stats[0].difficulty).toBe(0.5);
	});
});
