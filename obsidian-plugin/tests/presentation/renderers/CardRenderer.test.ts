import '../../test-setup';
import { App, Component, MarkdownRenderer } from 'obsidian';
import { CardRenderer } from '@/presentation/renderers/CardRenderer';

/** A container that keeps the sections created in it. */
function container() {
	const sections: Array<{ label?: string; content: any }> = [];
	const root: any = {
		children: sections,
		empty: jest.fn(() => {
			sections.length = 0;
		}),
		createDiv: jest.fn((o: any) => {
			const section: any = { content: undefined, setText: jest.fn() };
			section.createDiv = jest.fn((so: any) => {
				if (so.cls === 'arete-preview-label') section.label = so.text;
				const c = { cls: so.cls };
				if (so.cls === 'arete-preview-content') section.content = c;
				return c;
			});
			sections.push(section);
			root.last = section;
			root.lastOpts = o;
			return section;
		}),
	};
	return { root, sections };
}

describe('CardRenderer', () => {
	const app = new App();
	const component = new Component();

	beforeEach(() => {
		jest.clearAllMocks();
	});

	it('renders each content field as a labelled markdown section, in card order', async () => {
		const { root, sections } = container();

		await CardRenderer.render(
			app,
			root,
			{ Front: 'q', Back: '$x$', 'Back Extra': 'more' },
			'Note.md',
			component,
		);

		expect(sections.map((s) => s.label)).toEqual(['Front', 'Back', 'Back Extra']);
		const calls = (MarkdownRenderer.render as jest.Mock).mock.calls;
		expect(calls.map((c) => c[1])).toEqual(['q', '$x$', 'more']);
		expect(calls[0][0]).toBe(app);
		expect(calls[0][2]).toBe(sections[0].content);
		expect(calls[0][3]).toBe('Note.md');
		expect(calls[0][4]).toBe(component);
	});

	it('skips id, model, anki and deps, and anything null or nested', async () => {
		const { root, sections } = container();

		await CardRenderer.render(
			app,
			root,
			{
				id: 'arete_1',
				model: 'Basic',
				anki: { nid: 1 },
				deps: { requires: [] },
				Front: 'kept',
				Gone: null,
				Missing: undefined,
				List: ['a'],
			},
			'',
			component,
		);

		expect(sections.map((s) => s.label)).toEqual(['Front']);
	});

	it('stringifies non-string scalars', async () => {
		const { root } = container();
		await CardRenderer.render(app, root, { Count: 3, Flag: false }, '', component);
		expect((MarkdownRenderer.render as jest.Mock).mock.calls.map((c) => c[1])).toEqual([
			'3',
			'false',
		]);
	});

	it('clears the container before rendering', async () => {
		const { root } = container();
		await CardRenderer.render(app, root, { Front: 'q' }, '', component);
		expect(root.empty).toHaveBeenCalledTimes(1);
	});

	it('names the keys when no field is visible', async () => {
		const { root, sections } = container();
		const warn = jest.spyOn(console, 'warn').mockImplementation(() => {});

		await CardRenderer.render(app, root, { id: 'arete_1', model: 'Basic' }, '', component);

		expect(sections).toHaveLength(1);
		expect(sections[0]).toHaveProperty('setText');
		expect((sections[0] as any).setText).toHaveBeenCalledWith(
			'No visible fields found. Keys: id, model',
		);
		expect(MarkdownRenderer.render).not.toHaveBeenCalled();
		warn.mockRestore();
	});
});
