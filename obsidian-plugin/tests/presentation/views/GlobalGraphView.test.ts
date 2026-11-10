import { GlobalGraphView } from '@/presentation/views/GlobalGraphView';
import { GlobalGraphResult } from '@/domain/graph/types';
import { createMockElement as baseElement } from '../../test-setup';

/** The shared mock element, plus `setAttribute`, for the toolbar code. */
function createMockElement(tag?: string, opts?: any): any {
	const el = baseElement(tag, opts);
	el.setAttribute = jest.fn();
	el.toggleClass = jest.fn((cls: string, on: boolean) => {
		el.classes = new Set(el.classes ?? []);
		if (on) el.classes.add(cls);
		else el.classes.delete(cls);
	});
	el.createDiv = jest.fn((o?: any) => createMockElement('div', o));
	el.createEl = jest.fn((t: string, o?: any) => createMockElement(t, o));
	el.createSpan = jest.fn((o?: any) => createMockElement('span', o));
	return el;
}

// The shared obsidian mock has no setTooltip or ButtonComponent; the view calls both.
beforeAll(() => {
	const obsidian = jest.requireMock('obsidian');
	obsidian.setTooltip = jest.fn();
	obsidian.ButtonComponent = class {
		setButtonText() {
			return this;
		}
		setClass() {
			return this;
		}
		onClick() {
			return this;
		}
	};
});

/** Every element created under `el`, depth first, in creation order. */
function created(el: any): any[] {
	const out: any[] = [];
	const calls: Array<{ order: number; value: any }> = [];
	for (const fn of [el.createDiv, el.createEl, el.createSpan]) {
		const mock = fn?.mock;
		if (!mock) continue;
		mock.results.forEach((r: any, i: number) =>
			calls.push({ order: mock.invocationCallOrder[i], value: r.value }),
		);
	}
	calls.sort((a, b) => a.order - b.order);
	for (const { value: child } of calls) {
		if (!child) continue;
		out.push(child);
		out.push(...created(child));
	}
	return out;
}

const card = (id: string, file = 'F.md') => ({
	id,
	title: `title ${id}`,
	filePath: file,
	lineNumber: 1,
});

function graph(): GlobalGraphResult {
	return {
		files: [{ path: 'F.md', basename: 'F', cardCount: 4, cardIds: ['B', 'A', 'C', 'D'] }],
		cards: [card('A'), card('B'), card('C'), card('D')],
		requiresEdges: [
			{ type: 'requires', fromId: 'A', toId: 'C' },
			{ type: 'requires', fromId: 'A', toId: 'B' },
			{ type: 'requires', fromId: 'D', toId: 'A' },
			{ type: 'requires', fromId: 'B', toId: 'A' },
			{ type: 'requires', fromId: 'A', toId: 'missing' },
		],
		relatedEdges: [
			{ type: 'related', fromId: 'C', toId: 'A' },
			{ type: 'related', fromId: 'A', toId: 'D' },
		],
	};
}

async function makeView(g: GlobalGraphResult) {
	const resolver = {
		buildGraph: jest.fn().mockResolvedValue(undefined),
		getGlobalGraph: () => g,
	};
	const view: any = new GlobalGraphView({} as any, { dependencyResolver: resolver as any });
	await view.loadData();
	view.detailPanel = createMockElement('div');
	return view;
}

describe('GlobalGraphView detail panel', () => {
	it('lists prerequisites, dependents and related in edge order without scanning cards', async () => {
		const g = graph();
		const view = await makeView(g);
		view.showRelated = true;
		const find = jest.spyOn(g.cards, 'find');
		view.renderCardDetail({ ...card('A'), radius: 5, fileHue: 0, type: 'card' });
		expect(find).not.toHaveBeenCalled();
		const anchors = created(view.detailPanel)
			.filter((e) => e.tag === 'a')
			.map((e) => e.text);
		expect(anchors).toEqual([
			'F.md', // parent file link
			'title C', // prerequisites, missing id dropped
			'title B',
			'title D', // dependents
			'title B',
			'title C', // related
			'title D',
		]);
	});

	it('lists a file’s cards in file order without scanning cards', async () => {
		const g = graph();
		const view = await makeView(g);
		const find = jest.spyOn(g.cards, 'find');
		view.renderFileDetail({
			id: 'F.md',
			basename: 'F',
			cardCount: 4,
			cardIds: [],
			radius: 8,
			type: 'file',
		});
		expect(find).not.toHaveBeenCalled();
		const anchors = created(view.detailPanel)
			.filter((e) => e.tag === 'a')
			.map((e) => e.text);
		expect(anchors).toEqual(['title B', 'title A', 'title C', 'title D']);
	});
});

describe('GlobalGraphView hover index', () => {
	const id = (end: any) => (typeof end === 'string' ? end : end.id);

	/** The per-hover loops the index replaced. */
	function oldConnected(links: any[], hovered: string): Set<string> {
		const connected = new Set<string>([hovered]);
		for (const l of links) {
			if (id(l.source) === hovered) connected.add(id(l.target));
			if (id(l.target) === hovered) connected.add(id(l.source));
		}
		return connected;
	}
	function oldLitLinks(links: any[], hovered: string): number[] {
		return links
			.map((l, i) => (id(l.source) === hovered || id(l.target) === hovered ? i : -1))
			.filter((i) => i >= 0);
	}

	it('gives the same neighbours and lit links as the per-hover scan', () => {
		const view: any = new GlobalGraphView({} as any, { dependencyResolver: {} as any });
		const ids = Array.from({ length: 20 }, (_, i) => `n${i}`);
		let s = 7;
		const rand = () => ((s = (s * 1664525 + 1013904223) >>> 0) / 2 ** 32) * ids.length;
		const links = Array.from({ length: 60 }, (_, i) => {
			const a = ids[Math.floor(rand())];
			const b = i % 10 === 0 ? a : ids[Math.floor(rand())]; // some self-links
			// d3's forceLink swaps ids for node objects; cover both forms
			return {
				source: i % 2 ? a : { id: a },
				target: i % 3 ? b : { id: b },
				edgeType: 'requires',
				weight: 1,
			};
		});
		const { neighbours, incident } = view.indexLinks(links);
		for (const n of [...ids, 'isolated']) {
			const connected = new Set<string>([n, ...(neighbours.get(n) ?? [])]);
			expect(connected).toEqual(oldConnected(links, n));
			expect(incident.get(n) ?? []).toEqual(oldLitLinks(links, n));
		}
	});
});

describe('GlobalGraphView search and resize', () => {
	const d3 = jest.requireMock('d3');

	/** A stand-in for a d3 selection: records the last value set per attribute, per datum. */
	function fakeSelection(data: any[]) {
		const attrs = new Map<string, any[]>();
		const sel: any = {
			data: () => data,
			attr: (name: string, v: any) => {
				attrs.set(
					name,
					data.map((d) => (typeof v === 'function' ? v(d) : v)),
				);
				return sel;
			},
			attrs,
		};
		return sel;
	}
	function fakeNodeGroup(data: any[]) {
		const children = new Map<string, any>();
		return {
			data: () => data,
			select: (sel: string) => {
				if (!children.has(sel)) children.set(sel, fakeSelection(data));
				return children.get(sel);
			},
			children,
		};
	}

	afterEach(() => jest.useRealTimers());

	it('applies a search after a pause, without re-rendering or a new simulation', async () => {
		jest.useFakeTimers();
		const view: any = new GlobalGraphView({} as any, { dependencyResolver: {} as any });
		view.toolbarEl = createMockElement('div');
		view.renderToolbar();
		const input = view.toolbarEl.createEl.mock.results.find(
			(r: any) => r.value.tag === 'input',
		).value;
		const render = jest.spyOn(view, 'render').mockImplementation(() => undefined);
		const applySearch = jest.spyOn(view, 'applySearch');
		d3.forceSimulation.mockClear();
		for (const value of ['a', 'al', 'Alg']) {
			input.value = value;
			input._listeners.input();
		}
		expect(applySearch).not.toHaveBeenCalled();
		jest.advanceTimersByTime(200);
		expect(applySearch).toHaveBeenCalledTimes(1);
		expect(view.searchQuery).toBe('alg');
		expect(render).not.toHaveBeenCalled();
		expect(d3.forceSimulation).not.toHaveBeenCalled();
	});

	it('restyles with the opacities a full render used', () => {
		const view: any = new GlobalGraphView({} as any, { dependencyResolver: {} as any });
		const files = [
			{ id: 'math/Algebra.md', basename: 'Algebra' },
			{ id: 'Calculus.md', basename: 'Calculus' },
		];
		const nodeGroup = fakeNodeGroup(files);
		const link = fakeSelection([{}, {}]);
		view.searchTargets = { nodeGroup, link, isFileMode: true };
		view.searchQuery = 'alg';
		view.applySearch();
		expect(nodeGroup.select('circle').attrs.get('opacity')).toEqual([1, 0.15]);
		expect(nodeGroup.select('.arete-global-graph-node-label').attrs.get('opacity')).toEqual([
			1, 0.15,
		]);
		expect(link.attrs.get('stroke-opacity')).toEqual([0.1, 0.1]);
		view.searchQuery = 'no such note';
		view.applySearch();
		expect(nodeGroup.select('circle').attrs.get('opacity')).toEqual([1, 1]);
		expect(link.attrs.get('stroke-opacity')).toEqual([0.4, 0.4]);

		const cards = [
			{ id: 'arete_1', title: 'Group', filePath: 'Algebra.md' },
			{ id: 'arete_2', title: 'Ring', filePath: 'Rings.md' },
		];
		const cardGroup = fakeNodeGroup(cards);
		view.searchTargets = { nodeGroup: cardGroup, link, isFileMode: false };
		view.searchQuery = 'ring';
		view.applySearch();
		expect(cardGroup.select('circle').attrs.get('opacity')).toEqual([0.1, 1]);
		view.searchQuery = '';
		view.applySearch();
		expect(cardGroup.select('circle').attrs.get('opacity')).toEqual([0.85, 0.85]);
		expect(link.attrs.get('stroke-opacity')).toEqual([0.4, 0.4]);
	});

	it('renders on the first size, then only resizes the drawing, debounced', async () => {
		jest.useFakeTimers();
		let onResize: (entries: any[]) => void = () => undefined;
		(global as any).ResizeObserver = class {
			constructor(cb: any) {
				onResize = cb;
			}
			observe() {}
			disconnect() {}
		};
		const empty: GlobalGraphResult = {
			files: [],
			cards: [],
			requiresEdges: [],
			relatedEdges: [],
		};
		const view: any = await makeView(empty);
		view.app = new (jest.requireMock('obsidian').App)();
		view.registerEvent = jest.fn();
		view.contentEl = createMockElement('div');
		await view.onOpen();
		const render = jest.spyOn(view, 'render');
		const size = (width: number, height: number) =>
			onResize([{ contentRect: { width, height } }]);

		size(400, 300); // nothing drawn yet: draw now
		expect(render).toHaveBeenCalledTimes(1);

		const resizeViewport = jest.fn();
		view.resizeViewport = resizeViewport; // as a render that drew leaves it
		render.mockClear();
		size(410, 300);
		size(420, 300);
		size(430, 300);
		expect(resizeViewport).not.toHaveBeenCalled();
		jest.advanceTimersByTime(200);
		expect(resizeViewport).toHaveBeenCalledTimes(1);
		expect(render).not.toHaveBeenCalled();
	});
});
