import { MarkdownView, Notice } from 'obsidian';
import { CardYamlEditorView } from '@/presentation/views/CardYamlEditorView';
import {
	extractCardFields,
	parseCardYaml,
} from '@/presentation/views/yamlEditor/cardEditorHelpers';
import { CardParserService } from '@/application/services/CardParserService';
import { cardRangesField } from '@/presentation/extensions/CardGutterExtension';
import { createMockElement } from '../../test-setup';

jest.mock('@/presentation/renderers/CardPreviewFrame');
jest.mock('@/presentation/renderers/CardRenderer');

type Card = Record<string, any>;

/**
 * A note whose frontmatter lives in `fm`. `processFrontMatter` runs the view's callback on
 * it, and the metadata cache serves the same object back, as Obsidian does after a re-parse.
 */
function makeView(initialCards: Card[], plugin: any = {}) {
	const app: any = new (jest.requireMock('obsidian').App)();
	const note = { path: 'Note.md', stat: { mtime: 1000 } };
	const fm: any = { cards: initialCards };
	app.workspace.getActiveFile.mockReturnValue(note);
	app.metadataCache.getFileCache.mockImplementation(() => ({ frontmatter: fm }));
	app.fileManager.processFrontMatter.mockImplementation(async (_f: any, fn: any) => {
		fn(fm);
		note.stat.mtime += 1;
	});
	const host = { highlightCardLines: jest.fn(), ...plugin };
	const view: any = new CardYamlEditorView({} as any, host);
	view.app = app;
	view.renderIndex = jest.fn();
	view.refreshActiveView = jest.fn();
	return { view, app, fm, note, host };
}

const cards = () => [
	{ id: 'a', Front: 'A', Back: 'a' },
	{ id: 'b', Front: 'B', Back: 'b' },
	{ id: 'c', Front: 'C', Back: 'c' },
];
const order = (fm: any) => fm.cards.map((c: Card) => c.id);

describe('CardYamlEditorView: editing the card list in frontmatter', () => {
	beforeEach(() => {
		jest.clearAllMocks();
	});

	describe('addCard', () => {
		it('inserts a blank Basic card after the current one and selects it', async () => {
			const { view, fm, note } = makeView(cards());
			await view.loadCards();
			view.currentCardIndex = 1;

			await view.addCard();

			expect(fm.cards).toHaveLength(4);
			expect(fm.cards[2]).toEqual({ model: 'Basic', Front: '', Back: '' });
			expect(order({ cards: [fm.cards[0], fm.cards[1], fm.cards[3]] })).toEqual([
				'a',
				'b',
				'c',
			]);
			expect(view.currentCardIndex).toBe(2);
			expect(view.cards).toEqual(fm.cards);
			expect(view.cards).not.toBe(fm.cards);
			expect(view.ownWrite).toEqual({ path: note.path, mtime: note.stat.mtime });
		});

		it('creates the cards list when the note has none', async () => {
			const { view, fm } = makeView([]);
			delete fm.cards;
			await view.loadCards();

			await view.addCard();

			expect(fm.cards).toEqual([{ model: 'Basic', Front: '', Back: '' }]);
		});

		it('does nothing without an active note', async () => {
			const { view, app } = makeView(cards());
			app.workspace.getActiveFile.mockReturnValue(null);
			await view.addCard();
			expect(app.fileManager.processFrontMatter).not.toHaveBeenCalled();
		});
	});

	describe('deleteCard', () => {
		it('removes exactly the chosen card', async () => {
			const { view, fm } = makeView(cards());
			await view.loadCards();
			await view.deleteCard(1);
			expect(order(fm)).toEqual(['a', 'c']);
		});

		it('moves the selection back onto the list when the last card is deleted', async () => {
			const { view, fm } = makeView(cards());
			await view.loadCards();
			view.currentCardIndex = 2;
			await view.deleteCard(2);
			expect(order(fm)).toEqual(['a', 'b']);
			expect(view.currentCardIndex).toBe(1);
		});

		it('keeps the selection at 0 when the only card is deleted', async () => {
			const { view, fm } = makeView([{ id: 'a' }]);
			await view.loadCards();
			await view.deleteCard(0);
			expect(fm.cards).toEqual([]);
			expect(view.currentCardIndex).toBe(0);
		});
	});

	describe('reorderCards', () => {
		it.each([
			// from, to, selected before, expected order, selected after
			[0, 2, 0, ['b', 'c', 'a'], 2],
			[0, 2, 1, ['b', 'c', 'a'], 0],
			[2, 0, 1, ['c', 'a', 'b'], 2],
			[2, 0, 2, ['c', 'a', 'b'], 0],
			[0, 1, 2, ['b', 'a', 'c'], 2],
		])(
			'moves %i to %i with card %i selected',
			async (from, to, selected, expectedOrder, expectedSelected) => {
				const { view, fm } = makeView(cards());
				await view.loadCards();
				view.currentCardIndex = selected;

				await view.reorderCards(from, to);

				expect(order(fm)).toEqual(expectedOrder);
				// The selection follows the same card, whichever way the others shift.
				expect(view.currentCardIndex).toBe(expectedSelected);
				expect(fm.cards[view.currentCardIndex].id).toBe(['a', 'b', 'c'][selected]);
			},
		);

		it('does nothing without an active note', async () => {
			const { view, app } = makeView(cards());
			app.workspace.getActiveFile.mockReturnValue(null);
			await view.reorderCards(0, 1);
			expect(app.fileManager.processFrontMatter).not.toHaveBeenCalled();
		});
	});

	describe('drag and drop', () => {
		const drop = (data: string) => ({
			preventDefault: jest.fn(),
			dataTransfer: { getData: jest.fn().mockReturnValue(data) },
		});

		it('dropping card 0 on position 2 reorders the frontmatter', async () => {
			const { view, fm } = makeView(cards());
			await view.loadCards();
			await view.handleDrop(drop('0'), 2);
			expect(order(fm)).toEqual(['b', 'c', 'a']);
		});

		it('dropping a card on itself writes nothing', async () => {
			const { view, app } = makeView(cards());
			await view.loadCards();
			await view.handleDrop(drop('1'), 1);
			expect(app.fileManager.processFrontMatter).not.toHaveBeenCalled();
		});

		it('dropping something that is not a card index writes nothing', async () => {
			const { view, app } = makeView(cards());
			await view.loadCards();
			await view.handleDrop(drop('not-a-number'), 1);
			expect(app.fileManager.processFrontMatter).not.toHaveBeenCalled();
		});

		it('drag start records the index and marks the element', () => {
			const { view } = makeView(cards());
			const target = createMockElement('div');
			const e: any = { dataTransfer: { setData: jest.fn() }, target };
			view.handleDragStart(e, 2);
			expect(e.dataTransfer.setData).toHaveBeenCalledWith('text/plain', '2');
			expect(e.dataTransfer.effectAllowed).toBe('move');
			expect(target.addClass).toHaveBeenCalledWith('is-dragging');
		});

		it('drag over marks the nearest index item as a drop target', () => {
			const { view } = makeView(cards());
			const item = createMockElement('div');
			const e: any = {
				preventDefault: jest.fn(),
				dataTransfer: {},
				target: { closest: jest.fn().mockReturnValue(item) },
			};
			view.handleDragOver(e);
			expect(e.preventDefault).toHaveBeenCalled();
			expect(e.dataTransfer.dropEffect).toBe('move');
			expect(e.target.closest).toHaveBeenCalledWith('.arete-yaml-index-item');
			expect(item.addClass).toHaveBeenCalledWith('is-drag-over');
		});

		it('drag end clears both markers from every item', () => {
			const { view } = makeView(cards());
			const items = [createMockElement('div'), createMockElement('div')];
			view.indexContainer = { querySelectorAll: jest.fn().mockReturnValue(items) };
			view.handleDragEnd();
			for (const item of items) {
				expect(item.removeClass).toHaveBeenCalledWith('is-dragging');
				expect(item.removeClass).toHaveBeenCalledWith('is-drag-over');
			}
		});
	});

	describe('context menu', () => {
		it('offers Delete card, which deletes the right-clicked card', () => {
			const items: any[] = [];
			const Menu = jest.fn().mockImplementation(() => ({
				addItem: (cb: (i: any) => void) => {
					const item: any = {
						setTitle: jest.fn().mockReturnThis(),
						setIcon: jest.fn().mockReturnThis(),
						setWarning: jest.fn().mockReturnThis(),
						onClick: jest.fn().mockImplementation((fn: () => void) => {
							item.click = fn;
							return item;
						}),
					};
					items.push(item);
					cb(item);
				},
				showAtMouseEvent: jest.fn(),
			}));
			const obsidian = jest.requireMock('obsidian');
			const previous = obsidian.Menu;
			obsidian.Menu = Menu;
			try {
				const { view } = makeView(cards());
				view.deleteCard = jest.fn();
				view.showCardContextMenu({} as MouseEvent, 2);

				expect(items).toHaveLength(1);
				expect(items[0].setTitle).toHaveBeenCalledWith('Delete card');
				expect(items[0].setWarning).toHaveBeenCalledWith(true);
				items[0].click();
				expect(view.deleteCard).toHaveBeenCalledWith(2);
			} finally {
				obsidian.Menu = previous;
			}
		});
	});
});

describe('CardYamlEditorView: loading cards', () => {
	it('defaults the model to Basic and the card list to empty', async () => {
		const { view, app } = makeView([]);
		app.metadataCache.getFileCache.mockReturnValue({ frontmatter: {} });
		await view.loadCards();
		expect(view.fileModel).toBe('Basic');
		expect(view.cards).toEqual([]);
	});

	it('ignores a cards value that is not a list', async () => {
		const { view, app } = makeView([]);
		app.metadataCache.getFileCache.mockReturnValue({ frontmatter: { cards: 'oops' } });
		await view.loadCards();
		expect(view.cards).toEqual([]);
	});

	it('resets the selection when the note changes, keeps it for the same note', async () => {
		const { view, app } = makeView(cards());
		await view.loadCards();
		view.currentCardIndex = 2;
		await view.loadCards();
		expect(view.currentCardIndex).toBe(2);

		app.workspace.getActiveFile.mockReturnValue({ path: 'Other.md' });
		await view.loadCards();
		expect(view.currentCardIndex).toBe(0);
		expect(view.currentFilePath).toBe('Other.md');
	});

	it('clamps a selection that is past the end of a shortened list', async () => {
		const { view, fm } = makeView(cards());
		await view.loadCards();
		view.currentCardIndex = 2;
		fm.cards = [fm.cards[0]];
		await view.loadCards();
		expect(view.currentCardIndex).toBe(0);
	});

	it('clears the list when no note is active', async () => {
		const { view, app } = makeView(cards());
		await view.loadCards();
		app.workspace.getActiveFile.mockReturnValue(null);
		await view.loadCards();
		expect(view.cards).toEqual([]);
		expect(view.currentFilePath).toBeNull();
	});
});

describe('CardYamlEditorView: card to YAML', () => {
	const obsidian = jest.requireMock('obsidian');
	const yamlOf = (card: Card) => {
		const { view } = makeView([card]);
		view.cards = [card];
		return view.extractCardYaml(0);
	};

	it('says so when the index is out of range', () => {
		const { view } = makeView(cards());
		expect(view.extractCardYaml(-1)).toBe('# No card selected\n');
		expect(view.extractCardYaml(3)).toBe('# No card selected\n');
	});

	it('round-trips arbitrary structured cards losslessly via stringifyYaml', () => {
		const complex = {
			id: 'arete_1',
			model: 'Basic',
			deck: 'Math::Calc',
			tags: ['exam', 'important'],
			markdown: false,
			Front: '  indented\n---\n"quoted": value $x^2$',
			Back: 'one\ntwo\n',
			Source: 'a: b',
			count: 3,
			deps: { requires: ['x', 'y'], related: ['z'] },
			anki: { nid: 11, cid: 22 },
		};
		const dumped = yamlOf(complex);
		expect(obsidian.parseYaml(dumped)).toEqual(complex);
	});
});

describe('CardYamlEditorView: YAML back to a card', () => {
	let obsidian: any;
	let previous: any;
	beforeEach(() => {
		obsidian = jest.requireMock('obsidian');
		previous = obsidian.parseYaml;
	});
	afterEach(() => {
		obsidian.parseYaml = previous;
		jest.restoreAllMocks();
	});

	it('returns the parsed mapping', () => {
		obsidian.parseYaml = jest.fn().mockReturnValue({ Front: 'q' });
		expect(parseCardYaml('Front: q')).toEqual({ Front: 'q' });
		expect(obsidian.parseYaml).toHaveBeenCalledWith('Front: q');
	});

	it('returns null for empty, scalar, or list YAML', () => {
		obsidian.parseYaml = jest.fn().mockReturnValue(null);
		expect(parseCardYaml('')).toBeNull();
		obsidian.parseYaml = jest.fn().mockReturnValue('plain string');
		expect(parseCardYaml('plain string')).toBeNull();
		obsidian.parseYaml = jest.fn().mockReturnValue(['a', 'b']);
		expect(parseCardYaml('- a\n- b')).toBeNull();
	});

	it('returns null, and logs, when the YAML does not parse', () => {
		obsidian.parseYaml = jest.fn().mockImplementation(() => {
			throw new Error('bad yaml');
		});
		const log = jest.spyOn(console, 'error').mockImplementation(() => {});
		expect(parseCardYaml('{')).toBeNull();
		expect(log).toHaveBeenCalled();
	});
});

describe('CardYamlEditorView: writing the edited card back', () => {
	let obsidian: any;
	let previous: any;
	beforeEach(() => {
		jest.clearAllMocks();
		obsidian = jest.requireMock('obsidian');
		previous = obsidian.parseYaml;
	});
	afterEach(() => {
		obsidian.parseYaml = previous;
	});

	function withEditor(text: string, parsed: Card) {
		const ctx = makeView(cards());
		ctx.view.editorView = { state: { doc: { toString: () => text } } };
		obsidian.parseYaml = jest.fn().mockReturnValue(parsed);
		return ctx;
	}

	it('replaces only the selected card with what the source editor holds', async () => {
		const { view, fm } = withEditor('Front: new', { id: 'b', Front: 'new' });
		await view.loadCards();
		view.currentCardIndex = 1;
		view.viewMode = 'source';

		await view.syncToMain();

		expect(obsidian.parseYaml).toHaveBeenCalledWith('Front: new');
		expect(fm.cards).toEqual([
			{ id: 'a', Front: 'A', Back: 'a' },
			{ id: 'b', Front: 'new' },
			{ id: 'c', Front: 'C', Back: 'c' },
		]);
	});

	it('aborts writing and shows an inline error when source YAML is invalid', async () => {
		const log = jest.spyOn(console, 'error').mockImplementation(() => {});
		const { view, app, fm } = makeView(cards());
		await view.loadCards();
		view.parseErrorEl = createMockElement('div');
		view.editorView = { state: { doc: { toString: () => 'Front: [unclosed' } } };
		view.viewMode = 'source';

		await view.syncToMain();

		expect(app.fileManager.processFrontMatter).not.toHaveBeenCalled();
		expect(fm.cards[0]).toEqual({ id: 'a', Front: 'A', Back: 'a' });
		expect(view.parseErrorEl.show).toHaveBeenCalled();
		expect(view.parseErrorEl.setText).toHaveBeenCalledWith(
			expect.stringContaining('Invalid YAML'),
		);
		log.mockRestore();
	});

	it('in field mode writes the structured cloned card directly without YAML round-trip', async () => {
		const { view, fm } = withEditor('STALE', { id: 'a', Front: 'unused' });
		fm.cards[0].tags = ['math', 'calc'];
		fm.cards[0].markdown = false;
		await view.loadCards();
		view.viewMode = 'fields';
		view.cards[0].Front = '---\n"quoted": value';

		await view.syncToMain();

		expect(obsidian.parseYaml).not.toHaveBeenCalled();
		expect(fm.cards[0]).toEqual({
			id: 'a',
			Front: '---\n"quoted": value',
			Back: 'a',
			tags: ['math', 'calc'],
			markdown: false,
		});
		expect(fm.cards[0]).not.toBe(view.cards[0]);
	});

	it('binds target file and card index at schedule time and flushes before switching cards', async () => {
		jest.useFakeTimers();
		try {
			const { view, fm } = makeView(cards());
			await view.loadCards();
			view.viewMode = 'fields';
			view.currentCardIndex = 0;
			view.cards[0].Front = 'edited card 0';
			view.debouncedSyncToMain();

			// Immediately switch to card 2 before the 300ms timer fires
			view.selectCard(2);
			await Promise.resolve();
			jest.advanceTimersByTime(300);
			await Promise.resolve();

			expect(fm.cards[0].Front).toBe('edited card 0');
			expect(fm.cards[2].Front).toBe('C');
		} finally {
			jest.useRealTimers();
		}
	});

	it('flushes pending edits on close before destroying the editor', async () => {
		jest.useFakeTimers();
		try {
			const { view, fm } = makeView(cards());
			await view.loadCards();
			view.viewMode = 'fields';
			view.cards[0].Front = 'saved on close';
			view.debouncedSyncToMain();

			await view.onClose();

			expect(fm.cards[0].Front).toBe('saved on close');
		} finally {
			jest.useRealTimers();
		}
	});

	it('does not mutate cached frontmatter objects when loading or editing', async () => {
		const initial = cards();
		const { view } = makeView(initial);
		await view.loadCards();
		view.cards[0].Front = 'mutated locally';
		expect(initial[0].Front).toBe('A');
	});

	it('does not invent a card when the selection is not in the frontmatter', async () => {
		const { view, fm } = withEditor('x', { Front: 'x' });
		await view.loadCards();
		view.currentCardIndex = 7;
		view.viewMode = 'source';
		await view.syncToMain();
		expect(fm.cards).toHaveLength(3);
	});

	it('writes nothing without an editor or without an active note', async () => {
		const noEditor = makeView(cards());
		await noEditor.view.syncToMain();
		expect(noEditor.app.fileManager.processFrontMatter).not.toHaveBeenCalled();

		const noNote = withEditor('x', { Front: 'x' });
		noNote.app.workspace.getActiveFile.mockReturnValue(null);
		await noNote.view.syncToMain();
		expect(noNote.app.fileManager.processFrontMatter).not.toHaveBeenCalled();
	});

	it('clears the in-flight counter even when the write fails', async () => {
		const { view, app } = withEditor('x', { Front: 'x' });
		app.fileManager.processFrontMatter.mockRejectedValue(new Error('disk full'));
		view.viewMode = 'source';
		await expect(view.syncToMain()).rejects.toThrow('disk full');
		expect(view.inFlightWrites).toBe(0);
	});

	it('tracks overlapping writes with an in-flight counter', async () => {
		const resolvers: Array<() => void> = [];
		const { view, app, note } = makeView(cards());
		view.syncFromMain = jest.fn();
		app.fileManager.processFrontMatter.mockImplementation(
			() => new Promise<void>((resolve) => resolvers.push(resolve)),
		);
		await view.loadCards();
		view.viewMode = 'fields';

		const w1 = view.syncToMain();
		const w2 = view.syncToMain();
		expect(view.inFlightWrites).toBe(2);

		resolvers[0]();
		await w1;
		expect(view.inFlightWrites).toBe(1);
		view.onFileParsed(note);
		expect(view.syncFromMain).not.toHaveBeenCalled();

		resolvers[1]();
		await w2;
		expect(view.inFlightWrites).toBe(0);
	});

	it('preserves externally written id and anki metadata during debounced sync', async () => {
		jest.useFakeTimers();
		try {
			const { view, fm } = makeView([{ model: 'Basic', Front: 'Q', Back: 'A' }]);
			await view.loadCards();
			view.viewMode = 'fields';
			view.cards[0].Front = 'Q edited';
			view.debouncedSyncToMain();

			// Sync writes id and anki to frontmatter before the 300 ms timer fires.
			fm.cards[0].id = 'arete_01SYNC';
			fm.cards[0].anki = { nid: 111, cid: 222 };

			await jest.advanceTimersByTimeAsync(300);

			expect(fm.cards[0]).toEqual({
				model: 'Basic',
				Front: 'Q edited',
				Back: 'A',
				id: 'arete_01SYNC',
				anki: { nid: 111, cid: 222 },
			});
			expect(view.cards[0].id).toBe('arete_01SYNC');
			expect(view.cards[0].anki).toEqual({ nid: 111, cid: 222 });
		} finally {
			jest.useRealTimers();
		}
	});

	it('cancels a pending debounced write when an external parse arrives first', async () => {
		jest.useFakeTimers();
		try {
			const { view, fm, note, app } = makeView(cards());
			await view.loadCards();
			view.viewMode = 'fields';
			view.cards[0].Front = 'stale local edit';
			view.debouncedSyncToMain();

			// External process updates frontmatter and Obsidian re-parses the note.
			fm.cards[0] = { id: 'a', Front: 'external update', Back: 'a', anki: { nid: 77 } };
			note.stat.mtime = 9999;
			view.onFileParsed(note);
			await Promise.resolve();

			await jest.advanceTimersByTimeAsync(300);

			expect(app.fileManager.processFrontMatter).not.toHaveBeenCalled();
			expect(fm.cards[0].Front).toBe('external update');
			expect(view.cards[0].Front).toBe('external update');
		} finally {
			jest.useRealTimers();
		}
	});

	it('scopes ownWrite by path and clears it on echo or file switch', async () => {
		const { view, app, note } = makeView(cards());
		view.syncFromMain = jest.fn();
		await view.loadCards();

		await view.addCard();
		const mtime = note.stat.mtime;

		// Another file with the exact same mtime must not be ignored as an echo.
		const other = { path: 'Other.md', stat: { mtime } };
		app.workspace.getActiveFile.mockReturnValue(other);
		view.onFileParsed(other);
		expect(view.syncFromMain).toHaveBeenCalledTimes(1);

		// Echo for the original file is ignored once, then cleared.
		app.workspace.getActiveFile.mockReturnValue(note);
		await view.addCard();
		view.syncFromMain.mockClear();
		view.onFileParsed(note);
		expect(view.syncFromMain).not.toHaveBeenCalled();
		expect(view.ownWrite).toBeNull();
		view.onFileParsed(note);
		expect(view.syncFromMain).toHaveBeenCalledTimes(1);

		// Switching files via loadCards clears ownWrite.
		await view.addCard();
		expect(view.ownWrite).not.toBeNull();
		app.workspace.getActiveFile.mockReturnValue(other);
		await view.loadCards();
		expect(view.ownWrite).toBeNull();
	});

	it('catches processFrontMatter failures in debouncedSyncToMain, addCard, deleteCard, and reorderCards', async () => {
		jest.useFakeTimers();
		const errSpy = jest.spyOn(console, 'error').mockImplementation(() => {});
		(Notice as unknown as jest.Mock).mockClear();
		try {
			const { view, app } = makeView(cards());
			await view.loadCards();
			app.fileManager.processFrontMatter.mockRejectedValue(new Error('read-only'));

			view.viewMode = 'fields';
			view.cards[0].Front = 'changed';
			view.debouncedSyncToMain();
			await jest.advanceTimersByTimeAsync(300);

			await view.addCard();
			await view.deleteCard(0);
			await view.reorderCards(0, 1);

			expect(view.inFlightWrites).toBe(0);
			expect(view.cards).toHaveLength(3);
			expect(errSpy).toHaveBeenCalledWith(
				expect.stringContaining('[Arete]'),
				expect.any(Error),
			);
			const notices = (Notice as unknown as jest.Mock).mock.calls.map((c) => c[0]);
			expect(notices).toEqual([
				'Failed to save card changes.',
				'Failed to add card.',
				'Failed to delete card.',
				'Failed to reorder cards.',
			]);
		} finally {
			errSpy.mockRestore();
			jest.useRealTimers();
		}
	});

	it('blocks mode and card switches while Source YAML is invalid and keeps the error visible', async () => {
		const errSpy = jest.spyOn(console, 'error').mockImplementation(() => {});
		try {
			const { view, app } = makeView(cards());
			await view.loadCards();
			view.parseErrorEl = createMockElement('div');
			view.editorContainer = createMockElement('div');
			view.fieldEditorContainer = createMockElement('div');
			view.previewContainer = createMockElement('div');
			view.renderToolbar = jest.fn();
			view.viewMode = 'source';

			let docText = 'Front: [unclosed';
			view.editorView = { state: { doc: { toString: () => docText } }, dispatch: jest.fn() };
			view.debouncedSyncToMain();

			// Mode switch and card switch are blocked while pending Source YAML is invalid.
			view.setViewMode('fields');
			expect(view.viewMode).toBe('source');
			expect(view.parseErrorEl.show).toHaveBeenCalled();

			view.selectCard(1);
			expect(view.currentCardIndex).toBe(0);
			view.focusCard(2);
			expect(view.currentCardIndex).toBe(0);
			expect(await view.flushPendingSync()).toBe(false);

			// Also stays blocked after the debounce has already failed and cleared pendingSync.
			view.pendingSync = null;
			view.setViewMode('preview');
			expect(view.viewMode).toBe('source');
			view.selectCard(1);
			expect(view.currentCardIndex).toBe(0);
			expect(app.fileManager.processFrontMatter).not.toHaveBeenCalled();

			// Fixing the YAML restores flushing and switching.
			docText = 'id: a\nFront: Fixed\nBack: a';
			view.debouncedSyncToMain();
			expect(await view.flushPendingSync()).toBe(true);
			view.selectCard(1);
			expect(view.currentCardIndex).toBe(1);
		} finally {
			errSpy.mockRestore();
		}
	});

	it('debounces edits: one write, 300 ms after the last keystroke', () => {
		jest.useFakeTimers();
		try {
			const { view } = makeView(cards());
			view.syncToMain = jest.fn();
			view.debouncedSyncToMain();
			jest.advanceTimersByTime(200);
			view.debouncedSyncToMain();
			jest.advanceTimersByTime(299);
			expect(view.syncToMain).not.toHaveBeenCalled();
			jest.advanceTimersByTime(1);
			expect(view.syncToMain).toHaveBeenCalledTimes(1);
		} finally {
			jest.useRealTimers();
		}
	});

	it('does not reload from the file while its own write is in flight', () => {
		const { view, note } = makeView(cards());
		view.syncFromMain = jest.fn();
		view.inFlightWrites = 1;
		view.onFileParsed(note);
		expect(view.syncFromMain).not.toHaveBeenCalled();
	});

	it('skips the own-write parse echo after addCard, deleteCard, and reorderCards', async () => {
		const { view, note } = makeView(cards());
		view.syncFromMain = jest.fn();
		await view.loadCards();

		await view.addCard();
		view.onFileParsed(note);
		expect(view.syncFromMain).not.toHaveBeenCalled();

		await view.reorderCards(0, 1);
		view.onFileParsed(note);
		expect(view.syncFromMain).not.toHaveBeenCalled();

		await view.deleteCard(0);
		view.onFileParsed(note);
		expect(view.syncFromMain).not.toHaveBeenCalled();
	});
});

describe('CardYamlEditorView: field editor', () => {
	it('lists the content fields, hides reserved structural keys, and writes edits back to the card', () => {
		jest.useFakeTimers();
		try {
			const sample = {
				id: 'a',
				model: 'Basic',
				deck: 'Math',
				tags: ['exam'],
				markdown: true,
				cid: 10,
				nid: 20,
				Front: 'q',
				Back: 'a',
				anki: { nid: 1 },
				__line__: 4,
			};
			const { view } = makeView([sample]);
			const inputs: Record<string, any> = {};
			const group = createMockElement('div', {
				createEl: jest.fn((tag: string, o: any) => {
					const el = createMockElement(tag, { ...o, style: {}, value: o?.text });
					if (tag === 'textarea') inputs[o.text] = el;
					return el;
				}),
			});
			view.fieldEditorContainer = createMockElement('div', {
				createDiv: jest.fn(() => group),
			});
			view.cards = [sample];
			view.fileModel = 'Basic';
			view.renderDependencySection = jest.fn();
			view.debouncedSyncToMain = jest.fn();

			view.renderFieldEditor();

			expect(Object.keys(inputs)).toEqual(['q', 'a']);
			const labels = group.createEl.mock.calls
				.filter((c: any[]) => c[0] === 'label')
				.map((c: any[]) => c[1].text);
			expect(labels).toEqual(['Front', 'Back']);

			inputs['q'].value = 'edited';
			inputs['q']._listeners['input']();
			expect(view.cards[0].Front).toBe('edited');
			expect(view.debouncedSyncToMain).toHaveBeenCalledTimes(1);
		} finally {
			jest.useRealTimers();
		}
	});

	it('does not mutate deps on view, and creates deps only when dependencies are edited', () => {
		const { view } = makeView([{ id: 'a', Front: 'q' }]);
		view.fieldEditorContainer = createMockElement('div');
		view.cards = [{ id: 'a', Front: 'q' }];
		view.debouncedSyncToMain = jest.fn();
		const sections: Record<string, (v: string[]) => void> = {};
		view.renderDependencySection = jest.fn((label: string, _init: string[], cb: any) => {
			sections[label] = cb;
		});

		view.renderFieldEditor();

		expect(view.cards[0].deps).toBeUndefined();
		sections['requires'](['arete_x']);
		sections['related'](['arete_y']);
		expect(view.cards[0].deps).toEqual({ requires: ['arete_x'], related: ['arete_y'] });
		expect(view.debouncedSyncToMain).toHaveBeenCalledTimes(2);
	});

	it('handles a non-object deps value without mutating on view, replacing it on edit', () => {
		const { view } = makeView([{ id: 'a', deps: 'junk' }]);
		view.fieldEditorContainer = createMockElement('div');
		view.cards = [{ id: 'a', deps: 'junk' }];
		view.debouncedSyncToMain = jest.fn();
		const sections: Record<string, (v: string[]) => void> = {};
		view.renderDependencySection = jest.fn((label: string, init: string[], cb: any) => {
			expect(init).toEqual([]);
			sections[label] = cb;
		});
		view.renderFieldEditor();
		expect(view.cards[0].deps).toBe('junk');
		sections['requires'](['arete_x']);
		expect(view.cards[0].deps).toEqual({ requires: ['arete_x'] });
	});

	it('renderIndex tolerates null cards and non-string Front values', () => {
		const { view } = makeView([]);
		const listEl = createMockElement('div', {
			setAttribute: jest.fn(),
		});
		const indexEl = createMockElement('div', {
			createDiv: jest
				.fn()
				.mockReturnValueOnce(createMockElement('div'))
				.mockReturnValueOnce(listEl),
		});
		view.indexContainer = indexEl;
		view.cards = [null, { Front: 12345 }, { Text: true }];
		view.getCardWarning = jest.fn().mockReturnValue(false);
		// Restore real renderIndex on this test view
		view.renderIndex = (CardYamlEditorView.prototype as any).renderIndex.bind(view);

		expect(() => view.renderIndex()).not.toThrow();
	});

	it('renders nothing for an empty card list', () => {
		const { view } = makeView([]);
		view.fieldEditorContainer = createMockElement('div');
		view.cards = [];
		view.renderFieldEditor();
		expect(view.fieldEditorContainer.createDiv).not.toHaveBeenCalled();
	});

	it('extractCardFields keeps only scalar content fields, as strings', () => {
		expect(
			extractCardFields({
				id: 'a',
				model: 'Basic',
				deps: { requires: [] },
				anki: { nid: 1 },
				Front: 'q',
				Back: 7,
				Empty: null,
				Missing: undefined,
				Nested: { x: 1 },
				__line__: 3,
			}),
		).toEqual({ Front: 'q', Back: '7' });
	});

	it('getCardModel prefers the card model over the note model', () => {
		const { view } = makeView([]);
		view.fileModel = 'Basic';
		expect(view.getCardModel({ model: 'Cloze' })).toBe('Cloze');
		expect(view.getCardModel({})).toBe('Basic');
	});
});

describe('CardYamlEditorView: selection and navigation', () => {
	it('selectCard ignores indexes outside the list', () => {
		const { view, host } = makeView(cards());
		view.cards = cards();
		view.selectCard(-1);
		view.selectCard(3);
		expect(view.currentCardIndex).toBe(0);
		expect(host.highlightCardLines).not.toHaveBeenCalled();
	});

	it('selectCard moves the active marker, refreshes and highlights the lines', () => {
		const { view, host } = makeView(cards());
		view.cards = cards();
		const prev = createMockElement('div');
		const next = createMockElement('div', { scrollIntoView: jest.fn() });
		const list = createMockElement('div', { focus: jest.fn() });
		view.indexContainer = {
			querySelector: jest.fn((sel: string) => {
				if (sel.includes('data-index="0"')) return prev;
				if (sel.includes('data-index="2"')) return next;
				if (sel.includes('index-list')) return list;
				return null;
			}),
		};

		view.selectCard(2, true);

		expect(prev.removeClass).toHaveBeenCalledWith('is-active');
		expect(next.addClass).toHaveBeenCalledWith('is-active');
		expect(list.focus).toHaveBeenCalled();
		expect(next.scrollIntoView).toHaveBeenCalledWith({ block: 'nearest' });
		expect(view.currentCardIndex).toBe(2);
		expect(view.refreshActiveView).toHaveBeenCalled();
		expect(host.highlightCardLines).toHaveBeenCalledWith(2);
	});

	it('arrow keys step the selection and stay inside the list', () => {
		const { view } = makeView(cards());
		view.cards = cards();
		view.selectCard = jest.fn();
		const key = (k: string) => ({ key: k, preventDefault: jest.fn() }) as any;

		view.currentCardIndex = 1;
		view.handleKeyNavigation(key('ArrowDown'));
		expect(view.selectCard).toHaveBeenLastCalledWith(2, true);
		view.handleKeyNavigation(key('ArrowUp'));
		expect(view.selectCard).toHaveBeenLastCalledWith(0, true);

		view.currentCardIndex = 2;
		view.handleKeyNavigation(key('ArrowDown'));
		expect(view.selectCard).toHaveBeenLastCalledWith(2, true);
		view.currentCardIndex = 0;
		view.handleKeyNavigation(key('ArrowUp'));
		expect(view.selectCard).toHaveBeenLastCalledWith(0, true);

		view.selectCard.mockClear();
		view.handleKeyNavigation(key('Enter'));
		expect(view.selectCard).not.toHaveBeenCalled();
	});

	it('focusCard and showCardIndex ignore an index outside the list', async () => {
		const { view } = makeView(cards());
		await view.showCardIndex(1);
		expect(view.currentCardIndex).toBe(1);
		expect(view.refreshActiveView).toHaveBeenCalledTimes(1);

		view.focusCard(9);
		view.focusCard(-1);
		expect(view.currentCardIndex).toBe(1);
		expect(view.refreshActiveView).toHaveBeenCalledTimes(1);
	});
});

describe('CardYamlEditorView: Anki links and warnings', () => {
	beforeEach(() => {
		(Notice as unknown as jest.Mock).mockClear();
	});
	const notices = () => (Notice as unknown as jest.Mock).mock.calls.map((c) => c[0]);

	it('opens the Anki browser on the card note id', async () => {
		const browse = jest.fn().mockResolvedValue(true);
		const { view } = makeView([{ id: 'a', anki: { nid: 99 } }], { areteClient: { browse } });
		await view.loadCards();
		await view.openInAnki(0);
		expect(browse).toHaveBeenCalledWith('nid:99');
		expect(notices()).toEqual([]);
	});

	it('says so when Anki refuses to open', async () => {
		const browse = jest.fn().mockResolvedValue(false);
		const { view } = makeView([{ id: 'a', anki: { nid: 99 } }], { areteClient: { browse } });
		await view.loadCards();
		await view.openInAnki(0);
		expect(notices()).toEqual(['Failed to open Anki browser.']);
	});

	it('says so when the call throws', async () => {
		const browse = jest.fn().mockRejectedValue(new Error('down'));
		const { view } = makeView([{ id: 'a', anki: { nid: 99 } }], { areteClient: { browse } });
		await view.loadCards();
		await view.openInAnki(0);
		expect(notices()).toEqual(['Could not open the Anki browser, check that Anki is running.']);
	});

	it('does not call Anki for a card that was never synced', async () => {
		const browse = jest.fn();
		const { view } = makeView([{ id: 'a' }], { areteClient: { browse } });
		await view.loadCards();
		await view.openInAnki(0);
		expect(browse).not.toHaveBeenCalled();
		expect(notices()).toEqual(['Card is not yet synced to Anki (no NID found).']);
	});

	describe('getCardWarning', () => {
		const statsFor = (problematic?: Array<{ noteId: number }>) => ({
			statsService: {
				getCache: () => ({ concepts: { 'Note.md': { problematicCards: problematic } } }),
			},
		});

		it('flags a card whose note id is among the problematic cards', async () => {
			const { view } = makeView(
				[{ id: 'a', anki: { nid: 5 } }, { id: 'b', anki: { nid: 6 } }, { id: 'c' }],
				statsFor([{ noteId: 6 }]),
			);
			await view.loadCards();
			expect(view.getCardWarning(0)).toBe(false);
			expect(view.getCardWarning(1)).toBe(true);
			expect(view.getCardWarning(2)).toBe(false);
		});

		it('is false when the note has no stats or no problematic list', async () => {
			const none = makeView([{ id: 'a', anki: { nid: 5 } }], {
				statsService: { getCache: () => ({ concepts: {} }) },
			});
			await none.view.loadCards();
			expect(none.view.getCardWarning(0)).toBe(false);

			const noList = makeView([{ id: 'a', anki: { nid: 5 } }], statsFor(undefined));
			await noList.view.loadCards();
			expect(noList.view.getCardWarning(0)).toBe(false);
		});

		it('is false without an active note', async () => {
			const { view, app } = makeView([{ id: 'a', anki: { nid: 5 } }], statsFor([]));
			await view.loadCards();
			app.workspace.getActiveFile.mockReturnValue(null);
			expect(view.getCardWarning(0)).toBe(false);
		});
	});
});

describe('CardYamlEditorView: scrolling the note editor to a card', () => {
	function editorWith(content: string) {
		const parsed = CardParserService.parseCards(content);
		const editor = {
			getValue: () => content,
			setCursor: jest.fn(),
			scrollIntoView: jest.fn(),
			cm: {
				state: {
					field: jest.fn((f: unknown) => (f === cardRangesField ? parsed : undefined)),
				},
			},
		};
		const mdView: any = new MarkdownView({} as any);
		mdView.editor = editor;
		return { editor, mdView };
	}

	it('puts the cursor on the card start line from cardRangesField, skipping nested list dashes', async () => {
		const { view, app } = makeView(cards());
		const { editor, mdView } = editorWith(
			[
				'---',
				'cards:',
				'  - id: a',
				'    tags:',
				'      - nested-tag',
				'    Front: A',
				'  - id: b',
				'    Front: B',
				'---',
			].join('\n'),
		);
		app.workspace.getMostRecentLeaf = jest.fn().mockReturnValue({ view: mdView });

		await view.scrollToCard(1);

		expect(editor.setCursor).toHaveBeenCalledWith({ line: 6, ch: 0 });
		expect(editor.scrollIntoView).toHaveBeenCalledWith(
			{ from: { line: 6, ch: 0 }, to: { line: 6, ch: 0 } },
			true,
		);
	});

	it('leaves the cursor alone when the card is not in the text', async () => {
		const { view, app } = makeView(cards());
		const { editor, mdView } = editorWith('---\ncards:\n  - id: a\n---');
		app.workspace.getMostRecentLeaf = jest.fn().mockReturnValue({ view: mdView });
		await view.scrollToCard(5);
		expect(editor.setCursor).not.toHaveBeenCalled();
	});

	it('does nothing when the most recent leaf is not a Markdown editor', async () => {
		const { view, app } = makeView(cards());
		app.workspace.getMostRecentLeaf = jest.fn().mockReturnValue({ view: {} });
		await expect(view.scrollToCard(0)).resolves.toBeUndefined();
	});
});
