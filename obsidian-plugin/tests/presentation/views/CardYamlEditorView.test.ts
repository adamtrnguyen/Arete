import { App, Component } from 'obsidian';
import { CardYamlEditorView } from '@/presentation/views/CardYamlEditorView';
import { renderCardPreviewFrame } from '@/presentation/renderers/CardPreviewFrame';
import { CardRenderer } from '@/presentation/renderers/CardRenderer';
import { createMockElement } from '../../test-setup';

jest.mock('@/presentation/renderers/CardPreviewFrame');
jest.mock('@/presentation/renderers/CardRenderer');

describe('CardYamlEditorView preview', () => {
	let view: any;
	let app: App;
	let render: jest.Mock;
	beforeEach(() => {
		jest.clearAllMocks();
		app = new (jest.requireMock('obsidian').App)();
		render = jest
			.fn()
			.mockResolvedValue({ html: '<p>Answer</p>', css: '.card{font-size:22px}' });
		view = new CardYamlEditorView({} as any, { templateRenderer: { render } } as any);
		view.app = app;
		view.previewContainer = createMockElement('div');
		view.fieldEditorContainer = createMockElement('div');
		view.renderDependencySection = jest.fn();
		(app.workspace.getActiveFile as jest.Mock).mockReturnValue({ path: 'Zetetics.md' });
		(app.metadataCache.getFileCache as jest.Mock).mockReturnValue({
			frontmatter: {
				model: 'Basic with Extra',
				cards: [{ Front: 'F', Back: 'B', 'Back Extra': 'Extra' }],
			},
		});
	});

	it('uses the file model in Fields and Preview without adding it to card YAML', async () => {
		await view.loadCards();
		view.renderFieldEditor();
		expect(view.fieldEditorContainer.createDiv).toHaveBeenCalledWith(
			expect.objectContaining({ text: 'Basic with Extra' }),
		);
		await view.renderPreview();
		expect(render).toHaveBeenCalledWith(
			'Basic with Extra',
			'Front',
			expect.objectContaining({ 'Back Extra': 'Extra' }),
			{
				sourcePath: 'Zetetics.md',
				component: expect.any(Component),
			},
		);
		expect(view.cards[0].model).toBeUndefined();
		view.cards[0].model = 'Cloze';
		await view.renderPreview();
		expect(render.mock.calls[1][0]).toBe('Cloze');
	});

	it('shows the failure and field fallback instead of leaving an empty preview', async () => {
		await view.loadCards();
		render.mockRejectedValue(new Error('Could not start Arete'));
		await view.renderPreview();
		expect(view.previewContainer.createDiv).toHaveBeenCalledWith(
			expect.objectContaining({ text: 'Could not load card preview: Could not start Arete' }),
		);
		expect(CardRenderer.render).toHaveBeenCalled();
		expect(renderCardPreviewFrame).not.toHaveBeenCalled();
	});

	it('discards a slow previous render after switching sides', async () => {
		await view.loadCards();
		let finish: (value: { html: string; css: string }) => void;
		render.mockImplementationOnce(
			() =>
				new Promise((resolve) => {
					finish = resolve;
				}),
		);
		const previous = view.renderPreview();
		view.previewSide = 'Back';
		await view.renderPreview();
		finish!({ html: 'stale front', css: '' });
		await previous;
		expect(renderCardPreviewFrame).toHaveBeenCalledTimes(1);
		expect(renderCardPreviewFrame).toHaveBeenCalledWith(
			app,
			view.previewContainer,
			{ html: '<p>Answer</p>', css: '.card{font-size:22px}' },
			'Zetetics.md',
			expect.any(Component),
		);
	});
});

describe('CardYamlEditorView live reload', () => {
	// An agent edits the file on disk; the view must show that edit, not the one before.
	let view: any;
	let app: App;
	const note = { path: 'FlashAttention.md', stat: { mtime: 1000 } };
	beforeEach(() => {
		jest.clearAllMocks();
		app = new (jest.requireMock('obsidian').App)();
		view = new CardYamlEditorView({} as any, {} as any);
		view.app = app;
		view.syncFromMain = jest.fn();
		(app.workspace.getActiveFile as jest.Mock).mockReturnValue(note);
	});

	it('reloads when the active file has been re-parsed', () => {
		view.onFileParsed(note);
		expect(view.syncFromMain).toHaveBeenCalledTimes(1);
	});

	it('ignores other files', () => {
		view.onFileParsed({ path: 'Other.md', stat: { mtime: 1000 } });
		expect(view.syncFromMain).not.toHaveBeenCalled();
	});

	it('skips only the echo of its own write, whatever the parse delay', async () => {
		(app.fileManager.processFrontMatter as jest.Mock).mockImplementation(async () => {
			note.stat.mtime = 2000; // the view's own write
		});
		view.editorView = { state: { doc: { toString: () => 'Front: q\nBack: a' } } };
		view.viewMode = 'source';
		view.parseYamlToCard = jest.fn().mockReturnValue({ Front: 'q', Back: 'a' });
		await view.syncToMain();
		view.onFileParsed(note); // re-parse of that write, arriving after any timer
		expect(view.syncFromMain).not.toHaveBeenCalled();

		note.stat.mtime = 3000; // then an agent edits the file
		view.onFileParsed(note);
		expect(view.syncFromMain).toHaveBeenCalledTimes(1);
	});
});

describe('CardYamlEditorView leaf changes and field sizing', () => {
	let view: any;
	let app: App;
	const note = { path: 'Note.md' };
	let cards: any[];

	beforeEach(() => {
		jest.clearAllMocks();
		app = new (jest.requireMock('obsidian').App)();
		view = new CardYamlEditorView({} as any, {} as any);
		view.app = app;
		cards = [{ Front: 'q', Back: 'a' }];
		(app.workspace.getActiveFile as jest.Mock).mockReturnValue(note);
		(app.metadataCache.getFileCache as jest.Mock).mockImplementation(() => ({
			frontmatter: { cards },
		}));
		view.renderIndex = jest.fn();
		view.refreshActiveView = jest.fn();
	});

	const settle = () => new Promise((r) => setImmediate(r));

	it('a leaf change with the same note and parsed cards rebuilds nothing', async () => {
		await view.loadCards();
		view.handleActiveFileChange();
		await settle();
		expect(view.renderIndex).not.toHaveBeenCalled();
		expect(view.refreshActiveView).not.toHaveBeenCalled();
	});

	it('a leaf change to another note reloads', async () => {
		await view.loadCards();
		(app.workspace.getActiveFile as jest.Mock).mockReturnValue({ path: 'Other.md' });
		view.handleActiveFileChange();
		await settle();
		expect(view.renderIndex).toHaveBeenCalledTimes(1);
		expect(view.refreshActiveView).toHaveBeenCalledTimes(1);
	});

	it('a leaf change after the note was re-parsed reloads', async () => {
		await view.loadCards();
		cards = [{ Front: 'q2', Back: 'a' }]; // a re-parse yields a new array
		view.handleActiveFileChange();
		await settle();
		expect(view.refreshActiveView).toHaveBeenCalledTimes(1);
		expect(view.cards).toBe(cards);
	});

	it('sizes every field with all writes before any read (one layout)', async () => {
		jest.useFakeTimers();
		const ops: string[] = [];
		const textarea = (name: string) => {
			const style: Record<string, string> = {};
			return {
				style: new Proxy(style, {
					set(target, prop, value) {
						ops.push(`write ${name} ${value}`);
						target[prop as string] = value;
						return true;
					},
				}),
				get scrollHeight() {
					ops.push(`read ${name}`);
					return 40;
				},
				addEventListener: jest.fn(),
			};
		};
		const group = createMockElement('div', {
			createEl: jest.fn((tag: string, o: any) =>
				tag === 'textarea' ? textarea(o.text) : createMockElement(tag),
			),
		});
		view.fieldEditorContainer = createMockElement('div', { createDiv: jest.fn(() => group) });
		view.renderDependencySection = jest.fn();
		await view.loadCards();

		view.renderFieldEditor();
		jest.runOnlyPendingTimers();
		jest.useRealTimers();

		expect(ops).toEqual([
			'write q auto',
			'write a auto',
			'read q',
			'read a',
			'write q 40px',
			'write a 40px',
		]);
	});
});

describe('CardYamlEditorView toolbar', () => {
	const obsidian = jest.requireMock('obsidian');
	let view: any;

	beforeEach(() => {
		jest.clearAllMocks();
		obsidian.ExtraButtonComponent.instances.length = 0;
		const app = new obsidian.App();
		(app.workspace.getActiveFile as jest.Mock).mockReturnValue({ path: 'N.md' });
		view = new CardYamlEditorView(
			{} as any,
			{
				settings: { stats_algorithm: 'sm2' },
				statsService: { getCache: () => ({ concepts: {} }) },
			} as any,
		);
		view.app = app;
		view.toolbarContainer = createMockElement('div');
		view.cards = [{ Front: 'F', Back: 'B' }];
	});

	const buttons = () => obsidian.ExtraButtonComponent.instances as any[];

	it('draws every icon button as an ExtraButtonComponent with a setTooltip tooltip', () => {
		view.renderToolbar();

		expect(buttons().map((b) => [b.icon, b.tooltip])).toEqual([
			['file-text', 'Go to YAML in note'],
			['external-link', 'Open in Anki'],
			['bar-chart-2', 'Card not synced (no NID)'],
			['share-2', 'Open local graph'],
			['edit-3', 'Card edit mode'],
			['code', 'Source mode'],
			['eye', 'Preview mode'],
		]);
	});

	it('marks the current mode with is-active and a card without an NID is-disabled', () => {
		view.renderToolbar();

		const state = (b: any) =>
			b.extraSettingsEl.toggleClass.mock.calls.map((c: any[]) => `${c[0]}:${c[1]}`);
		const byIcon = (icon: string) => buttons().find((b) => b.icon === icon);
		expect(state(byIcon('edit-3'))).toContain('is-active:true');
		expect(state(byIcon('code'))).toContain('is-active:false');
		expect(state(byIcon('bar-chart-2'))).toContain('is-disabled:true');
		expect(state(byIcon('share-2'))).toContain('is-disabled:false');
	});

	it('sets no inline style on the stats button', () => {
		view.renderToolbar();
		for (const b of buttons()) expect(b.extraSettingsEl.style).toEqual({});
	});

	it('shows badge explanations as tooltips, not title attributes', () => {
		const { setTooltip } = obsidian;
		view.cards = [{ Front: 'F', anki: { nid: 7 }, id: 'arete_1' }];
		view.plugin.statsService = {
			getCache: () => ({
				concepts: {
					'N.md': {
						cardStats: { 7: { ease: 2500, lapses: 2, due: 0 } },
					},
				},
			}),
		};
		view.renderToolbar();

		const texts = (setTooltip as jest.Mock).mock.calls.map((c) => c[1]);
		expect(texts).toContain('SM-2 ease: 250%');
		expect(texts).toContain('Lapses: 2');
	});

	describe('badge colours follow the card-state cut-offs, as the gutter does', () => {
		const badges = (stats: object, thresholds: object = {}) => {
			view.plugin.settings = {
				stats_algorithm: 'fsrs',
				stats_lapse_threshold: 3,
				stats_difficulty_threshold: 0.9,
				stats_ease_threshold: 2100,
				...thresholds,
			};
			view.cards = [{ Front: 'F', anki: { nid: 7 }, id: 'arete_1' }];
			view.plugin.statsService = {
				getCache: () => ({ concepts: { 'N.md': { cardStats: { 7: stats } } } }),
			};
			view.toolbarContainer = createMockElement('div');
			view.renderToolbar();
			const leftGroup = view.toolbarContainer.createDiv.mock.results[0].value;
			const statsContainer = leftGroup.createDiv.mock.results[0].value;
			return statsContainer.createDiv.mock.calls.map((c: any[]) => [c[0].text, c[0].cls]);
		};

		it('leaves an unflagged difficulty green and warns from one lapse', () => {
			expect(badges({ difficulty: 0.52, lapses: 1, due: 0 })).toEqual([
				['D: 5.2', 'arete-stat-badge mod-success'],
				['1L', 'arete-stat-badge mod-warning'],
			]);
		});

		it('warns from difficulty 0.7 and errs at the difficulty and lapse thresholds', () => {
			expect(badges({ difficulty: 0.75, lapses: 3, due: 0 })).toEqual([
				['D: 7.5', 'arete-stat-badge mod-warning'],
				['3L', 'arete-stat-badge mod-error'],
			]);
			expect(badges({ difficulty: 0.9, lapses: 0, due: 0 })).toEqual([
				['D: 9.0', 'arete-stat-badge mod-error'],
			]);
		});

		it('reads the lapse threshold from the settings', () => {
			expect(
				badges({ difficulty: 0.2, lapses: 4, due: 0 }, { stats_lapse_threshold: 4 }),
			).toEqual([
				['D: 2.0', 'arete-stat-badge mod-success'],
				['4L', 'arete-stat-badge mod-error'],
			]);
		});
	});
});

describe('CardYamlEditorView styling', () => {
	it('styles the editor wrapper with a class, not inline style', async () => {
		const obsidian = jest.requireMock('obsidian');
		const app = new obsidian.App();
		const container = createMockElement('div');
		const view: any = new CardYamlEditorView(
			{} as any,
			{
				settings: { stats_algorithm: 'sm2' },
				statsService: { getCache: () => ({ concepts: {} }) },
			} as any,
		);
		view.app = app;
		view.containerEl = { children: [null, container] };
		view.registerEvent = jest.fn();
		for (const draw of ['renderIndex', 'renderToolbar', 'createEditor', 'setViewMode']) {
			view[draw] = jest.fn();
		}

		await view.onOpen();

		const wrapper = view.editorContainer;
		expect(wrapper).toBeDefined();
		expect(container.createDiv).not.toHaveBeenCalledWith(
			expect.objectContaining({
				attr: expect.objectContaining({ style: expect.anything() }),
			}),
		);
		expect(wrapper.style).toEqual({});
	});
});
