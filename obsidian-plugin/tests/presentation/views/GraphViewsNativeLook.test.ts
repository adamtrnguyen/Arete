import { LocalGraphView } from '@/presentation/views/LocalGraphView';
import { GlobalGraphView } from '@/presentation/views/GlobalGraphView';
import { createMockElement as baseElement } from '../../test-setup';

const fs = jest.requireActual('fs');
const path = jest.requireActual('path');
const read = (rel: string) => fs.readFileSync(path.join(__dirname, '../../../', rel), 'utf8');
const VIEWS = [
	'src/presentation/views/LocalGraphView.ts',
	'src/presentation/views/GlobalGraphView.ts',
];

function element(tag?: string, opts?: any): any {
	const el = baseElement(tag, { clientWidth: 400, clientHeight: 300, ...opts });
	el.classes = new Set<string>();
	el.toggleClass = jest.fn((cls: string, on: boolean) => {
		if (on) el.classes.add(cls);
		else el.classes.delete(cls);
	});
	el.createDiv = jest.fn((o?: any) => element('div', o));
	el.createEl = jest.fn((t: string, o?: any) => element(t, o));
	el.createSpan = jest.fn((o?: any) => element('span', o));
	return el;
}

let setTooltip: jest.Mock;
beforeEach(() => {
	setTooltip = jest.fn();
	jest.requireMock('obsidian').setTooltip = setTooltip;
});

const tips = () => setTooltip.mock.calls.map((c) => c[1]);

describe('graph views: source stays native', () => {
	it.each(VIEWS)('%s has no literal inline style, hex colour or d3 .style()', (file) => {
		const src = read(file);
		expect(src).not.toMatch(/\.style\.\w+\s*=\s*['"]/);
		expect(src).not.toMatch(/\.style\(/);
		expect(src).not.toMatch(/#[0-9a-fA-F]{6}\b/);
		expect(src).not.toMatch(/'(?:white|red)'/);
		expect(src).not.toMatch(/setAttribute\(\s*'title'/);
	});

	it.each(VIEWS)('%s logs only with the [Arete] prefix', (file) => {
		const src = read(file);
		expect(src).not.toMatch(/\[Arete (Graph|Server)/);
	});

	it.each(VIEWS)('%s does not import 3d-force-graph itself', (file) => {
		const src = read(file);
		expect(src).not.toMatch(/require\('3d-force-graph'\)/);
		expect(src).toMatch(/loadForceGraph3D/);
	});

	it('uses state classes in is-* form', () => {
		const css = read('src/styles/global-graph.css');
		const src = read(VIEWS[1]);
		expect(css + src).not.toMatch(/arete-(hovering|connected)/);
		expect(css).toMatch(/is-hovering/);
		expect(css).toMatch(/is-connected/);
	});

	it('styles graph colours with --graph-* variables and no transition: all', () => {
		const css = read('src/styles/global-graph.css');
		expect(css).toMatch(/var\(--graph-node\)/);
		expect(css).toMatch(/var\(--graph-line\)/);
		expect(css).toMatch(/var\(--graph-text\)/);
		expect(css).toMatch(/var\(--graph-node-focused\)/);
		expect(css).not.toMatch(/transition:\s*all/);
		expect(css).not.toMatch(/cursor:\s*pointer/);
		expect(css).not.toMatch(/-rgb/);
	});
});

describe('LocalGraphView native look', () => {
	function make() {
		const view: any = new LocalGraphView({} as any, {
			dependencyResolver: { buildGraph: jest.fn().mockResolvedValue(undefined) } as any,
			getCardContext: jest.fn(),
			setCardContext: jest.fn(),
		});
		view.container = element('div');
		view.refresh = jest.fn();
		return view;
	}

	it('names the view in sentence case', () => {
		expect(make().getDisplayText()).toBe('Arete local graph');
	});

	it('gives toolbar buttons setTooltip text in sentence case, not a title attribute', () => {
		const view = make();
		view.renderToolbar();
		expect(tips()).toEqual([
			'Rebuild graph',
			'Toggle card rendering',
			'Toggle related links',
			'Toggle 2D/3D view',
			'Toggle clustering by file',
		]);
		const buttons = view.container.createDiv.mock.results[0].value.createDiv.mock.results;
		for (const r of buttons) expect(r.value.setAttribute).toBeUndefined();
	});

	it('marks a toggle active with is-active and never writes an inline colour', () => {
		const view = make();
		view.renderToolbar();
		const toolbar = view.container.createDiv.mock.results[0].value;
		const toggles = toolbar.createDiv.mock.results.map((r: any) => r.value);
		const [, render, related, dimension, cluster] = toggles;
		render._listeners.click();
		expect(render.classes.has('is-active')).toBe(true);
		render._listeners.click();
		expect(render.classes.has('is-active')).toBe(false);
		related._listeners.click(); // showRelated starts true
		expect(related.classes.has('is-active')).toBe(false);
		dimension._listeners.click();
		expect(dimension.classes.has('is-active')).toBe(true);
		cluster._listeners.click();
		expect(cluster.classes.has('is-active')).toBe(true);
		for (const t of toggles) expect(t.style.color).toBeUndefined();
	});

	it('creates the tooltip container with a class, not inline styles', () => {
		const view = make();
		view.ensureTooltip();
		expect(view.container.createDiv).toHaveBeenCalledWith({ cls: 'arete-graph-tooltip' });
		expect(view.tooltipContainer.style).toEqual({});
	});

	it('paints 3D from the theme once per drawing and repaints in place on a theme change', () => {
		const view = make();
		const canvas = element('div');
		const win = {
			getComputedStyle: jest.fn((probe: any) => ({
				color: `rgb(0, 0, 0) /* ${probe.style.color} */`,
			})),
		};
		canvas.win = win;
		view.render3DGraph(canvas, [], []);
		const graph = view.graph3D;
		const resolvedOnce = win.getComputedStyle.mock.calls.length;
		expect(resolvedOnce).toBe(7);
		const accessor = graph.nodeColor.mock.calls.at(-1)[0];
		expect(accessor({ group: 'center' })).not.toBe(accessor({ group: 'prereq' }));
		const builds = graph.graphData.mock.calls.length;
		accessor({ group: 'related' });
		accessor({ group: 'dependent' });
		expect(win.getComputedStyle.mock.calls.length).toBe(resolvedOnce); // not per node

		view.repaint3D();
		expect(win.getComputedStyle.mock.calls.length).toBe(resolvedOnce * 2);
		expect(graph.graphData.mock.calls.length).toBe(builds); // not rebuilt
		expect(graph.backgroundColor).toHaveBeenCalledTimes(2);
	});

	it('forgets the repaint when the drawing is released', () => {
		const view = make();
		view.render3DGraph(element('div'), [], []);
		expect(view.repaint3D).not.toBeNull();
		view.releaseDrawing();
		expect(view.repaint3D).toBeNull();
	});

	it('styles the 3D hover card with classes', () => {
		const view = make();
		view.render3DGraph(element('div', { offsetLeft: 0, offsetTop: 0 }), [], []);
		view.graph3D.onNodeHover.mock.calls[0][0]({ id: 'a', title: 't', filePath: 'A.md' });
		const card = view.tooltipContainer.createDiv.mock.calls[0][0];
		expect(card.cls).toContain('mod-3d');
		expect(view.tooltipContainer.addClass).toHaveBeenCalledWith('is-visible');
	});
});

describe('GlobalGraphView native look', () => {
	function make() {
		const view: any = new GlobalGraphView({} as any, { dependencyResolver: {} as any });
		view.toolbarEl = element('div');
		view.graphContainer = element('div');
		view.detailPanel = element('div');
		return view;
	}

	it('names the view in sentence case', () => {
		expect(make().getDisplayText()).toBe('Arete global graph');
	});

	it('gives toolbar buttons setTooltip text in sentence case', () => {
		const view = make();
		view.renderToolbar();
		expect(tips()).toEqual([
			'Rebuild graph',
			'Switch to card mode',
			'Toggle related edges',
			'Toggle 2D/3D',
			'Fit to view',
		]);
	});

	it('marks active toggles with is-active at creation and no inline colour', () => {
		const view = make();
		view.viewMode = 'card';
		view.showRelated = true;
		view.renderMode = '3d';
		view.renderToolbar();
		const made = view.toolbarEl.createDiv.mock.calls.map((c: any) => c[0].cls);
		expect(made.filter((c: string) => c.includes('is-active'))).toHaveLength(4);
		for (const r of view.toolbarEl.createDiv.mock.results) {
			expect(r.value.style.color).toBeUndefined();
		}
		expect(tips()).toContain('Toggle clustering by file');
		expect(tips()).toContain('Switch to file mode');
	});

	it('paints 3D from the theme once per drawing and repaints in place on a theme change', () => {
		const view = make();
		view.graphData = {
			files: [{ path: 'a.md', basename: 'a', cardCount: 1, cardIds: ['x'] }],
			cards: [{ id: 'x', title: 'x', filePath: 'a.md', lineNumber: 1 }],
			requiresEdges: [],
			relatedEdges: [],
		};
		const win = { getComputedStyle: jest.fn(() => ({ color: 'rgb(9, 9, 9)' })) };
		view.graphContainer.win = win;
		view.renderFile3D();
		const graph = view.graph3D;
		expect(win.getComputedStyle).toHaveBeenCalledTimes(7);
		expect(graph.linkColor.mock.calls.at(-1)[0]()).toBe('rgb(9, 9, 9)');
		expect(win.getComputedStyle).toHaveBeenCalledTimes(7);
		view.repaint3D();
		expect(win.getComputedStyle).toHaveBeenCalledTimes(14);
		expect(graph.graphData).toHaveBeenCalledTimes(1);
		view.stopSimulation();
		expect(view.repaint3D).toBeNull();
	});
});
