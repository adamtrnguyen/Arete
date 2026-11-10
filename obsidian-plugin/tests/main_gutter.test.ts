import './test-setup';
import { App } from 'obsidian';
import AretePlugin from '@/main';
import { createCardGutter } from '@presentation/extensions/CardGutterExtension';

jest.mock('@infrastructure/arete/SyncService');
jest.mock('@infrastructure/arete/CheckService');
jest.mock('@application/services/StatsService', () => ({
	StatsService: jest.fn().mockImplementation(() => ({
		refreshStats: jest.fn().mockResolvedValue([]),
		getCache: jest.fn(),
		settings: {},
	})),
}));
jest.mock('@presentation/extensions/CardGutterExtension', () => ({
	createCardGutter: jest.fn().mockReturnValue({ gutter: true }),
	highlightCardEffect: { of: jest.fn() },
}));

describe('AretePlugin: what a click in the card gutter does', () => {
	let plugin: AretePlugin;
	let app: any;
	let onCardClick: (index: number) => void;
	let statsFor: (nid: number | null, cid: number | null, view: any) => unknown;
	let registered: unknown[];

	beforeEach(async () => {
		jest.clearAllMocks();
		app = new App();
		app.workspace.trigger = jest.fn();
		plugin = new AretePlugin(app, { dir: 'd' } as any);
		plugin.loadData = jest.fn().mockResolvedValue({ stats_algorithm: 'fsrs' });
		registered = [];
		plugin.registerEditorExtension = jest.fn((e: unknown) => {
			registered.push(e);
		}) as any;

		await plugin.onload();

		[onCardClick, statsFor] = (createCardGutter as jest.Mock).mock.calls[0];
	});

	it('registers the gutter with the configured stats algorithm', () => {
		expect((createCardGutter as jest.Mock).mock.calls[0][2]).toBe('fsrs');
		expect(registered).toContainEqual({ gutter: true });
	});

	describe('clicking a card marker', () => {
		beforeEach(() => {
			plugin.highlightCardLines = jest.fn();
			plugin.syncYamlEditorToCard = jest.fn();
			app.workspace.getActiveFile.mockReturnValue({ path: 'N.md' });
			app.metadataCache.getFileCache.mockReturnValue({
				frontmatter: { cards: [{ id: 'arete_A' }, { id: 'arete_B' }, {}] },
			});
		});

		it('highlights the card, focuses it in the editor, and announces it to the graph', () => {
			onCardClick(1);

			expect(plugin.highlightCardLines).toHaveBeenCalledWith(1);
			expect(plugin.syncYamlEditorToCard).toHaveBeenCalledWith(1);
			expect(plugin.getCardContext('N.md')).toBe('arete_B');
			expect(app.workspace.trigger).toHaveBeenCalledWith('arete:card-selected', 'arete_B');
		});

		it('a card with no id is highlighted but not announced', () => {
			onCardClick(2);

			expect(plugin.highlightCardLines).toHaveBeenCalledWith(2);
			expect(plugin.getCardContext('N.md')).toBeUndefined();
			expect(app.workspace.trigger).not.toHaveBeenCalled();
		});

		it('an index past the last card is not announced', () => {
			onCardClick(9);
			expect(app.workspace.trigger).not.toHaveBeenCalled();
		});

		it('with no active note it still highlights and focuses', () => {
			app.workspace.getActiveFile.mockReturnValue(null);
			onCardClick(0);
			expect(plugin.highlightCardLines).toHaveBeenCalledWith(0);
			expect(plugin.syncYamlEditorToCard).toHaveBeenCalledWith(0);
			expect(app.workspace.trigger).not.toHaveBeenCalled();
		});
	});

	describe('looking up the stats shown beside a marker', () => {
		const viewFor = (file: { path: string } | undefined) => ({
			state: { field: jest.fn(() => ({ file })) },
		});
		const cardStats = { 500: { cardId: 500 }, 700: { noteId: 700 } };

		beforeEach(() => {
			(plugin.statsService.getCache as jest.Mock).mockReturnValue({
				concepts: { 'N.md': { cardStats }, 'Empty.md': {} },
			});
		});

		it('prefers the card id and falls back to the note id', () => {
			const view = viewFor({ path: 'N.md' });
			expect(statsFor(1, 500, view)).toBe(cardStats[500]);
			expect(statsFor(700, null, view)).toBe(cardStats[700]);
			expect(statsFor(700, 999, view)).toBe(cardStats[700]);
		});

		it('returns nothing for an unknown card, an unknown note, or a note with no stats', () => {
			expect(statsFor(1, 2, viewFor({ path: 'N.md' }))).toBeNull();
			expect(statsFor(null, null, viewFor({ path: 'N.md' }))).toBeNull();
			expect(statsFor(1, 500, viewFor({ path: 'Other.md' }))).toBeNull();
			expect(statsFor(1, 500, viewFor({ path: 'Empty.md' }))).toBeNull();
		});

		it('returns nothing when the editor has no file', () => {
			expect(statsFor(1, 500, viewFor(undefined))).toBeNull();
		});
	});
});
