import { MarkdownView, Notice } from 'obsidian';
import { CardYamlEditorView } from '@/presentation/views/CardYamlEditorView';
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
			const { view, fm } = makeView(cards());
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
			expect(view.cards).toBe(fm.cards);
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

	it('writes content fields as |- block scalars, even when plain', () => {
		expect(yamlOf({ Front: 'q', Back: 'a' })).toBe('Front: |-\n  q\nBack: |-\n  a');
	});

	it('indents every line of a multi-line value', () => {
		expect(yamlOf({ Front: 'one\ntwo\n' })).toBe('Front: |-\n  one\n  two');
	});

	it('writes an empty content field as an empty string', () => {
		expect(yamlOf({ Front: '' })).toBe("Front: ''");
	});

	it('block-scalars a non-content string that holds YAML-special characters', () => {
		expect(yamlOf({ Source: 'a: b' })).toBe('Source: |-\n  a: b');
		expect(yamlOf({ Source: 'plain' })).toBe('Source: plain');
	});

	it('writes non-string scalars as they are', () => {
		expect(yamlOf({ id: 'a1', count: 3, flag: true })).toBe('id: a1\ncount: 3\nflag: true');
	});

	it('block-scalars a value with an underscore, such as an arete id (same YAML value)', () => {
		expect(yamlOf({ id: 'arete_1' })).toBe('id: |-\n  arete_1');
	});

	it('writes deps as a structure with only the non-empty lists', () => {
		expect(yamlOf({ deps: { requires: ['x', 'y'], related: [] } })).toBe(
			'deps:\n  requires:\n    - x\n    - y',
		);
		expect(yamlOf({ deps: { requires: [], related: ['z'] } })).toBe(
			'deps:\n  related:\n    - z',
		);
		expect(yamlOf({ deps: { requires: ['x'], related: ['z'] } })).toBe(
			'deps:\n  requires:\n    - x\n  related:\n    - z',
		);
	});

	it('resets a deps value that is not a mapping to an empty one', () => {
		expect(yamlOf({ deps: {} })).toBe('deps: {}');
		expect(yamlOf({ deps: 'x' })).toBe('deps: {}');
		expect(yamlOf({ deps: null })).toBe('deps: {}');
	});

	it('writes the anki block with only the ids that exist', () => {
		expect(yamlOf({ anki: { nid: 11, cid: 22 } })).toBe('anki:\n  nid: 11\n  cid: 22');
		expect(yamlOf({ anki: { nid: 11 } })).toBe('anki:\n  nid: 11');
		expect(yamlOf({ anki: {} })).toBe('anki: {}');
		expect(yamlOf({ anki: 'x' })).toBe('anki: {}');
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
		const { view } = makeView([]);
		expect(view.parseYamlToCard('Front: q')).toEqual({ Front: 'q' });
		expect(obsidian.parseYaml).toHaveBeenCalledWith('Front: q');
	});

	it('returns an empty card for empty YAML', () => {
		obsidian.parseYaml = jest.fn().mockReturnValue(null);
		const { view } = makeView([]);
		expect(view.parseYamlToCard('')).toEqual({});
	});

	it('returns an empty card, and logs, when the YAML does not parse', () => {
		obsidian.parseYaml = jest.fn().mockImplementation(() => {
			throw new Error('bad yaml');
		});
		const log = jest.spyOn(console, 'error').mockImplementation(() => {});
		const { view } = makeView([]);
		expect(view.parseYamlToCard('{')).toEqual({});
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

	it('in field mode writes the YAML built from the field edits, not the editor text', async () => {
		const { view, fm } = withEditor('STALE', { id: 'a', Front: 'from fields' });
		await view.loadCards();
		view.viewMode = 'fields';
		view.cards[0].Front = 'from fields';

		await view.syncToMain();

		const parsedText = (obsidian.parseYaml as jest.Mock).mock.calls[0][0];
		expect(parsedText).toContain('Front: |-\n  from fields');
		expect(parsedText).not.toContain('STALE');
		expect(fm.cards[0]).toEqual({ id: 'a', Front: 'from fields' });
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

	it('clears the in-flight flag even when the write fails', async () => {
		const { view, app } = withEditor('x', { Front: 'x' });
		app.fileManager.processFrontMatter.mockRejectedValue(new Error('disk full'));
		view.viewMode = 'source';
		await expect(view.syncToMain()).rejects.toThrow('disk full');
		expect(view.isUpdatingFromMain).toBe(false);
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
		view.isUpdatingFromMain = true;
		view.onFileParsed(note);
		expect(view.syncFromMain).not.toHaveBeenCalled();
	});
});

describe('CardYamlEditorView: field editor', () => {
	it('lists the content fields, hides structural keys, and writes edits back to the card', () => {
		jest.useFakeTimers();
		try {
			const { view } = makeView([
				{ id: 'a', model: 'Basic', Front: 'q', Back: 'a', anki: { nid: 1 }, __line__: 4 },
			]);
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
			view.cards = [
				{ id: 'a', model: 'Basic', Front: 'q', Back: 'a', anki: { nid: 1 }, __line__: 4 },
			];
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

	it('creates the deps structure and writes dependency changes into it', () => {
		const { view } = makeView([{ id: 'a', Front: 'q' }]);
		view.fieldEditorContainer = createMockElement('div');
		view.cards = [{ id: 'a', Front: 'q' }];
		view.debouncedSyncToMain = jest.fn();
		const sections: Record<string, (v: string[]) => void> = {};
		view.renderDependencySection = jest.fn((label: string, _init: string[], cb: any) => {
			sections[label] = cb;
		});

		view.renderFieldEditor();

		expect(view.cards[0].deps).toEqual({ requires: [], related: [] });
		sections['requires'](['arete_x']);
		sections['related'](['arete_y']);
		expect(view.cards[0].deps).toEqual({ requires: ['arete_x'], related: ['arete_y'] });
		expect(view.debouncedSyncToMain).toHaveBeenCalledTimes(2);
	});

	it('replaces a deps value that is not an object', () => {
		const { view } = makeView([{ id: 'a', deps: 'junk' }]);
		view.fieldEditorContainer = createMockElement('div');
		view.cards = [{ id: 'a', deps: 'junk' }];
		view.renderDependencySection = jest.fn();
		view.renderFieldEditor();
		expect(view.cards[0].deps).toEqual({ requires: [], related: [] });
	});

	it('renders nothing for an empty card list', () => {
		const { view } = makeView([]);
		view.fieldEditorContainer = createMockElement('div');
		view.cards = [];
		view.renderFieldEditor();
		expect(view.fieldEditorContainer.createDiv).not.toHaveBeenCalled();
	});

	it('extractCardFields keeps only scalar content fields, as strings', () => {
		const { view } = makeView([]);
		expect(
			view.extractCardFields({
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
		const editor = {
			getValue: () => content,
			setCursor: jest.fn(),
			scrollIntoView: jest.fn(),
		};
		const mdView: any = new MarkdownView({} as any);
		mdView.editor = editor;
		return { editor, mdView };
	}

	it('puts the cursor on the list item line of the requested card', async () => {
		const { view, app } = makeView(cards());
		const { editor, mdView } = editorWith(
			['---', 'cards:', '  - id: a', '    Front: A', '  - id: b', '    Front: B', '---'].join(
				'\n',
			),
		);
		app.workspace.getMostRecentLeaf = jest.fn().mockReturnValue({ view: mdView });

		await view.scrollToCard(1);

		expect(editor.setCursor).toHaveBeenCalledWith({ line: 4, ch: 0 });
		expect(editor.scrollIntoView).toHaveBeenCalledWith(
			{ from: { line: 4, ch: 0 }, to: { line: 4, ch: 0 } },
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
