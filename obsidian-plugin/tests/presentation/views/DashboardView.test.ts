import '../../test-setup';
import { createMockElement } from '../../test-setup';
import { DashboardView } from '@/presentation/views/DashboardView';
import { StatsNode } from '@/domain/stats';
import { DEFAULT_SETTINGS } from '@/domain/settings';
import { inlineStyled, installNativeObsidian, texts, walk } from '../../mocks/nativeObsidian';

function node(over: Partial<StatsNode>): StatsNode {
	return {
		title: 'x',
		filePath: '',
		deckName: 'x',
		isLeaf: false,
		children: [],
		count: 1,
		lapses: 0,
		difficulty: null,
		stability: null,
		retrievability: null,
		problematicCount: 0,
		score: 0,
		...over,
	};
}

describe('DashboardView overview expand/collapse', () => {
	const leaf = node({ title: 'Note', filePath: 'Note.md', deckName: 'Math', isLeaf: true });
	const deck = node({ title: 'Math', deckName: 'Math', children: [leaf] });
	const root = node({ title: 'Vault', deckName: 'All', children: [deck] });

	let cache: { concepts: Record<string, any>; lastFetched: number };
	let plugin: any;
	let view: DashboardView;
	let container: any;

	beforeEach(() => {
		cache = { concepts: { 'Note.md': { cardStats: {} } }, lastFetched: 1 };
		plugin = {
			settings: { ui_expanded_decks: [], ui_expanded_concepts: [] },
			statsService: {
				getCache: jest.fn(() => cache),
				getAggregatedStats: jest.fn(() => root),
			},
			saveSettings: jest.fn(),
			requestSaveUiState: jest.fn(),
		};
		view = new DashboardView({} as any, plugin);
		container = createMockElement('div');
		view.renderOverview(container);
		// The row's parent finds the overview element the way the DOM would.
		container.createDiv.mock.results[0].value.closest = jest.fn(() => container);
	});

	/** The header and children element of the top-level deck row. */
	function deckRow() {
		const list = container.createDiv.mock.results[0].value;
		const deckContainer = list.createDiv.mock.results[0].value;
		return {
			header: deckContainer.createDiv.mock.results[0].value,
			body: deckContainer.createDiv.mock.results[1].value,
		};
	}

	test('a click persists through the debounced UI save, never saveSettings', () => {
		deckRow().header.onclick();
		expect(plugin.requestSaveUiState).toHaveBeenCalledTimes(1);
		expect(plugin.saveSettings).not.toHaveBeenCalled();
		expect(plugin.settings.ui_expanded_decks).toEqual(['Math']);

		deckRow().header.onclick();
		expect(plugin.settings.ui_expanded_decks).toEqual([]);
	});

	test('a click rebuilds only the subtree and reuses the aggregated tree', () => {
		const { header, body } = deckRow();
		header.onclick();

		expect(container.empty).toHaveBeenCalledTimes(1); // the first render only
		expect(body.empty).toHaveBeenCalledTimes(1);
		expect(body.createDiv).toHaveBeenCalled(); // the child row
		expect(plugin.statsService.getAggregatedStats).toHaveBeenCalledTimes(1);

		view.renderOverview(container);
		expect(plugin.statsService.getAggregatedStats).toHaveBeenCalledTimes(1);
	});

	test('after a stats refresh a click rebuilds the tree with the new numbers', () => {
		cache.concepts = { 'Note.md': { cardStats: {} } }; // refreshStats replaces the map
		deckRow().header.onclick();

		expect(container.empty).toHaveBeenCalledTimes(2);
		expect(plugin.statsService.getAggregatedStats).toHaveBeenCalledTimes(2);
	});
});

/** A dashboard over a mock container, with the native components recorded. */
function mount(over: { concepts?: Record<string, any>; plugin?: Record<string, any> } = {}) {
	const rec = installNativeObsidian();
	const cache = { concepts: over.concepts ?? {}, lastFetched: 1 };
	const plugin: any = {
		app: {},
		settings: { ...DEFAULT_SETTINGS, ui_expanded_decks: [], ui_expanded_concepts: [] },
		statsService: {
			getCache: jest.fn(() => cache),
			getAggregatedStats: jest.fn(() => node({ children: [] })),
			refreshStats: jest.fn().mockResolvedValue(undefined),
		},
		saveStats: jest.fn().mockResolvedValue(undefined),
		saveSettings: jest.fn(),
		requestSaveUiState: jest.fn(),
		leechService: { getLeeches: jest.fn((): any[] => []), suspendCard: jest.fn() },
		linkCheckerService: { checkIntegrity: jest.fn().mockResolvedValue([]) },
		areteClient: { getDeckNames: jest.fn().mockResolvedValue([]) },
		...over.plugin,
	};
	const view: any = new DashboardView({} as any, plugin);
	const container = createMockElement('div');
	view.containerEl = { children: [null, container] };
	return { view, container, plugin, rec, cache };
}

describe('DashboardView shell', () => {
	it('names the view in sentence case', () => {
		const { view } = mount();
		expect(view.getDisplayText()).toBe('Arete dashboard');
	});

	it('draws the header and tabs with classes and no inline style', async () => {
		const m = mount({
			concepts: { 'A.md': { totalCards: 4, problematicCardsCount: 2, cardStats: {} } },
		});
		await m.view.render();
		expect(m.container.addClass).toHaveBeenCalledWith('arete-dashboard-container');
		expect(m.container.style).toEqual({});
		const content = walk(m.container).find((w) =>
			w.classes.includes('arete-dashboard-content'),
		);
		expect(content).toBeDefined();
		expect(inlineStyled(m.container)).toEqual([]);
	});

	it('writes header text and tab labels in sentence case', async () => {
		const m = mount();
		await m.view.render();
		const all = texts(m.container);
		expect(all).toEqual(
			expect.arrayContaining([
				'Arete dashboard',
				'Overview',
				'Leeches',
				'Integrity',
				'Queue builder',
			]),
		);
		expect(all).not.toContain('Queue Builder');
		expect(all).not.toContain('Arete Dashboard');
	});

	it('flags the issue count with mod-error only when there are issues', async () => {
		const withIssues = mount({
			concepts: { 'A.md': { totalCards: 4, problematicCardsCount: 2, cardStats: {} } },
		});
		await withIssues.view.render();
		const span = walk(withIssues.container).find((w) => w.text === '2 issues')!;
		expect(span.el.addClass).toHaveBeenCalledWith('mod-error');
		const none = mount();
		await none.view.render();
		const zero = walk(none.container).find((w) => w.text === '0 issues')!;
		expect(zero.el.addClass).not.toHaveBeenCalled();
	});

	it('marks the active tab with is-active and no inline colour', async () => {
		const m = mount();
		await m.view.render();
		const tabs = walk(m.container).filter((w) => w.classes.includes('arete-tab-btn'));
		expect(tabs).toHaveLength(4);
		expect(tabs[0].el.addClass).toHaveBeenCalledWith('is-active');
		expect(tabs[1].el.addClass).not.toHaveBeenCalled();
		expect(tabs.every((t) => Object.keys(t.el.style).length === 0)).toBe(true);
	});

	it('refreshes through an ExtraButtonComponent with a tooltip, not a title', async () => {
		const m = mount();
		await m.view.render();
		expect(m.rec.extraButtons).toHaveLength(1);
		expect(m.rec.extraButtons[0].icon).toBe('refresh-cw');
		expect(m.rec.extraButtons[0].tooltip).toBe('Sync and refresh stats');
		const render = jest.spyOn(m.view, 'render').mockResolvedValue(undefined);
		await m.rec.extraButtons[0].onClick!();
		expect(m.plugin.statsService.refreshStats).toHaveBeenCalledTimes(1);
		expect(m.plugin.saveStats).toHaveBeenCalledTimes(1);
		expect(render).toHaveBeenCalledTimes(1);
	});

	it('shows empty and success states with icons and classes', () => {
		const m = mount();
		const el = createMockElement('div');
		m.view.renderEmptyState(el, 'Nothing here.');
		m.view.renderSuccess(el, 'All good.');
		const classes = walk(el).flatMap((w) => w.classes);
		expect(classes).toEqual(
			expect.arrayContaining([
				'arete-empty-state',
				'arete-success-state',
				'arete-empty-message',
			]),
		);
		expect(m.rec.icons.map((i) => i.icon)).toEqual(['search', 'check-circle']);
		expect(inlineStyled(el)).toEqual([]);
	});
});

describe('DashboardView overview', () => {
	const cls = (w: { classes: string[] }) => w.classes.join(' ');
	const byClass = (root: any, c: string) => walk(root).filter((w) => w.classes.includes(c));

	function renderNode(over: Partial<StatsNode>, depth = 0) {
		const m = mount();
		const el = createMockElement('div');
		m.view.renderDeckNode(el, node({ title: 'Math', deckName: 'Math', ...over }), depth);
		return { ...m, el };
	}

	it('gives a deck header a class and indents it with the only inline style', () => {
		const { el } = renderNode({}, 2);
		const [header] = byClass(el, 'arete-deck-header');
		expect(header).toBeDefined();
		expect(header.el.style).toEqual({ paddingLeft: '40px' });
		const others = inlineStyled(el).filter((w) => w.el !== header.el);
		expect(others).toEqual([]);
	});

	it('flags each deck figure from the shared cut-offs', () => {
		const { el } = renderNode({
			difficulty: 0.75,
			stability: 5,
			retrievability: 0.8,
			lapses: 3,
		});
		const flags = Object.fromEntries(
			byClass(el, 'arete-deck-stat').map((w) => [
				w.classes.find((c) => c.startsWith('mod-') && !/warning|error/.test(c)),
				w.classes.filter((c) => c === 'mod-warning' || c === 'mod-error'),
			]),
		);
		expect(flags).toEqual({
			'mod-count': [],
			'mod-difficulty': ['mod-warning'],
			'mod-stability': ['mod-warning'],
			'mod-retrievability': ['mod-warning'],
			'mod-lapses': ['mod-error'],
		});
	});

	it('leaves healthy figures unflagged and shows a dash for missing ones', () => {
		const { el } = renderNode({
			difficulty: 0.3,
			stability: 30,
			retrievability: 0.95,
			lapses: 0,
		});
		expect(byClass(el, 'arete-deck-stat').flatMap((w) => w.classes)).not.toEqual(
			expect.arrayContaining(['mod-warning']),
		);
		const missing = renderNode({});
		expect(texts(missing.el)).toEqual(expect.arrayContaining(['-']));
	});

	it('tints a deck with issues and badges the count with mod-error', () => {
		const { el } = renderNode({ problematicCount: 2 });
		const [header] = byClass(el, 'arete-deck-header');
		expect(header.el.addClass).toHaveBeenCalledWith('mod-error');
		const badge = walk(el).find((w) => w.text === '2 issues')!;
		expect(cls(badge)).toBe('arete-badge mod-error');
		expect(badge.el.style).toEqual({});
	});

	it('uses lowercase figure suffixes', () => {
		const { el } = renderNode({
			difficulty: 0.5,
			stability: 10,
			retrievability: 0.9,
			lapses: 1,
			count: 3,
		});
		expect(texts(el)).toEqual(
			expect.arrayContaining(['3 cards', '5.0 D', '10.0d stab', '90% ret', '1 laps']),
		);
	});

	describe('card rows', () => {
		const stat = (over = {}) => ({
			cardId: 7,
			noteId: 7,
			lapses: 4,
			ease: 2500,
			difficulty: 0.95,
			stability: 5,
			retrievability: 0.5,
			intervalGrowth: 1.5,
			deckName: 'Math',
			interval: 1,
			due: 0,
			reps: 3,
			averageTime: 1,
			front: '<b>What?</b>',
			...over,
		});

		function renderList(card: any) {
			const m = mount({ concepts: { 'Note.md': {} } });
			const el = createMockElement('div');
			m.view.renderCardList(el, { filePath: 'Note.md', cardStats: { 7: card } } as any);
			return { ...m, el };
		}

		it('hovers with :hover, not mouse handlers', () => {
			const { el } = renderList(stat());
			const [row] = byClass(el, 'arete-card-row');
			const [q] = byClass(el, 'arete-card-question').filter((w) =>
				w.classes.includes('arete-clickable'),
			);
			for (const w of [row, q, ...byClass(el, 'arete-card-stats-group')]) {
				expect(w.el.addEventListener).not.toHaveBeenCalled();
			}
		});

		it('sets no inline style on any row element', () => {
			const { el } = renderList(stat({ isOverlearning: true }));
			expect(inlineStyled(el)).toEqual([]);
		});

		it('flags the columns from the shared cut-offs', () => {
			const { el } = renderList(stat());
			const cols = Object.fromEntries(
				byClass(el, 'arete-card-col')
					.filter((w) => w.text !== undefined && w.classes.length > 0)
					.slice(6) // the six header cells come first
					.map((w) => [
						w.classes.find((c) =>
							/^mod-(difficulty|stability|growth|retrievability|lapses|flags)$/.test(
								c,
							),
						),
						w.classes.filter((c) => /^mod-(success|warning|error)$/.test(c)),
					]),
			);
			expect(cols).toEqual({
				'mod-difficulty': ['mod-error'], // 0.95 >= 0.9
				'mod-stability': ['mod-warning'], // 5 < 7
				'mod-growth': ['mod-success'], // 1.5 >= 1.2
				'mod-retrievability': ['mod-warning'], // 0.5 < 0.85, never an error
				'mod-lapses': ['mod-error'], // 4 >= 3
				'mod-flags': [],
			});
		});

		it('follows the lapse threshold in settings', () => {
			const lenient = mount({
				concepts: { 'Note.md': {} },
				plugin: {
					settings: {
						...DEFAULT_SETTINGS,
						stats_lapse_threshold: 6,
						ui_expanded_decks: [],
						ui_expanded_concepts: [],
					},
				},
			});
			const el = createMockElement('div');
			lenient.view.renderCardList(el, {
				filePath: 'Note.md',
				cardStats: { 7: stat() },
			} as any);
			const lapses = byClass(el, 'arete-card-col')
				.filter((w) => w.classes.includes('mod-lapses'))
				.pop()!;
			expect(lapses.classes).toContain('mod-warning');
			expect(lapses.classes).not.toContain('mod-error');
		});

		it('shows overlearning with a zap icon and a tooltip, not the text OL', () => {
			const { el, rec } = renderList(stat({ isOverlearning: true }));
			expect(rec.icons.map((i) => i.icon)).toContain('zap');
			expect(rec.tooltips.map((t) => t.text)).toContain('Overlearning detected');
			expect(texts(el)).not.toContain('OL');
		});

		it('sets tooltips with setTooltip and sentence case headers', () => {
			const { el, rec } = renderList(stat());
			expect(rec.tooltips.map((t) => t.text)).toEqual(
				expect.arrayContaining(['What?', 'Flags, such as overlearning']),
			);
			expect(texts(el)).toEqual(expect.arrayContaining(['Card question']));
			expect(texts(el)).not.toContain('Card Question');
		});

		it('opens the stats modal with the plugin settings on a stats click', () => {
			const { el } = renderList(stat());
			const [group] = byClass(el, 'arete-card-stats-group');
			const e = { stopPropagation: jest.fn() };
			expect(() => group.el.onclick(e)).not.toThrow();
			expect(e.stopPropagation).toHaveBeenCalled();
		});
	});
});

describe('DashboardView leeches', () => {
	const leech = (over = {}) => ({
		front: '<i>Define a port</i>',
		cardId: 11,
		filePath: 'Note.md',
		lapses: 4,
		ease: 0,
		difficulty: 0.95,
		...over,
	});

	async function renderLeeches(leeches: any[], suspend = jest.fn().mockResolvedValue(true)) {
		const m = mount({
			plugin: { leechService: { getLeeches: jest.fn(() => leeches), suspendCard: suspend } },
		});
		const el = createMockElement('div');
		await m.view.renderLeeches(el);
		return { ...m, el, suspend };
	}

	it('builds the table with classes and no inline style', async () => {
		const { el } = await renderLeeches([leech(), leech({ cardId: 12, lapses: 1 })]);
		expect(inlineStyled(el)).toEqual([]);
		const classes = walk(el).flatMap((w) => w.classes);
		expect(classes).toEqual(
			expect.arrayContaining([
				'arete-leech-table',
				'arete-leech-front',
				'arete-leech-actions',
			]),
		);
	});

	it('writes the column headers in sentence case', async () => {
		const { el } = await renderLeeches([leech()]);
		expect(texts(el)).toEqual(
			expect.arrayContaining(['Card (front)', 'Difficulty', 'Lapses', 'Actions']),
		);
		expect(texts(el)).not.toContain('Card (Front)');
	});

	it('flags lapses with the shared cut-offs', async () => {
		const { el } = await renderLeeches([
			leech({ lapses: 0 }),
			leech({ lapses: 2 }),
			leech({ lapses: 3 }),
		]);
		const tds = walk(el).filter((w) => w.tag === 'td');
		const lapseCells = tds.filter((w) => /^[0-9]$/.test(w.el.textContent ?? ''));
		const flags = lapseCells.map((w) => (w.el.addClass.mock.calls[0] ?? [null])[0]);
		expect(flags).toEqual([null, 'mod-warning', 'mod-error']);
	});

	it('sets the tooltip with setTooltip and shows the front without markup', async () => {
		const { el, rec } = await renderLeeches([leech()]);
		expect(rec.tooltips.map((t) => t.text)).toContain('<i>Define a port</i>');
		const front = walk(el).find((w) => w.classes.includes('arete-leech-front'))!;
		expect(front.el.textContent).toBe('Define a port');
		expect(front.el.title).toBeUndefined();
	});

	it('suspends through a ButtonComponent and marks the row is-suspended', async () => {
		const { rec, suspend, el } = await renderLeeches([leech()]);
		expect(rec.buttons).toHaveLength(1);
		expect(rec.buttons[0].text).toBe('Suspend');
		await rec.buttons[0].onClick!();
		expect(suspend).toHaveBeenCalledWith(11);
		expect(rec.buttons[0].text).toBe('Suspended');
		expect(rec.buttons[0].disabled).toBe(true);
		const row = walk(el).find((w) => w.tag === 'tr' && w.el.addClass.mock.calls.length)!;
		expect(row.el.addClass).toHaveBeenCalledWith('is-suspended');
		expect(row.el.style).toEqual({});
	});

	it('restores the button when suspending fails', async () => {
		const { rec } = await renderLeeches([leech()], jest.fn().mockResolvedValue(false));
		await rec.buttons[0].onClick!();
		expect(rec.buttons[0].text).toBe('Suspend');
		expect(rec.buttons[0].disabled).toBe(false);
	});
});

describe('DashboardView integrity', () => {
	const ref = (over = {}) => ({
		sourceFile: { basename: 'Note' },
		linkText: '![[pic.png]]',
		linkPath: 'pic.png',
		type: 'image',
		position: null as null,
		...over,
	});

	function renderIntegrity(over: { refs?: any[] | null; scanning?: boolean } = {}) {
		const m = mount();
		m.view.app = {
			vault: { getMarkdownFiles: jest.fn(() => [{}, {}]) },
			workspace: { getLeaf: jest.fn() },
		};
		m.view.brokenRefs = over.refs ?? null;
		m.view.isScanning = over.scanning ?? false;
		const el = createMockElement('div');
		m.view.renderIntegrity(el);
		return { ...m, el };
	}

	it('explains the scan with elements, not innerHTML, and sets no inline style', () => {
		const { el } = renderIntegrity({
			refs: [ref(), ref({ type: 'invalid-yaml', errorMessage: 'bad' })],
		});
		expect(inlineStyled(el)).toEqual([]);
		const strong = walk(el)
			.filter((w) => w.tag === 'strong')
			.map((w) => w.text);
		expect(strong).toEqual(['broken image embeds', 'invalid YAML']);
		expect(walk(el).every((w) => w.el.innerHTML === undefined)).toBe(true);
	});

	it('runs the check from a call-to-action ButtonComponent', async () => {
		const { rec, view, plugin } = renderIntegrity();
		expect(rec.buttons).toHaveLength(1);
		expect(rec.buttons[0]).toMatchObject({
			text: 'Run integrity check',
			cta: true,
			disabled: false,
		});
		const render = jest.spyOn(view, 'render').mockResolvedValue(undefined);
		plugin.linkCheckerService.checkIntegrity.mockResolvedValue([ref()]);
		await rec.buttons[0].onClick!();
		expect(plugin.linkCheckerService.checkIntegrity).toHaveBeenCalledWith([{}, {}]);
		expect(view.brokenRefs).toHaveLength(1);
		expect(view.isScanning).toBe(false);
		expect(render).toHaveBeenCalledTimes(2);
	});

	it('disables the button and says so while scanning', () => {
		const { rec } = renderIntegrity({ scanning: true });
		expect(rec.buttons[0]).toMatchObject({ text: 'Scanning vault...', disabled: true });
	});

	it('logs a failed check with the [Arete] prefix and keeps the notice plain', async () => {
		const { rec, view, plugin } = renderIntegrity();
		jest.spyOn(view, 'render').mockResolvedValue(undefined);
		const error = jest.spyOn(console, 'error').mockImplementation(() => undefined);
		const failure = new Error('boom');
		plugin.linkCheckerService.checkIntegrity.mockRejectedValue(failure);
		await rec.buttons[0].onClick!();
		expect(error).toHaveBeenCalledWith('[Arete] Integrity check failed:', failure);
		error.mockRestore();
	});

	it('labels badges in sentence case and flags invalid YAML with mod-error', () => {
		const { el, rec } = renderIntegrity({
			refs: [
				ref(),
				ref({ type: 'link' }),
				ref({ type: 'invalid-yaml', errorMessage: 'bad' }),
			],
		});
		const badges = walk(el).filter((w) => w.classes.includes('arete-badge'));
		expect(badges.map((b) => b.text)).toEqual(['Broken image', 'Broken link', 'Invalid YAML']);
		expect(badges.map((b) => b.classes.includes('mod-error'))).toEqual([false, false, true]);
		expect(rec.tooltips.map((t) => t.text)).toContain('bad');
	});

	it('goes to the location through an ExtraButtonComponent with a tooltip', () => {
		const { rec, view } = renderIntegrity({ refs: [ref()] });
		expect(rec.extraButtons).toHaveLength(1);
		expect(rec.extraButtons[0]).toMatchObject({
			icon: 'external-link',
			tooltip: 'Go to location',
		});
		const go = jest.spyOn(view, 'goToIssue').mockResolvedValue(undefined);
		rec.extraButtons[0].onClick!();
		expect(go).toHaveBeenCalledWith({ basename: 'Note' }, '![[pic.png]]');
	});

	it('shows the success state when nothing is broken', () => {
		const { el, rec } = renderIntegrity({ refs: [] });
		expect(walk(el).some((w) => w.classes.includes('arete-success-state'))).toBe(true);
		expect(rec.icons.map((i) => i.icon)).toContain('check-circle');
	});
});

describe('DashboardView queue builder', () => {
	const queue = {
		deck: 'Math',
		dueCount: 2,
		totalWithPrereqs: 3,
		queue: [
			{ id: 1, position: 1, isPrereq: true, title: 'Limits', file: 'Limits.md' },
			{ id: 2, position: 2, isPrereq: false, title: 'Derivatives', file: 'Derivatives.md' },
		],
	};

	function renderQueue(build = jest.fn().mockResolvedValue(queue)) {
		const m = mount({
			plugin: {
				areteClient: {
					getDeckNames: jest.fn().mockResolvedValue(['Math']),
					buildStudyQueue: build,
					createQueueDeck: jest.fn().mockResolvedValue(true),
				},
			},
		});
		const el = createMockElement('div');
		m.view.renderQueueBuilder(el);
		return { ...m, el, build };
	}

	it('labels the controls in sentence case and styles them with classes', () => {
		const { el } = renderQueue();
		expect(texts(el)).toEqual(
			expect.arrayContaining([
				'Queue builder',
				'Deck filter',
				'Prerequisite depth',
				'Max cards',
				'All decks',
			]),
		);
		expect(texts(el).join('|')).not.toMatch(/Queue Builder|Deck Filter|Max Cards|All Decks/);
		expect(inlineStyled(el)).toEqual([]);
	});

	it('builds from a call-to-action button, disabling it while it works', async () => {
		const { rec, build } = renderQueue();
		expect(rec.buttons).toHaveLength(1);
		expect(rec.buttons[0]).toMatchObject({ text: 'Build queue', cta: true });
		const pending = rec.buttons[0].onClick!();
		expect(rec.buttons[0]).toMatchObject({ text: 'Building...', disabled: true });
		await pending;
		expect(build).toHaveBeenCalledWith(null, 2, 50);
		expect(rec.buttons[0]).toMatchObject({ text: 'Build queue', disabled: false });
	});

	it('lists the queue with classes, a Prerequisite badge and a native send button', async () => {
		const { rec, el } = renderQueue();
		await rec.buttons[0].onClick!();
		expect(rec.buttons.map((b) => b.text)).toEqual(['Build queue', 'Send to Anki']);
		const all = walk(el);
		const badges = all.filter((w) => w.classes.includes('arete-badge'));
		expect(badges.map((b) => [b.text, b.classes.includes('mod-accent')])).toEqual([
			['Prerequisite', true],
		]);
		expect(all.filter((w) => w.classes.includes('arete-queue-item'))).toHaveLength(2);
		expect(inlineStyled(el)).toEqual([]);
	});

	it('sends the queue ids to Anki and tells the user in a plain sentence', async () => {
		const { rec, plugin } = renderQueue();
		await rec.buttons[0].onClick!();
		await rec.buttons[1].onClick!();
		expect(plugin.areteClient.createQueueDeck).toHaveBeenCalledWith([1, 2]);
	});

	it('logs a failed build with the [Arete] prefix and restores the button', async () => {
		const failure = new Error('down');
		const { rec } = renderQueue(jest.fn().mockRejectedValue(failure));
		const error = jest.spyOn(console, 'error').mockImplementation(() => undefined);
		await rec.buttons[0].onClick!();
		expect(error).toHaveBeenCalledWith('[Arete] Failed to build the queue:', failure);
		expect(rec.buttons[0]).toMatchObject({ text: 'Build queue', disabled: false });
		error.mockRestore();
	});
});
