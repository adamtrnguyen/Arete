import { App, Component } from 'obsidian';
import { CardYamlEditorView } from '@/presentation/views/CardYamlEditorView';
import { renderCardPreviewFrame } from '@/presentation/renderers/CardPreviewFrame';
import { CardRenderer } from '@/presentation/renderers/CardRenderer';
import { createMockElement } from '../../test-setup';

jest.mock('@/presentation/renderers/CardPreviewFrame');
jest.mock('@/presentation/renderers/CardRenderer');

describe('CardYamlEditorView preview', () => {
	let view: any;
	let app: App;
	let render: jest.Mock;
	beforeEach(() => {
		jest.clearAllMocks();
		app = new (jest.requireMock('obsidian').App)();
		render = jest
			.fn()
			.mockResolvedValue({ html: '<p>Answer</p>', css: '.card{font-size:22px}' });
		view = new CardYamlEditorView({} as any, { templateRenderer: { render } } as any);
		view.app = app;
		view.previewContainer = createMockElement('div');
		view.fieldEditorContainer = createMockElement('div');
		view.renderDependencySection = jest.fn();
		(app.workspace.getActiveFile as jest.Mock).mockReturnValue({ path: 'Zetetics.md' });
		(app.metadataCache.getFileCache as jest.Mock).mockReturnValue({
			frontmatter: {
				model: 'Basic with Extra',
				cards: [{ Front: 'F', Back: 'B', 'Back Extra': 'Extra' }],
			},
		});
	});

	it('uses the file model in Fields and Preview without adding it to card YAML', async () => {
		await view.loadCards();
		view.renderFieldEditor();
		expect(view.fieldEditorContainer.createDiv).toHaveBeenCalledWith(
			expect.objectContaining({ text: 'Basic with Extra' }),
		);
		await view.renderPreview();
		expect(render).toHaveBeenCalledWith(
			'Basic with Extra',
			'Front',
			expect.objectContaining({ 'Back Extra': 'Extra' }),
			{
				sourcePath: 'Zetetics.md',
				component: expect.any(Component),
			},
		);
		expect(view.cards[0].model).toBeUndefined();
		view.cards[0].model = 'Cloze';
		await view.renderPreview();
		expect(render.mock.calls[1][0]).toBe('Cloze');
	});

	it('shows the failure and field fallback instead of leaving an empty preview', async () => {
		await view.loadCards();
		render.mockRejectedValue(new Error('Could not start Arete'));
		await view.renderPreview();
		expect(view.previewContainer.createDiv).toHaveBeenCalledWith(
			expect.objectContaining({ text: 'Could not load card preview: Could not start Arete' }),
		);
		expect(CardRenderer.render).toHaveBeenCalled();
		expect(renderCardPreviewFrame).not.toHaveBeenCalled();
	});

	it('discards a slow previous render after switching sides', async () => {
		await view.loadCards();
		let finish: (value: { html: string; css: string }) => void;
		render.mockImplementationOnce(
			() =>
				new Promise((resolve) => {
					finish = resolve;
				}),
		);
		const previous = view.renderPreview();
		view.previewSide = 'Back';
		await view.renderPreview();
		finish!({ html: 'stale front', css: '' });
		await previous;
		expect(renderCardPreviewFrame).toHaveBeenCalledTimes(1);
		expect(renderCardPreviewFrame).toHaveBeenCalledWith(
			app,
			view.previewContainer,
			{ html: '<p>Answer</p>', css: '.card{font-size:22px}' },
			'Zetetics.md',
			expect.any(Component),
		);
	});
});
