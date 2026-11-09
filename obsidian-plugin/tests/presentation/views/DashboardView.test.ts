import '../../test-setup';
import { createMockElement } from '../../test-setup';
import { DashboardView } from '@/presentation/views/DashboardView';
import { StatsNode } from '@/domain/stats';

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
