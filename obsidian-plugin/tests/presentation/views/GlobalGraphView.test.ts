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
