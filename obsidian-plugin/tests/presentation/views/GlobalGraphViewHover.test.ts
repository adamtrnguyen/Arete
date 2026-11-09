import { GlobalGraphView } from '@/presentation/views/GlobalGraphView';
import { createMockElement } from '../../test-setup';

jest.mock('d3', () => jest.requireActual('../../mocks/d3-chain'));

/**
 * Hover highlight, with a d3 stand-in that runs renderD3. A hover only toggles classes
 * (global-graph.css does the dimming) and never writes an opacity, so leaving a node shows
 * the styling from before the hover, search dimming included.
 */
describe('GlobalGraphView hover highlight', () => {
	const d3 = jest.requireMock('d3');
	const card = (id: string, filePath: string) => ({ id, title: id, filePath, lineNumber: 1 });

	function draw(searchQuery: string) {
		const view: any = new GlobalGraphView({} as any, { dependencyResolver: {} as any });
		view.graphContainer = createMockElement('div', { clientWidth: 400, clientHeight: 300 });
		view.graphData = {
			files: ['A.md', 'B.md', 'C.md'].map((path) => ({
				path,
				basename: path[0],
				cardCount: 1,
				cardIds: [path[0].toLowerCase()],
			})),
			cards: [card('a', 'A.md'), card('b', 'B.md'), card('c', 'C.md')],
			requiresEdges: [{ type: 'requires', fromId: 'a', toId: 'b' }],
			relatedEdges: [],
		};
		view.searchQuery = searchQuery;
		d3.boundSelections.length = 0;
		d3.selected.length = 0;
		view.render();
		const [links, nodes] = d3.boundSelections;
		const svg = d3.selected[0].node();
		const nodeEl = (id: string) =>
			nodes.nodes()[nodes.data().findIndex((n: any) => n.id === id)];
		return { links, nodes, svg, nodeEl };
	}

	it('marks the hovered node, its neighbours and its links, then clears them', () => {
		const { links, nodes, svg, nodeEl } = draw('a');
		const connected = (el: any) => el.classList.contains('arete-connected');
		d3.attrLog.length = 0;

		nodes.handlers.mouseover({}, nodes.data()[0]); // A.md, which requires B.md
		expect(svg.classList.contains('arete-hovering')).toBe(true);
		expect([
			connected(nodeEl('A.md')),
			connected(nodeEl('B.md')),
			connected(nodeEl('C.md')),
		]).toEqual([true, true, false]);
		expect(connected(links.nodes()[0])).toBe(true);

		nodes.handlers.mouseout();
		expect(svg.classList.contains('arete-hovering')).toBe(false);
		expect(['A.md', 'B.md', 'C.md'].map((id) => connected(nodeEl(id)))).toEqual([
			false,
			false,
			false,
		]);
		expect(connected(links.nodes()[0])).toBe(false);
		// The old mouseout wrote opacity 1 / 0.4 everywhere, erasing the search dimming.
		expect(d3.attrLog.filter((a: string) => a.includes('opacity'))).toEqual([]);
	});
});
