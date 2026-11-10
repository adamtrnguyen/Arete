import { Component, MarkdownRenderer, TFile } from 'obsidian';
import { LocalGraphView } from '@/presentation/views/LocalGraphView';
import { createMockElement as baseElement } from '../../test-setup';

// A chainable stand-in for d3, enough to run renderD3Graph: every call returns the same
// selection, which records event handlers and runs `each` over the bound data.
jest.mock('d3', () => {
	const handlers: Record<string, (...args: any[]) => any> = {};
	let data: any[] = [];
	const sel: any = new Proxy(function () {}, {
		get(_t, k) {
			if (k === 'then') return undefined;
			if (k === 'on')
				return (name: string, fn: any) => {
					handlers[name] = fn;
					return sel;
				};
			if (k === 'data')
				return (d: any[]) => {
					data = d;
					return sel;
				};
			if (k === 'each')
				return (fn: any) => {
					data.forEach((d, i) => fn.call({}, d, i));
					return sel;
				};
			if (k === 'node') return () => (global as any).mockGraphElement();
			return () => sel;
		},
		apply: () => sel,
	});
	const make = () => sel;
	return {
		mockHandlers: handlers,
		select: make,
		zoom: make,
		drag: make,
		forceSimulation: make,
		forceLink: make,
		forceManyBody: make,
		forceCenter: make,
		forceCollide: make,
		forceX: make,
		forceY: make,
	};
});

/** The shared mock element, with the DOM members CardRenderer and the tooltip use. */
function element(tag?: string, opts?: any): any {
	const el = baseElement(tag, { clientWidth: 400, clientHeight: 300, children: [], ...opts });
	el.createDiv = jest.fn((o?: any) => {
		const child = element('div', o);
		el.children.push(child);
		return child;
	});
	el.createEl = jest.fn((t: string, o?: any) => element(t, o));
	el.createSpan = jest.fn((o?: any) => element('span', o));
	el.getBoundingClientRect = jest.fn(() => ({ left: 0, top: 0, width: 400, height: 300 }));
	return el;
}
(global as any).mockGraphElement = () => element('div');

/** Components still attached under `c`, at any depth. */
function liveChildren(c: Component): number {
	return ((c as any).children as Component[]).reduce((n, ch) => n + 1 + liveChildren(ch), 0);
}

const flush = () => new Promise((resolve) => setImmediate(resolve));

describe('LocalGraphView rendered markdown', () => {
	const d3 = jest.requireMock('d3');
	const node = {
		id: 'arete_A',
		title: '$x^2$ and **bold**',
		group: 'center' as const,
		filePath: 'Algebra.md',
		lineNumber: 1,
	};
	let view: any;

	beforeEach(() => {
		jest.clearAllMocks();
		// MarkdownRenderer.render hands what it renders to `component` to manage
		// (obsidian.d.ts); model that with one child component per call.
		(MarkdownRenderer.render as jest.Mock).mockImplementation(
			async (_app: any, _md: string, _el: any, _path: string, component: Component) => {
				component.addChild(new Component());
			},
		);
		const app = new (jest.requireMock('obsidian').App)();
		const file = Object.assign(new TFile(), { path: 'Algebra.md' });
		app.vault.getAbstractFileByPath = jest.fn(() => file);
		app.metadataCache.getFileCache.mockReturnValue({
			frontmatter: { cards: [{ id: 'arete_A', Front: 'F', Back: 'B' }] },
		});
		view = new LocalGraphView({} as any, {
			dependencyResolver: {} as any,
			getCardContext: jest.fn(),
			setCardContext: jest.fn(),
		});
		view.app = app;
		view.container = element('div');
	});

	it('renders a hover tooltip once per drawing, still as markdown', async () => {
		view.renderD3Graph(element('div'), [node], []);
		for (let i = 0; i < 5; i++) await d3.mockHandlers.mouseover({ pageX: 0, pageY: 0 }, node);
		expect(MarkdownRenderer.render).toHaveBeenCalledTimes(1);
		expect((MarkdownRenderer.render as jest.Mock).mock.calls[0][1]).toBe(node.title);
		expect(view.tooltipContainer.appendChild).toHaveBeenCalledTimes(4);

		view.renderD3Graph(element('div'), [node], []); // a new drawing renders afresh
		await d3.mockHandlers.mouseover({ pageX: 0, pageY: 0 }, node);
		expect(MarkdownRenderer.render).toHaveBeenCalledTimes(2);
	});

	it('keeps only the current drawing’s tooltip components', async () => {
		for (let i = 0; i < 3; i++) {
			view.renderD3Graph(element('div'), [node], []);
			for (let j = 0; j < 4; j++)
				await d3.mockHandlers.mouseover({ pageX: 0, pageY: 0 }, node);
		}
		// one drawing component holding one rendered tooltip
		expect(liveChildren(view.tooltipComponent)).toBe(2);
	});

	it('keeps only the current drawing’s card-mode components', async () => {
		view.renderCardNodes = true;
		for (let i = 0; i < 3; i++) {
			view.renderD3Graph(element('div'), [node], []);
			await flush();
		}
		// one drawing component holding the card's two rendered fields
		expect(liveChildren(view.tooltipComponent)).toBe(3);
	});

	it('renders afresh when the same node shows a different title', async () => {
		view.renderD3Graph(element('div'), [node], []);
		await d3.mockHandlers.mouseover({ pageX: 0, pageY: 0 }, node);
		await d3.mockHandlers.mouseover({ pageX: 0, pageY: 0 }, { ...node, title: 'edited' });
		expect((MarkdownRenderer.render as jest.Mock).mock.calls.map((c) => c[1])).toEqual([
			node.title,
			'edited',
		]);
	});

	it('unloads the drawing’s components when the view closes', async () => {
		view.renderD3Graph(element('div'), [node], []);
		for (let j = 0; j < 4; j++) await d3.mockHandlers.mouseover({ pageX: 0, pageY: 0 }, node);
		expect(liveChildren(view.tooltipComponent)).toBe(2);
		await view.onClose();
		expect(liveChildren(view.tooltipComponent)).toBe(0);
	});

	it('releases the drawing’s components when an empty message replaces it', async () => {
		view.renderD3Graph(element('div'), [node], []);
		await d3.mockHandlers.mouseover({ pageX: 0, pageY: 0 }, node);
		view.renderEmpty(element('div'), 'No Arete cards found in this file.');
		expect(liveChildren(view.tooltipComponent)).toBe(0);
	});
});

/**
 * The tooltip is absolutely positioned inside the view's container, which sits anywhere in
 * the window (the right sidebar starts near x = 2000 on a wide screen).
 */
describe('LocalGraphView tooltip position', () => {
	const d3 = jest.requireMock('d3');
	const node = { id: 'arete_A', title: 't', group: 'center', filePath: 'A.md', lineNumber: 1 };
	let view: any;

	beforeEach(() => {
		view = new LocalGraphView({} as any, {
			dependencyResolver: {} as any,
			getCardContext: jest.fn(),
			setCardContext: jest.fn(),
		});
		view.app = new (jest.requireMock('obsidian').App)();
		view.container = element('div');
		view.container.getBoundingClientRect = () => ({
			left: 2009,
			top: 100,
			width: 900,
			height: 1400,
		});
	});

	it('places the 2D tooltip beside the pointer, in the container’s coordinates', async () => {
		view.renderD3Graph(element('div'), [node], []);
		await d3.mockHandlers.mouseover(
			{ clientX: 2300, clientY: 400, pageX: 2300, pageY: 400 },
			node,
		);
		expect(view.tooltipContainer.style.left).toBe('303px');
		expect(view.tooltipContainer.style.top).toBe('312px');
		d3.mockHandlers.mousemove({ clientX: 2310, clientY: 420 }, node);
		expect(view.tooltipContainer.style.left).toBe('313px');
		expect(view.tooltipContainer.style.top).toBe('332px');
	});

	it('puts the 2D tooltip to the pointer’s left near the container’s right edge', async () => {
		view.renderD3Graph(element('div'), [node], []);
		await d3.mockHandlers.mouseover({ clientX: 2809, clientY: 400 }, node);
		expect(view.tooltipContainer.style.left).toBe('488px');
	});

	it('pins the 3D tooltip to the canvas corner', () => {
		const canvas = element('div', { offsetLeft: 0, offsetTop: 40 });
		view.render3DGraph(canvas, [], []);
		const onHover = view.graph3D.onNodeHover.mock.calls[0][0];
		onHover(node);
		expect(view.tooltipContainer.style.display).toBe('block');
		expect(view.tooltipContainer.style.left).toBe('12px');
		expect(view.tooltipContainer.style.top).toBe('52px');
	});
});
