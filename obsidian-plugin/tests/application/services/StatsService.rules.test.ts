import '../../test-setup';
import { App, TFile } from 'obsidian';
import { StatsService } from '@application/services/StatsService';

const stat = (o: Record<string, any>) => ({
	noteId: 1,
	cardId: 1,
	lapses: 0,
	ease: 2500,
	deckName: 'D',
	...o,
});

describe('StatsService.refreshStats: which cards count and when they are flagged', () => {
	let app: any;
	let settings: any;
	let source: { getCardStats: jest.Mock };
	let service: StatsService;
	let files: Record<string, any>;

	beforeEach(() => {
		jest.spyOn(console, 'warn').mockImplementation(() => {});
		app = new App();
		files = {};
		app.vault.getMarkdownFiles = jest.fn(() =>
			Object.keys(files).map((p) => ({ path: p, name: p }) as TFile),
		);
		app.metadataCache.getFileCache = jest.fn((f: { path: string }) => files[f.path]);
		settings = {
			stats_algorithm: 'sm2',
			stats_lapse_threshold: 3,
			stats_ease_threshold: 2100,
			stats_difficulty_threshold: 0.9,
		};
		source = { getCardStats: jest.fn().mockResolvedValue([]) };
		service = new StatsService(app, settings, source as any);
	});
	afterEach(() => jest.restoreAllMocks());

	const note = (cards: any[], extra: Record<string, any> = {}) => ({
		frontmatter: { arete: true, cards, ...extra },
	});
	const reply = (rows: Array<Record<string, any>>) => {
		// the client returns the Python snake_case shape; fetchAnkiCardStats maps it
		source.getCardStats.mockResolvedValue(
			rows.map((r) => ({
				card_id: r.cardId,
				note_id: r.noteId,
				lapses: r.lapses ?? 0,
				ease: r.ease ?? 2500,
				difficulty: r.difficulty,
				deck_name: r.deckName ?? 'D',
				stability: r.stability,
				current_retrievability: r.retrievability,
			})),
		);
	};

	it('asks Anki only for notes linked from arete: true files, skipping bad entries', async () => {
		files['a.md'] = note([
			{ anki: { nid: '101' }, Front: 'q' },
			{ anki: { nid: 'abc' }, Front: 'bad nid' },
			{ Front: 'never synced' },
			null,
			{ anki: { nid: 102 }, Text: 'cloze' },
		]);
		files['not-arete.md'] = {
			frontmatter: { arete: false, cards: [{ anki: { nid: '999' } }] },
		};
		files['no-cards.md'] = { frontmatter: { arete: true } };
		files['empty.md'] = note([]);
		files['plain.md'] = {};

		await service.refreshStats();

		expect(source.getCardStats).toHaveBeenCalledTimes(1);
		expect(source.getCardStats).toHaveBeenCalledWith([101, 102]);
	});

	it('does not call Anki at all when no card is linked', async () => {
		files['a.md'] = note([{ Front: 'never synced' }]);
		await expect(service.refreshStats()).resolves.toEqual([]);
		expect(source.getCardStats).not.toHaveBeenCalled();
	});

	it('ignores stats for notes the vault does not list', async () => {
		files['a.md'] = note([{ anki: { nid: '1' }, Front: 'q' }]);
		reply([
			{ noteId: 1, cardId: 10 },
			{ noteId: 77, cardId: 11 },
		]);

		const [concept] = await service.refreshStats();

		expect(concept.totalCards).toBe(1);
		expect(Object.keys(concept.cardStats).sort()).toEqual(['1', '10']);
	});

	describe('SM-2 flags', () => {
		beforeEach(() => {
			files['a.md'] = note(
				[1, 2, 3, 4].map((n) => ({
					anki: { nid: String(n) },
					Front: `q${n}`,
					Back: `a${n}`,
				})),
			);
		});

		it('flags lapses at the threshold and ease below it, and says why', async () => {
			reply([
				{ noteId: 1, cardId: 1, lapses: 2, ease: 2500 }, // fine
				{ noteId: 2, cardId: 2, lapses: 3, ease: 2500 }, // lapses == threshold
				{ noteId: 3, cardId: 3, lapses: 0, ease: 2000 }, // ease below
				{ noteId: 4, cardId: 4, lapses: 5, ease: 1300 }, // both
			]);

			const [c] = await service.refreshStats();

			expect(c.problematicCards.map((p) => [p.noteId, p.issue])).toEqual([
				[2, 'Lapses: 3'],
				[3, 'Ease: 200%'],
				[4, 'Lapses: 5, Ease: 130%'],
			]);
			expect(c.problematicCardsCount).toBe(3);
			expect(c.score).toBe(3 / 4);
		});

		it('ease exactly at the threshold is not flagged', async () => {
			reply([{ noteId: 1, cardId: 1, ease: 2100 }]);
			const [c] = await service.refreshStats();
			expect(c.problematicCardsCount).toBe(0);
		});

		it('carries the card front and back into the problem entry', async () => {
			reply([{ noteId: 3, cardId: 3, lapses: 9 }]);
			const [c] = await service.refreshStats();
			expect(c.problematicCards[0]).toMatchObject({
				front: 'q3',
				back: 'a3',
				cardId: 3,
				deckName: 'D',
			});
		});

		it('sums lapses and rounds the mean ease', async () => {
			reply([
				{ noteId: 1, cardId: 1, lapses: 1, ease: 2500 },
				{ noteId: 2, cardId: 2, lapses: 2, ease: 2301 },
				{ noteId: 3, cardId: 3, lapses: 0, ease: 2300 },
			]);
			const [c] = await service.refreshStats();
			expect(c.totalLapses).toBe(3);
			expect(c.averageEase).toBe(Math.round((2500 + 2301 + 2300) / 3));
			expect(c.averageDifficulty).toBeNull();
		});
	});

	describe('FSRS flags', () => {
		beforeEach(() => {
			settings.stats_algorithm = 'fsrs';
			files['a.md'] = note([1, 2].map((n) => ({ anki: { nid: String(n) }, Front: `q${n}` })));
		});

		it('flags difficulty above the threshold, not at it, and ignores ease', async () => {
			reply([
				{ noteId: 1, cardId: 1, difficulty: 0.9, ease: 100 },
				{ noteId: 2, cardId: 2, difficulty: 0.91, ease: 100 },
			]);
			const [c] = await service.refreshStats();
			expect(c.problematicCards.map((p) => [p.noteId, p.issue])).toEqual([[2, 'Diff: 91%']]);
		});

		it('averages difficulty to two decimals over the cards that have one', async () => {
			reply([
				{ noteId: 1, cardId: 1, difficulty: 0.5, stability: 10, retrievability: 0.9 },
				{ noteId: 2, cardId: 2, difficulty: 0.3333, stability: 20, retrievability: 0.7 },
			]);
			const [c] = await service.refreshStats();
			expect(c.averageDifficulty).toBe(0.42);
			expect(c.sumStability).toBe(30);
			expect(c.countStability).toBe(2);
			expect(c.sumRetrievability).toBeCloseTo(1.6);
		});

		it('a card without difficulty is not counted in the difficulty mean', async () => {
			reply([
				{ noteId: 1, cardId: 1, difficulty: 0.6 },
				{ noteId: 2, cardId: 2 },
			]);
			const [c] = await service.refreshStats();
			expect(c.averageDifficulty).toBe(0.6);
			expect(c.countDifficulty).toBe(1);
			expect(c.totalCards).toBe(2);
		});
	});

	describe('several cards of one note', () => {
		beforeEach(() => {
			files['a.md'] = note([{ anki: { nid: '1' }, Front: 'q' }]);
		});
		const merged = async () => {
			const [c] = await service.refreshStats();
			return c.cardStats[1];
		};

		it('FSRS keeps the card with the higher difficulty', async () => {
			settings.stats_algorithm = 'fsrs';
			reply([
				{ noteId: 1, cardId: 10, difficulty: 0.7 },
				{ noteId: 1, cardId: 11, difficulty: 0.4 },
			]);
			expect((await merged()).cardId).toBe(10);
		});

		it('FSRS prefers a card that has a difficulty over one that has none', async () => {
			settings.stats_algorithm = 'fsrs';
			reply([
				{ noteId: 1, cardId: 10 },
				{ noteId: 1, cardId: 11, difficulty: 0.2 },
			]);
			expect((await merged()).cardId).toBe(11);
		});

		it('FSRS breaks a difficulty tie by lapses', async () => {
			settings.stats_algorithm = 'fsrs';
			reply([
				{ noteId: 1, cardId: 10, difficulty: 0.5, lapses: 1 },
				{ noteId: 1, cardId: 11, difficulty: 0.5, lapses: 4 },
			]);
			expect((await merged()).cardId).toBe(11);
		});

		it('FSRS does not let more lapses override a clearly lower difficulty', async () => {
			settings.stats_algorithm = 'fsrs';
			reply([
				{ noteId: 1, cardId: 10, difficulty: 0.8, lapses: 0 },
				{ noteId: 1, cardId: 11, difficulty: 0.3, lapses: 9 },
			]);
			expect((await merged()).cardId).toBe(10);
		});

		it('SM-2 keeps the lower ease, and breaks an ease tie by lapses', async () => {
			reply([
				{ noteId: 1, cardId: 10, ease: 2500 },
				{ noteId: 1, cardId: 11, ease: 1800 },
			]);
			expect((await merged()).cardId).toBe(11);

			reply([
				{ noteId: 1, cardId: 20, ease: 2000, lapses: 0 },
				{ noteId: 1, cardId: 21, ease: 2000, lapses: 3 },
			]);
			expect((await merged()).cardId).toBe(21);
		});

		it('SM-2 does not let more lapses override a lower ease', async () => {
			reply([
				{ noteId: 1, cardId: 10, ease: 1800, lapses: 0 },
				{ noteId: 1, cardId: 11, ease: 2400, lapses: 9 },
			]);
			expect((await merged()).cardId).toBe(10);
		});

		it('every card is still stored under its own card id and counted', async () => {
			reply([
				{ noteId: 1, cardId: 10 },
				{ noteId: 1, cardId: 11 },
			]);
			const [c] = await service.refreshStats();
			expect(c.cardStats[10].cardId).toBe(10);
			expect(c.cardStats[11].cardId).toBe(11);
			expect(c.totalCards).toBe(2);
		});
	});

	describe('deck and ordering', () => {
		it('with no deck in the note, the deck holding most of its cards wins', async () => {
			files['a.md'] = note([1, 2, 3].map((n) => ({ anki: { nid: String(n) }, Front: 'q' })));
			reply([
				{ noteId: 1, cardId: 1, deckName: 'Small' },
				{ noteId: 2, cardId: 2, deckName: 'Big' },
				{ noteId: 3, cardId: 3, deckName: 'Big' },
			]);
			const [c] = await service.refreshStats();
			expect(c.primaryDeck).toBe('Big');
		});

		it('a deck written in the note beats the decks Anki reports', async () => {
			files['a.md'] = note([{ anki: { nid: '1' }, Front: 'q' }], { deck: 'Mine' });
			reply([{ noteId: 1, cardId: 1, deckName: 'Other' }]);
			const [c] = await service.refreshStats();
			expect(c.primaryDeck).toBe('Mine');
		});

		it('a note whose cards Anki did not return keeps deck Unknown and zero counts', async () => {
			files['a.md'] = note([{ anki: { nid: '1' }, Front: 'q' }]);
			files['b.md'] = note([{ anki: { nid: '2' }, Front: 'q' }]);
			reply([{ noteId: 2, cardId: 2 }]);

			const results = await service.refreshStats();
			const a = results.find((r) => r.filePath === 'a.md')!;

			expect(a.totalCards).toBe(0);
			expect(a.primaryDeck).toBe('Unknown');
			expect(a.score).toBe(0);
		});

		it('returns the most problematic notes first and stores them in the cache', async () => {
			files['ok.md'] = note([{ anki: { nid: '1' }, Front: 'q' }]);
			files['bad.md'] = note([{ anki: { nid: '2' }, Front: 'q' }]);
			reply([
				{ noteId: 1, cardId: 1, lapses: 0 },
				{ noteId: 2, cardId: 2, lapses: 8 },
			]);
			jest.spyOn(Date, 'now').mockReturnValue(1234);

			const results = await service.refreshStats();

			expect(results.map((r) => r.filePath)).toEqual(['bad.md', 'ok.md']);
			expect(Object.keys(service.getCache().concepts).sort()).toEqual(['bad.md', 'ok.md']);
			expect(service.getCache().lastFetched).toBe(1234);
		});
	});

	describe('aggregation tree', () => {
		it('rolls child counts up a deck hierarchy and weights means by card count', async () => {
			files['a.md'] = note(
				[1, 2, 3].map((n) => ({ anki: { nid: String(n) }, Front: 'q' })),
				{
					deck: 'Math::Algebra',
				},
			);
			files['b.md'] = note([{ anki: { nid: '4' }, Front: 'q' }], { deck: 'Math::Calculus' });
			settings.stats_algorithm = 'fsrs';
			reply([
				{ noteId: 1, cardId: 1, difficulty: 0.2, lapses: 1 },
				{ noteId: 2, cardId: 2, difficulty: 0.2, lapses: 1 },
				{ noteId: 3, cardId: 3, difficulty: 0.2, lapses: 1 },
				{ noteId: 4, cardId: 4, difficulty: 1.0, lapses: 5 },
			]);

			const root = service.getAggregatedStats(await service.refreshStats());
			const math = root.children[0];

			expect(root.count).toBe(4);
			expect(math.deckName).toBe('Math');
			expect(math.count).toBe(4);
			expect(math.lapses).toBe(8);
			expect(math.difficulty).toBeCloseTo((0.2 * 3 + 1.0 * 1) / 4);
			expect(math.children.map((c) => c.deckName).sort()).toEqual([
				'Math::Algebra',
				'Math::Calculus',
			]);
		});

		it('counts cards by state, with no-data cards apart, and the counts add up', async () => {
			files['a.md'] = note(
				[1, 2, 3, 4].map((n) => ({ anki: { nid: String(n) }, Front: 'q' })),
				{ deck: 'Math::Algebra' },
			);
			files['b.md'] = note([{ anki: { nid: '5' }, Front: 'q' }], { deck: 'Math::Calculus' });
			settings.stats_algorithm = 'fsrs';
			reply([
				{ noteId: 1, cardId: 1, difficulty: 0.2, stability: 30, retrievability: 0.95 },
				{ noteId: 2, cardId: 2, difficulty: 0.8 },
				{ noteId: 3, cardId: 3, lapses: 5 },
				{ noteId: 4, cardId: 4 },
				{ noteId: 5, cardId: 5 },
			]);

			const root = service.getAggregatedStats(await service.refreshStats());
			const math = root.children[0];
			const algebra = math.children.find((c) => c.deckName === 'Math::Algebra')!;
			const calculus = math.children.find((c) => c.deckName === 'Math::Calculus')!;

			expect(algebra.stateCounts).toEqual({ success: 1, warning: 1, error: 1, nodata: 1 });
			expect(calculus.stateCounts).toEqual({ success: 0, warning: 0, error: 0, nodata: 1 });
			expect(root.stateCounts).toEqual({ success: 1, warning: 1, error: 1, nodata: 2 });
			for (const node of [root, math, algebra, calculus]) {
				const { success, warning, error, nodata } = node.stateCounts;
				expect(success + warning + error + nodata).toBe(node.count);
			}
		});

		it('sorts children by problem score, worst first, and files unknown decks under Default', async () => {
			files['calm.md'] = note([{ anki: { nid: '1' }, Front: 'q' }]);
			files['hot.md'] = note([{ anki: { nid: '2' }, Front: 'q' }]);
			reply([
				{ noteId: 1, cardId: 1, lapses: 0, deckName: 'D' },
				{ noteId: 2, cardId: 2, lapses: 9, deckName: 'D' },
			]);
			const concepts = await service.refreshStats();
			concepts.forEach((c) => (c.primaryDeck = 'Unknown'));

			const root = service.getAggregatedStats(concepts);

			expect(root.children).toHaveLength(1);
			expect(root.children[0].deckName).toBe('Default');
			expect(root.children[0].children.map((c) => c.title)).toEqual(['hot', 'calm']);
			expect(root.children[0].score).toBe(0.5);
		});
	});
});
