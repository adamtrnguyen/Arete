/**
 * The graph views require 3d-force-graph (and with it three.js) on the first 3D render,
 * not when their module loads.
 */
describe('graph views load 3d-force-graph lazily', () => {
	const element = (): any => ({
		clientWidth: 400,
		clientHeight: 300,
		style: {},
		empty: jest.fn(),
		createDiv: jest.fn(() => element()),
	});

	/** Run `body` with fresh view modules and a counting 3d-force-graph mock. */
	function withViews(body: (views: { local: any; global: any }) => void): number {
		let loads = 0;
		jest.isolateModules(() => {
			jest.doMock('3d-force-graph', () => {
				loads++;
				// ForceGraph3D()(element) -> the jest mock's chainable graph instance.
				const instance = jest.requireActual('../../mocks/3d-force-graph.mock.ts').default;
				return () => () => instance();
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
		let graph3D: any;
		const loads = withViews(({ local }) => {
			const view = Object.create(local.prototype);
			view.container = element();
			view.graph3D = null;
			view.render3DGraph(element(), [], []);
			graph3D = view.graph3D;
		});
		expect(loads).toBe(1);
		expect(graph3D.graphData).toHaveBeenCalled();
	});

	it('requires it on the first global 3D render', () => {
		let graph3D: any;
		const loads = withViews(({ global }) => {
			const view = Object.create(global.prototype);
			view.graphContainer = element();
			view.graph3D = null;
			view.graphData = {
				files: [{ path: 'a.md', basename: 'a', cardCount: 1, cardIds: ['x'] }],
				cards: [{ id: 'x', title: 'x', filePath: 'a.md', lineNumber: 1 }],
				requiresEdges: [],
				relatedEdges: [],
			};
			view.renderFile3D();
			graph3D = view.graph3D;
		});
		expect(loads).toBe(1);
		expect(graph3D.graphData).toHaveBeenCalled();
	});
});
