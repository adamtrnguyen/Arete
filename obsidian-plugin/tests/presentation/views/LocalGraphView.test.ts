import { TFile } from 'obsidian';
import { LocalGraphView } from '@/presentation/views/LocalGraphView';
import { LocalGraphResult } from '@/domain/graph/types';
import { createSizedDomElement as element } from '../../helpers/domMock';

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

	it('does not cache drawnKey when the canvas collapses to 0x0 while loading', async () => {
		const { resolver, view } = setup();
		const canvas = withCanvas(view);
		let finish: () => void = () => undefined;
		resolver.buildGraph.mockImplementationOnce(
			() => new Promise<void>((resolve) => (finish = resolve)),
		);

		const loading = view.refresh();
		canvas.clientWidth = 0;
		canvas.clientHeight = 0;
		finish();
		await loading;

		expect(view.renderD3Graph).not.toHaveBeenCalled();
		expect(view.drawnKey).toBeNull();

		// Expanding the sidebar later renders the graph instead of leaving a blank canvas.
		canvas.clientWidth = 400;
		canvas.clientHeight = 300;
		await view.refresh();
		expect(view.renderD3Graph).toHaveBeenCalledTimes(1);
		expect(view.drawnKey).not.toBeNull();
	});

	it('keeps explicit setContext target instead of reverting to the previously active file', async () => {
		const { app, resolver, view } = setup();
		const other = Object.assign(new TFile(), { path: 'Geometry.md', extension: 'md' });
		const origFile = app.workspace.getActiveFile();
		app.vault.getAbstractFileByPath = jest.fn((p: string) =>
			p === 'Geometry.md' ? other : origFile,
		);
		app.metadataCache.getFileCache.mockImplementation((f: any) =>
			f.path === 'Geometry.md'
				? { frontmatter: { arete: true, cards: [{ id: 'arete_B' }] } }
				: { frontmatter: { arete: true, cards: [{ id: 'arete_A' }] } },
		);
		withCanvas(view);

		await view.setContext('Geometry.md', 'arete_B');

		expect(view.currentFilePath).toBe('Geometry.md');
		expect(resolver.getLocalGraph).toHaveBeenLastCalledWith('arete_B', 2);
		expect(view.plugin.setCardContext).toHaveBeenCalledWith('Geometry.md', 'arete_B');
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

	it('tracks renames and deletes of the currently displayed note', async () => {
		jest.useFakeTimers();
		const { app, view } = setup();
		(global as any).ResizeObserver = class {
			observe() {}
			disconnect() {}
		};
		const vaultHandlers: Record<string, (...args: any[]) => void> = {};
		app.vault.on = jest.fn((name: string, cb: (...args: any[]) => void) => {
			vaultHandlers[name] = cb;
			return {};
		});
		app.workspace.on = jest.fn(() => ({}));
		view.registerEvent = jest.fn();
		view.contentEl = element('div');
		await view.onOpen();
		view.drawnKey = 'prev-key';

		const refresh = jest.spyOn(view, 'refresh').mockResolvedValue(undefined);
		const renamed = Object.assign(new TFile(), { path: 'LinearAlgebra.md', extension: 'md' });
		vaultHandlers.rename(renamed, 'Algebra.md');
		expect(view.currentFilePath).toBe('LinearAlgebra.md');
		expect(view.drawnKey).toBeNull();
		jest.advanceTimersByTime(150);
		expect(refresh).toHaveBeenCalledTimes(1);

		vaultHandlers.delete(renamed);
		expect(view.currentFilePath).toBeNull();
		expect(view.centeredCardId).toBeNull();
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

	it('releaseDrawing stops simulation and clears _svg', () => {
		const { view } = setup();
		const stop = jest.fn();
		view.simulation = { stop };
		view._svg = { attr: jest.fn() };
		view.releaseDrawing();
		expect(stop).toHaveBeenCalledTimes(1);
		expect(view.simulation).toBeNull();
		expect(view._svg).toBeNull();
	});
});
