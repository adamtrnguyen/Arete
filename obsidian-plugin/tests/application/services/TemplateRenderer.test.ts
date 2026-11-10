import { TemplateRenderer } from '@/application/services/TemplateRenderer';
import { AreteClient } from '@/infrastructure/arete/AreteClient';
import { App, Component, MarkdownRenderer } from 'obsidian';
import { DEFAULT_SETTINGS } from '@/domain/settings';

jest.mock('@/infrastructure/arete/AreteClient');

describe('TemplateRenderer', () => {
	let renderer: TemplateRenderer;
	let mockApp: App;
	let mockRepo: jest.Mocked<AreteClient>;
	let opts: { sourcePath: string; component: Component };

	beforeEach(() => {
		jest.clearAllMocks();
		opts = { sourcePath: 'Cards/Example.md', component: {} as Component };
		mockApp = new (jest.requireMock('obsidian').App)() as App;
		mockRepo = new AreteClient(DEFAULT_SETTINGS) as jest.Mocked<AreteClient>;

		// Setup default mocks for repo
		mockRepo.modelStyling.mockResolvedValue('.card { color: black; }');
		// Return a nested structure as expected now
		mockRepo.modelTemplates.mockResolvedValue({
			'Card 1': {
				Front: '{{Front}}',
				Back: '{{FrontSide}}<hr id=answer>{{Back}}',
			},
		});

		renderer = new TemplateRenderer(mockApp, mockRepo);
	});

	it('should use MarkdownRenderer in obsidian mode', async () => {
		renderer.setMode('obsidian');

		// Mock MarkdownRenderer behavior
		(MarkdownRenderer.render as jest.Mock).mockImplementation(async (app, val, el) => {
			el.innerHTML = `<b>${val.replace(/\*\*/g, '')}</b>`; // Simple mock transform
		});

		const result = await renderer.render('Basic', 'Front', { Front: '**Bold Text**' }, opts);

		expect(MarkdownRenderer.render).toHaveBeenCalledWith(
			mockApp,
			'**Bold Text**',
			expect.anything(),
			opts.sourcePath,
			opts.component,
		);
		expect(result?.html).toContain('<b>Bold Text</b>');
	});

	it('should NOT use MarkdownRenderer in anki mode', async () => {
		renderer.setMode('anki');
		(MarkdownRenderer.render as jest.Mock).mockClear();

		const result = await renderer.render('Basic', 'Front', { Front: '**Bold Text**' }, opts);

		expect(MarkdownRenderer.render).not.toHaveBeenCalled();
		// Assuming the template just renders the field
		expect(result?.html).toContain('**Bold Text**');
	});

	it('should unescape HTML tags in Mustache templates', async () => {
		renderer.setMode('obsidian');

		// Mock MD render to return HTML
		(MarkdownRenderer.render as jest.Mock).mockImplementation(async (app, val, el) => {
			el.innerHTML = '<p>Paragraph</p>';
		});

		const result = await renderer.render('Basic', 'Front', { Front: 'Text' }, opts);

		// If escaped: &lt;p&gt;Paragraph&lt;/p&gt;
		// If unescaped: <p>Paragraph</p>
		expect(result?.html).toContain('<p>Paragraph</p>');
		expect(result?.html).not.toContain('&lt;p&gt;');
	});

	it('surfaces a model-load failure so the UI can explain the fallback', async () => {
		mockRepo.modelStyling.mockRejectedValue(new Error('Failed'));
		await expect(renderer.render('NonExistent', 'Front', {}, opts)).rejects.toThrow('Failed');
	});

	it('should handle missing template types', async () => {
		mockRepo.modelTemplates.mockResolvedValue({
			'Card 1': { Front: '{{Front}}', Back: '' } as any,
		});
		const result = await renderer.render('Basic', 'Back', { Front: 'text' }, opts);
		expect(result).toBeNull();
	});

	it('should render FrontSide in Back template', async () => {
		renderer.setMode('anki');
		const result = await renderer.render(
			'Basic',
			'Back',
			{
				Front: 'FrontVal',
				Back: 'BackVal',
			},
			opts,
		);
		expect(result?.html).toContain('FrontVal'); // FrontSide rendered
		expect(result?.html).toContain('BackVal');
	});

	it('should return null if no templates found in model', async () => {
		mockRepo.modelTemplates.mockResolvedValue({});
		const result = await renderer.render('Empty', 'Front', {}, opts);
		expect(result).toBeNull();
	});

	it('shares pending loads and never overlaps model reads, including different renderers', async () => {
		let active = 0;
		let maxActive = 0;
		const read = async () => {
			active++;
			maxActive = Math.max(maxActive, active);
			await new Promise((resolve) => setTimeout(resolve, 1));
			active--;
		};
		mockRepo.modelStyling.mockImplementation(async () => {
			await read();
			return '';
		});
		mockRepo.modelTemplates.mockImplementation(async () => {
			await read();
			return {};
		});
		const other = new TemplateRenderer(mockApp, mockRepo);
		await Promise.all([
			renderer.preloadModel('Basic'),
			renderer.preloadModel('Basic'),
			renderer.preloadModel('Cloze'),
			other.preloadModel('Other'),
		]);
		expect(maxActive).toBe(1);
		expect(mockRepo.modelStyling).toHaveBeenCalledTimes(3);
		expect(mockRepo.modelTemplates).toHaveBeenCalledTimes(3);
	});

	it('retries failed loads without poisoning the queue or cache', async () => {
		mockRepo.modelStyling.mockRejectedValueOnce(new Error('busy'));
		await expect(renderer.preloadModel('Basic')).rejects.toThrow('busy');
		await renderer.preloadModel('Basic');
		await renderer.preloadModel('Basic');
		expect(mockRepo.modelStyling).toHaveBeenCalledTimes(2);
		expect(mockRepo.modelTemplates).toHaveBeenCalledTimes(1);
	});

	it('renders Cloze the way Anki shows card 1: hidden on the front, revealed on the back', async () => {
		// Mustache cannot evaluate {{cloze:Text}}, so the renderer handles cloze itself.
		renderer.setMode('anki'); // raw fields, so the assertion sees the cloze markup itself
		mockRepo.modelTemplates.mockResolvedValue({
			Cloze: {
				Front: '{{cloze:Text}}',
				Back: '{{cloze:Text}}<br>{{Back Extra}}',
			},
		});
		const fields = {
			Text: 'Uses {{c1::online softmax}} and {{c2::tiling}}.',
			'Back Extra': 'E',
		};

		const front = await renderer.render('Cloze', 'Front', fields, opts);
		expect(front?.html).toBe('Uses <span class="cloze">[...]</span> and tiling.');

		const back = await renderer.render('Cloze', 'Back', fields, opts);
		expect(back?.html).toBe('Uses <span class="cloze">online softmax</span> and tiling.<br>E');
	});

	it('shows a type-answer box on the front and the answer on the back', async () => {
		renderer.setMode('anki');
		mockRepo.modelTemplates.mockResolvedValue({
			'Card 1': {
				Front: '{{Front}}<br>{{type:Back}}',
				Back: '{{FrontSide}}<hr id=answer>{{Back}}',
			},
		});
		const front = await renderer.render('Typed', 'Front', { Front: 'Q', Back: 'A' }, opts);
		expect(front?.html).toBe(
			'Q<br><span class="arete-type-answer" style="display:inline-block;min-width:10em;padding:2px 8px;border:1px solid #999;border-radius:4px;color:#888">type answer</span>',
		);
	});

	describe('field rendering cost', () => {
		const clozeModel = {
			'Card 1': {
				Front: '{{cloze:Text}}',
				Back: '{{cloze:Text}}<br>{{Back Extra}}',
			},
		};
		const fields = { Text: 'A {{c1::b}} c', 'Back Extra': 'E', Unused: 'U' };
		const renderedValues = () =>
			(MarkdownRenderer.render as jest.Mock).mock.calls.map((c) => c[1]).sort();

		beforeEach(() => {
			renderer.setMode('obsidian');
			mockRepo.modelTemplates.mockResolvedValue(clozeModel);
			(MarkdownRenderer.render as jest.Mock).mockImplementation(async (_a, val, el) => {
				el.innerHTML = `<i>${val}</i>`;
			});
		});

		it('renders only the fields the templates use, with the same output', async () => {
			const front = await renderer.render('Cloze', 'Front', fields, opts);
			expect(renderedValues()).toEqual(['A <span class="cloze">[...]</span> c']);
			expect(front?.html).toBe('<i>A <span class="cloze">[...]</span> c</i>');

			(MarkdownRenderer.render as jest.Mock).mockClear();
			const back = await renderer.render('Cloze', 'Back', fields, opts);
			expect(renderedValues()).toEqual(['A <span class="cloze">b</span> c', 'E']);
			expect(back?.html).toBe('<i>A <span class="cloze">b</span> c</i><br><i>E</i>');
		});

		it('renders the front fields too when the back shows {{FrontSide}}', async () => {
			mockRepo.modelTemplates.mockResolvedValue({
				'Card 1': { Front: '{{Front}}', Back: '{{FrontSide}}<hr id=answer>{{Back}}' },
			});
			const back = await renderer.render(
				'Basic',
				'Back',
				{ Front: 'Q', Back: 'A', Unused: 'U' },
				opts,
			);
			expect(renderedValues()).toEqual(['A', 'Q']);
			expect(back?.html).toBe('<i>Q</i><hr id=answer><i>A</i>');
		});

		it('starts every field render before any finishes', async () => {
			mockRepo.modelTemplates.mockResolvedValue({
				'Card 1': { Front: '{{A}}{{B}}{{C}}', Back: '' },
			});
			const finish: Array<() => void> = [];
			(MarkdownRenderer.render as jest.Mock).mockImplementation(
				(_a, val, el) =>
					new Promise<void>((resolve) =>
						finish.push(() => {
							el.innerHTML = val;
							resolve();
						}),
					),
			);

			const pending = renderer.render('Three', 'Front', { A: '1', B: '2', C: '3' }, opts);
			await new Promise((r) => setImmediate(r));
			expect(MarkdownRenderer.render).toHaveBeenCalledTimes(3);
			finish.reverse().forEach((f) => f());
			expect((await pending)?.html).toBe('123');
		});

		it('renders every field when a template does not parse', async () => {
			mockRepo.modelTemplates.mockResolvedValue({
				'Card 1': { Front: '{{#Open}}{{Text}}', Back: '' },
			});
			const result = await renderer.render(
				'Broken',
				'Front',
				{ Text: 't', Other: 'o' },
				opts,
			);
			expect(renderedValues()).toEqual(['o', 't']);
			expect(result).toBeNull();
		});

		it('clearModelCache makes the next render fetch the note type again', async () => {
			await renderer.render('Cloze', 'Front', fields, opts);
			await renderer.render('Cloze', 'Front', fields, opts);
			expect(mockRepo.modelTemplates).toHaveBeenCalledTimes(1);

			renderer.clearModelCache();
			await renderer.render('Cloze', 'Front', fields, opts);
			expect(mockRepo.modelStyling).toHaveBeenCalledTimes(2);
			expect(mockRepo.modelTemplates).toHaveBeenCalledTimes(2);
		});
	});
});
