import '../../test-setup';
import { App, TFile } from 'obsidian';
import { StatsService } from '@application/services/StatsService';
import { AnkiCardStats } from '@domain/stats';
import { AretePluginSettings } from '@domain/settings';
import { AreteClient } from '@infrastructure/arete/AreteClient';

describe('StatsService', () => {
	let app: App;
	let settings: AretePluginSettings;
	let service: StatsService;
	let client: AreteClient;

	beforeEach(() => {
		app = new App();
		// Mock vault and metadata cache
		app.vault.getMarkdownFiles = jest.fn().mockReturnValue([]);
		app.metadataCache.getFileCache = jest.fn().mockReturnValue({});

		settings = {
			python_path: 'python3',
			backend: 'auto',
			anki_connect_url: 'http://localhost:8765',
			stats_algorithm: 'sm2',
			stats_lapse_threshold: 3,
			stats_ease_threshold: 2100,
			stats_difficulty_threshold: 0.9,
		} as any;

		client = new AreteClient(settings);
		service = new StatsService(app, settings, client);
	});

	test('refreshStats aggregates cards correctly by file and prioritizes YAML deck', async () => {
		// 1. Mock Vault Files
		const file1 = { path: 'concept1.md', basename: 'Concept 1' } as TFile;
		const file2 = { path: 'concept2.md', basename: 'Concept 2' } as TFile;
		(app.vault.getMarkdownFiles as jest.Mock).mockReturnValue([file1, file2]);

		// 2. Mock Metadata Cache (Cards in frontmatter)
		(app.metadataCache.getFileCache as jest.Mock).mockImplementation((f) => {
			if (f.path === 'concept1.md') {
				return {
					frontmatter: {
						deck: 'YAML Deck', // Should override Anki
						cards: [
							{ nid: '101', front: 'C1 Card 1' },
							{ nid: '102', front: 'C1 Card 2' },
						],
					},
				};
			}
			if (f.path === 'concept2.md') {
				return {
					frontmatter: {
						// No deck in YAML
						cards: [{ nid: '201', front: 'C2 Card 1' }],
					},
				};
			}
			return {};
		});

		// 3. Mock Anki Fetch (Mocking the network call)
		const mockAnkiStats: AnkiCardStats[] = [
			{
				noteId: 101,
				cardId: 1,
				lapses: 5,
				ease: 1500,
				interval: 1,
				due: 0,
				reps: 10,
				averageTime: 5000,
				deckName: 'Anki Deck A',
			},
			{
				noteId: 102,
				cardId: 2,
				lapses: 0,
				ease: 2500,
				interval: 10,
				due: 0,
				reps: 2,
				averageTime: 4000,
				deckName: 'Anki Deck A',
			},
			{
				noteId: 201,
				cardId: 3,
				lapses: 0,
				ease: 2300,
				interval: 5,
				due: 0,
				reps: 3,
				averageTime: 6000,
				deckName: 'Anki Deck B',
			},
		];

		service.fetchAnkiCardStats = jest.fn().mockResolvedValue(mockAnkiStats);

		// 4. Run Refresh
		const stats = await service.refreshStats();

		// 5. Verify Results
		expect(stats.length).toBe(2);

		// Concept 1 (Has YAML Deck)
		const c1 = stats.find((s) => s.filePath === 'concept1.md');
		expect(c1).toBeDefined();
		expect(c1?.primaryDeck).toBe('YAML Deck'); // Should be from YAML
		expect(c1?.totalCards).toBe(2);
		expect(c1?.problematicCardsCount).toBe(1);

		// Concept 2 (No YAML Deck)
		const c2 = stats.find((s) => s.filePath === 'concept2.md');
		expect(c2).toBeDefined();
		expect(c2?.primaryDeck).toBe('Anki Deck B'); // Should be from Anki
		expect(c2?.totalCards).toBe(1);
	});

	test('handles files with no linked cards gracefully', async () => {
		const file1 = { path: 'empty.md', basename: 'Empty' } as TFile;
		(app.vault.getMarkdownFiles as jest.Mock).mockReturnValue([file1]);
		(app.metadataCache.getFileCache as jest.Mock).mockReturnValue({});

		const stats = await service.refreshStats();
		expect(stats.length).toBe(0);
	});

	test('detects problematic cards with FSRS thresholds', async () => {
		service.settings.stats_algorithm = 'fsrs';
		service.settings.stats_difficulty_threshold = 0.7;

		const file = { path: 'test.md', basename: 'Test' } as TFile;
		(app.vault.getMarkdownFiles as jest.Mock).mockReturnValue([file]);
		(app.metadataCache.getFileCache as jest.Mock).mockReturnValue({
			frontmatter: { cards: [{ nid: '101' }] },
		});

		service.fetchAnkiCardStats = jest.fn().mockResolvedValue([
			{
				noteId: 101,
				cardId: 1,
				lapses: 0,
				ease: 2500,
				difficulty: 0.8,
				deckName: 'D1',
			},
		]);

		const stats = await service.refreshStats();
		expect(stats[0].problematicCardsCount).toBe(1);
		expect(stats[0].problematicCards[0].issue).toContain('Diff: 80%');
	});

	test('merges multiple cards per nid (FSRS)', async () => {
		service.settings.stats_algorithm = 'fsrs';
		const file = { path: 'test.md', basename: 'Test' } as TFile;
		(app.vault.getMarkdownFiles as jest.Mock).mockReturnValue([file]);
		(app.metadataCache.getFileCache as jest.Mock).mockReturnValue({
			frontmatter: { cards: [{ nid: '101', front: 'Q' }] },
		});

		// Two cards for same note, different difficulties
		service.fetchAnkiCardStats = jest.fn().mockResolvedValue([
			{
				noteId: 101,
				cardId: 1,
				lapses: 1,
				ease: 2500,
				difficulty: 0.3,
				stability: 10,
				retrievability: 0.9,
				deckName: 'D1',
			},
			{
				noteId: 101,
				cardId: 2,
				lapses: 1,
				ease: 2500,
				difficulty: 0.6,
				stability: 5,
				retrievability: 0.8,
				deckName: 'D1',
			},
		]);

		const stats = await service.refreshStats();
		const card = stats[0].cardStats[101];
		expect(card.difficulty).toBe(0.6); // Should prefer higher difficulty
	});

	test('aggregates hierarchical decks correctly', () => {
		const conceptStats: any[] = [
			{
				fileName: 'File A.md',
				filePath: 'a.md',
				primaryDeck: 'Sub::Level1',
				totalCards: 10,
				problematicCardsCount: 2,
				score: 0.2,
				sumDifficulty: 5,
				countDifficulty: 1,
				cardStats: {},
			},
			{
				fileName: 'File B.md',
				filePath: 'b.md',
				primaryDeck: 'Sub::Level2',
				totalCards: 10,
				problematicCardsCount: 5,
				score: 0.5,
				sumDifficulty: 8,
				countDifficulty: 1,
				cardStats: {},
			},
		];

		const root = service.getAggregatedStats(conceptStats);

		// Check Root (Vault)
		expect(root.count).toBe(20);
		expect(root.problematicCount).toBe(7);
		expect(root.score).toBe(0.35);

		// Check 'Sub' deck node
		const subNode = root.children.find((n) => n.title === 'Sub');
		expect(subNode).toBeDefined();
		expect(subNode?.count).toBe(20);
		expect(subNode?.difficulty).toBeCloseTo(6.5, 1);

		// Check stability/retrievability aggregation
		expect(root.stability).toBeNull(); // Not provided in mock conceptStats
	});

	test('aggregates stability and retrievability metrics', () => {
		const conceptStats: any[] = [
			{
				fileName: 'A.md',
				filePath: 'a.md',
				primaryDeck: 'D1',
				totalCards: 1,
				sumStability: 10,
				countStability: 1,
				sumRetrievability: 0.9,
				countRetrievability: 1,
				cardStats: {},
			},
		];
		const root = service.getAggregatedStats(conceptStats);
		expect(root.stability).toBe(10);
		expect(root.retrievability).toBe(0.9);
	});

	describe('fetchAnkiCardStats', () => {
		test('successfully fetches and maps stats', async () => {
			const mockData = [
				{
					card_id: 1,
					note_id: 101,
					lapses: 2,
					ease: 2500,
					difficulty: 5,
					deck_name: 'D1',
					interval: 10,
					due: 0,
					reps: 5,
					average_time_ms: 3000,
					stability: 15,
					current_retrievability: 0.85,
				},
			];
			service['client'].invoke = jest.fn().mockResolvedValue(mockData);

			const stats = await service.fetchAnkiCardStats([101]);
			expect(stats[0]).toEqual(
				expect.objectContaining({
					cardId: 1,
					noteId: 101,
					stability: 15,
					retrievability: 0.85,
					averageTime: 3000,
				}),
			);
		});

		test('handles unexpected response format', async () => {
			service['client'].invoke = jest.fn().mockResolvedValue({ error: 'not array' });
			const stats = await service.fetchAnkiCardStats([101]);
			expect(stats).toEqual([]);
		});

		test('handles client error', async () => {
			service['client'].invoke = jest.fn().mockRejectedValue(new Error('Network error'));
			const stats = await service.fetchAnkiCardStats([101]);
			expect(stats).toEqual([]);
		});
	});

	test('getCache returns current cache', () => {
		expect(service.getCache()).toBe(service.cache);
	});

	test('merges multiple cards per nid (SM-2)', async () => {
		service.settings.stats_algorithm = 'sm2';
		const file = { path: 'test.md', basename: 'Test' } as TFile;
		(app.vault.getMarkdownFiles as jest.Mock).mockReturnValue([file]);
		(app.metadataCache.getFileCache as jest.Mock).mockReturnValue({
			frontmatter: { cards: [{ nid: '101' }] },
		});

		service.fetchAnkiCardStats = jest.fn().mockResolvedValue([
			{ noteId: 101, cardId: 1, lapses: 1, ease: 2500, deckName: 'D1' },
			{ noteId: 101, cardId: 2, lapses: 1, ease: 2100, deckName: 'D1' },
		]);

		const stats = await service.refreshStats();
		expect(stats[0].cardStats[101].ease).toBe(2100);
	});

	test('merges multiple cards per nid (Lapses Fallback)', async () => {
		service.settings.stats_algorithm = 'sm2';
		const file = { path: 'test.md', basename: 'Test' } as TFile;
		(app.vault.getMarkdownFiles as jest.Mock).mockReturnValue([file]);
		(app.metadataCache.getFileCache as jest.Mock).mockReturnValue({
			frontmatter: { cards: [{ nid: '101' }] },
		});

		service.fetchAnkiCardStats = jest.fn().mockResolvedValue([
			{ noteId: 101, cardId: 1, lapses: 5, ease: 2500, deckName: 'D1' },
			{ noteId: 101, cardId: 2, lapses: 2, ease: 2500, deckName: 'D1' },
		]);

		const stats = await service.refreshStats();
		expect(stats[0].cardStats[101].lapses).toBe(5);
	});
});
