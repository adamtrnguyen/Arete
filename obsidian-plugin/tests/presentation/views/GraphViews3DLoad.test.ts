import { LocalGraphView } from '@/presentation/views/LocalGraphView';
import { GlobalGraphView } from '@/presentation/views/GlobalGraphView';

const element = (): any => ({
	clientWidth: 400,
	clientHeight: 300,
	style: {},
	empty: jest.fn(),
	createDiv: jest.fn(() => element()),
});

const localView = (View: any) =>
	new View({}, { dependencyResolver: {}, getCardContext: jest.fn(), setCardContext: jest.fn() });

const globalView = (View: any) => {
	const view = new View({}, { dependencyResolver: {} });
	view.graphContainer = element();
	view.graphData = {
		files: [{ path: 'a.md', basename: 'a', cardCount: 1, cardIds: ['x'] }],
		cards: [{ id: 'x', title: 'x', filePath: 'a.md', lineNumber: 1 }],
		requiresEdges: [],
		relatedEdges: [],
	};
	return view;
};

/**
 * The graph views require 3d-force-graph (and with it three.js) on the first 3D render,
 * not when their module loads.
 */
describe('graph views load 3d-force-graph lazily', () => {
	/** Run `body` with fresh view modules and a counting 3d-force-graph mock. */
	function withViews(body: (views: { local: any; global: any }) => void): number {
		let loads = 0;
		jest.isolateModules(() => {
			jest.doMock('3d-force-graph', () => {
				loads++;
				return jest.requireActual('../../mocks/3d-force-graph.mock.ts');
			});
			body({
				local: jest.requireActual('@/presentation/views/LocalGraphView').LocalGraphView,
				global: jest.requireActual('@/presentation/views/GlobalGraphView').GlobalGraphView,
			});
		});
		return loads;
	}

	it('does not require it when the view modules load', () => {
		expect(withViews(() => undefined)).toBe(0);
	});

	it('requires it on the first local 3D render', () => {
		const loads = withViews(({ local }) => {
			const view = localView(local);
			view.container = element();
			view.render3DGraph(element(), [], []);
		});
		expect(loads).toBe(1);
	});

	it('requires it on the first global 3D render', () => {
		const loads = withViews(({ global }) => globalView(global).renderFile3D());
		expect(loads).toBe(1);
	});
});

/**
 * 3d-force-graph is ESM-only, so require() returns its namespace object, not the factory.
 * The mock has that shape; these render through it.
 */
describe('3D render with an ESM-shaped 3d-force-graph', () => {
	it('gets the namespace shape from require()', () => {
		expect(Object.keys(jest.requireActual('3d-force-graph'))).toEqual(['default']);
	});

	it('draws the local graph in 3D', () => {
		const view = localView(LocalGraphView);
		view.container = element();
		view.render3DGraph(element(), [], []);
		expect(view.graph3D.graphData).toHaveBeenCalled();
	});

	it('draws the global file and card graphs in 3D', () => {
		const files = globalView(GlobalGraphView);
		files.renderFile3D();
		expect(files.graph3D.graphData).toHaveBeenCalled();
		const cards = globalView(GlobalGraphView);
		cards.renderCard3D();
		expect(cards.graph3D.graphData).toHaveBeenCalled();
	});
});
