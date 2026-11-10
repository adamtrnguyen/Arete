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
