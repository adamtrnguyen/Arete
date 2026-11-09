import { GlobalGraphView } from '@/presentation/views/GlobalGraphView';
import { GlobalGraphResult } from '@/domain/graph/types';
import { createMockElement } from '../../test-setup';

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
