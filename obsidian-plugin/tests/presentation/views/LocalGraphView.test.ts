import { TFile } from 'obsidian';
import { LocalGraphView } from '@/presentation/views/LocalGraphView';
import { LocalGraphResult } from '@/domain/graph/types';
import { createMockElement as baseElement } from '../../test-setup';

/** The shared mock element, sized, with the DOM methods the view calls. */
function element(tag?: string, opts?: any): any {
	const el = baseElement(tag, { clientWidth: 400, clientHeight: 300, ...opts });
	el.setAttribute = jest.fn();
	el.toggleClass = jest.fn((cls: string, on: boolean) => {
		el.classes = new Set(el.classes ?? []);
		if (on) el.classes.add(cls);
		else el.classes.delete(cls);
	});
	el.createDiv = jest.fn((o?: any) => element('div', o));
	el.createEl = jest.fn((t: string, o?: any) => element(t, o));
	el.createSpan = jest.fn((o?: any) => element('span', o));
	el.querySelector = jest.fn((sel: string) => {
		const cls = sel.replace(/^\./, '');
		return el.createDiv.mock.results.map((r: any) => r.value).find((c: any) => c.cls === cls);
	});
	return el;
}

// The shared obsidian mock has no setTooltip; the views call it for toolbar tooltips.
beforeAll(() => {
	jest.requireMock('obsidian').setTooltip = jest.fn();
});

const CENTER = { id: 'arete_A', title: 'algebra', filePath: 'Algebra.md', lineNumber: 5 };
const LOCAL: LocalGraphResult = {
	center: CENTER,
	prerequisites: [],
	dependents: [],
	related: [],
	links: [],
	cycles: [],
};

function setup() {
	const app = new (jest.requireMock('obsidian').App)();
	const file = Object.assign(new TFile(), { path: 'Algebra.md', extension: 'md' });
	app.workspace.getActiveFile.mockReturnValue(file);
	app.vault.getAbstractFileByPath = jest.fn(() => file);
	app.metadataCache.getFileCache.mockReturnValue({
		frontmatter: { arete: true, cards: [{ id: 'arete_A' }] },
	});
	const resolver = {
		version: 0,
		buildGraph: jest.fn().mockResolvedValue(undefined),
		getLocalGraph: jest.fn(() => LOCAL),
	};
	const host = {
		dependencyResolver: resolver as any,
		getCardContext: jest.fn(),
		setCardContext: jest.fn(),
	};
	const view: any = new LocalGraphView({} as any, host);
	view.app = app;
	view.renderD3Graph = jest.fn();
	view.render3DGraph = jest.fn();
	return { app, resolver, view };
}

/** A view whose canvas exists, without running onOpen. */
function withCanvas(view: any) {
	const canvas = element('div', { cls: 'arete-graph-canvas' });
	view.container = { querySelector: () => canvas };
	return canvas;
}

describe('LocalGraphView.refresh', () => {
	afterEach(() => jest.useRealTimers());

	it('does not fetch or redraw a picture that has not changed', async () => {
		const { resolver, view } = setup();
		withCanvas(view);
		await view.refresh();
		await view.refresh();
		await view.refresh();
		expect(resolver.buildGraph).toHaveBeenCalledTimes(1);
		expect(view.renderD3Graph).toHaveBeenCalledTimes(1);
	});

	it('redraws once the graph data changes, as after an edit to a card file', async () => {
		const { resolver, view } = setup();
		withCanvas(view);
		await view.refresh();
		resolver.version++; // what invalidate() does
		await view.refresh();
		expect(resolver.buildGraph).toHaveBeenCalledTimes(2);
		expect(view.renderD3Graph).toHaveBeenCalledTimes(2);
	});

	it('redraws when an option that shapes the picture changes', async () => {
		const { view } = setup();
		withCanvas(view);
		await view.refresh();
		view.depth = 3;
		await view.refresh();
		view.showRelated = false;
		await view.refresh();
		view.viewMode = '3d';
		await view.refresh();
		expect(view.renderD3Graph).toHaveBeenCalledTimes(3);
		expect(view.render3DGraph).toHaveBeenCalledTimes(1);
	});

	it('does not fetch the graph while the view is hidden', async () => {
		const { resolver, view } = setup();
		const canvas = withCanvas(view);
		canvas.clientWidth = 0;
		canvas.clientHeight = 0;
		await view.refresh();
		expect(resolver.buildGraph).not.toHaveBeenCalled();
		expect(canvas.empty).not.toHaveBeenCalled();
	});

	it('resizes the drawing on a pure size change instead of rebuilding it', async () => {
		const { resolver, view } = setup();
		const canvas = withCanvas(view);
		await view.refresh();
		view._svg = { attr: jest.fn() };
		view.layoutCenter = { x: 200, y: 150 };
		canvas.clientWidth = 600;
		canvas.clientHeight = 500;
		await view.refresh();
		expect(resolver.buildGraph).toHaveBeenCalledTimes(1);
		expect(view.renderD3Graph).toHaveBeenCalledTimes(1);
		expect(view._svg.attr).toHaveBeenCalledWith('viewBox', [-100, -100, 600, 500]);
	});

	it('lets only the latest of two overlapping refreshes draw', async () => {
		const { resolver, view } = setup();
		withCanvas(view);
		let finish: () => void = () => undefined;
		resolver.buildGraph.mockImplementationOnce(
			() => new Promise<void>((resolve) => (finish = resolve)),
		);
		const first = view.refresh();
		view.depth = 4;
		const second = view.refresh();
		finish();
		await first;
		await second;
		expect(view.renderD3Graph).toHaveBeenCalledTimes(1);
		expect(resolver.getLocalGraph).toHaveBeenLastCalledWith('arete_A', 4);
	});
});

describe('LocalGraphView events', () => {
	afterEach(() => jest.useRealTimers());

	it('refreshes once per burst of leaf changes and has no workspace resize listener', async () => {
		jest.useFakeTimers();
		const { app, view } = setup();
		(global as any).ResizeObserver = class {
			observe() {}
			disconnect() {}
		};
		const handlers: Record<string, () => void> = {};
		app.workspace.on = jest.fn((name: string, cb: () => void) => {
			handlers[name] = cb;
			return {};
		});
		view.registerEvent = jest.fn();
		view.contentEl = element('div');
		await view.onOpen();
		expect(Object.keys(handlers)).not.toContain('resize');

		const refresh = jest.spyOn(view, 'refresh');
		handlers['active-leaf-change']();
		handlers['active-leaf-change']();
		handlers['active-leaf-change']();
		expect(refresh).not.toHaveBeenCalled();
		jest.advanceTimersByTime(150);
		expect(refresh).toHaveBeenCalledTimes(1);
	});
});

describe('LocalGraphView logging', () => {
	afterEach(() => jest.restoreAllMocks());

	it('logs nothing on a refresh while debug mode is off', async () => {
		const log = jest.spyOn(console, 'log').mockImplementation(() => undefined);
		const { view } = setup();
		withCanvas(view);
		await view.refresh();
		await view.setContext('Algebra.md', 'arete_A');
		expect(view.renderD3Graph).toHaveBeenCalled();
		expect(log).not.toHaveBeenCalled();
	});
});
