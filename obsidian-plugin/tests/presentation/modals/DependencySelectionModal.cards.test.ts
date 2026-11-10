import '../../test-setup';
import { createMockElement } from '../../test-setup';
import { App, MarkdownRenderer, Notice } from 'obsidian';
import { DependencySelectionModal } from '@/presentation/modals/DependencySelectionModal';
import { CardRenderer } from '@/presentation/renderers/CardRenderer';

jest.mock('@/presentation/renderers/CardRenderer', () => ({
	CardRenderer: { render: jest.fn().mockResolvedValue(undefined) },
}));

const file = (path: string) => ({ path, basename: path.replace(/\.md$/, '') });

describe('DependencySelectionModal: choosing a card', () => {
	let app: any;
	let modal: any;
	let onSubmit: jest.Mock;
	const deckNote = file('Deck.md');
	const cache: Record<string, any> = {};

	beforeEach(() => {
		jest.clearAllMocks();
		jest.useFakeTimers();
		app = new App();
		app.vault.getMarkdownFiles = jest.fn(() => [deckNote, file('Empty.md')]);
		app.vault.cachedRead = jest.fn().mockResolvedValue('# body');
		app.metadataCache.getFileCache = jest.fn((f: { path: string }) => cache[f.path]);
		cache['Deck.md'] = {
			frontmatter: {
				cards: [
					{ id: 'arete_1', Front: 'What is ![[pic.png]] here?' },
					{ Front: 'no id, so not linkable' },
					{ id: 'arete_2', Text: 'Cloze {{c1::x}}' },
					{ id: 'arete_3' },
				],
			},
		};
		cache['Empty.md'] = { frontmatter: { cards: [] } };
		onSubmit = jest.fn();
		modal = new DependencySelectionModal(app, onSubmit);
		modal.resultsContainer = createMockElement('div', { children: [] });
		modal.previewContainer = createMockElement('div');
		modal.searchInput = { setPlaceholder: jest.fn(), setValue: jest.fn() };
		modal.close = jest.fn();
		modal.loadFiles();
	});
	afterEach(() => {
		jest.useRealTimers();
	});

	const ids = () => modal.filteredItems.map((i: any) => i.id);
	const labels = () => modal.filteredItems.map((i: any) => i.label);

	it('lists files first, labelled by basename with the path as description', () => {
		expect(modal.mode).toBe('files');
		expect(ids()).toEqual(['Deck.md', 'Empty.md']);
		expect(labels()).toEqual(['Deck', 'Empty']);
	});

	it('drilling into a note lists its cards that have ids, behind a Back entry', () => {
		modal.selectedIndex = 0;
		modal.selectCurrent();

		expect(modal.mode).toBe('cards');
		expect(ids()).toEqual(['__back__', 'arete_1', 'arete_2', 'arete_3']);
		expect(labels()).toEqual([
			'.. (back to files)',
			'What is [Image] here?',
			'Cloze {{c1::x}}',
			'Untitled Card',
		]);
		expect(modal.searchInput.setValue).toHaveBeenCalledWith('');
		expect(modal.searchInput.setPlaceholder).toHaveBeenLastCalledWith(
			'Searching cards in Deck...',
		);
	});

	it('truncates a long front to 60 characters', () => {
		cache['Deck.md'].frontmatter.cards = [{ id: 'arete_1', Front: 'x'.repeat(100) }];
		modal.loadCards(deckNote);
		expect(modal.filteredItems[1].label).toBe('x'.repeat(60));
	});

	it('a note without cards says so and stays on the file list', () => {
		modal.selectedIndex = 1;
		modal.selectCurrent();

		expect(Notice).toHaveBeenCalledWith('No cards found in Empty');
		expect(modal.mode).toBe('files');
		expect(ids()).toEqual(['Deck.md', 'Empty.md']);
	});

	it('a note whose cards value is not a list is treated as having none', () => {
		cache['Deck.md'].frontmatter.cards = 'junk';
		modal.loadCards(deckNote);
		expect(Notice).toHaveBeenCalledWith('No cards found in Deck');
		expect(modal.mode).toBe('files');
	});

	it('Enter on a card returns its id and closes', () => {
		modal.loadCards(deckNote);
		modal.selectedIndex = 2;

		modal.selectCurrent();

		expect(onSubmit).toHaveBeenCalledWith('arete_2');
		expect(modal.close).toHaveBeenCalled();
	});

	it('Enter on the Back entry returns to the file list without submitting', () => {
		modal.loadCards(deckNote);
		modal.selectedIndex = 0;

		modal.selectCurrent();

		expect(modal.mode).toBe('files');
		expect(onSubmit).not.toHaveBeenCalled();
		expect(modal.close).not.toHaveBeenCalled();
	});

	it('Enter with nothing listed does nothing', () => {
		modal.filterItems('zzz-no-match');
		expect(() => modal.selectCurrent()).not.toThrow();
		expect(onSubmit).not.toHaveBeenCalled();
	});

	it('moving the selection wraps at both ends', () => {
		modal.moveSelection(-1);
		expect(modal.selectedIndex).toBe(1);
		modal.moveSelection(1);
		expect(modal.selectedIndex).toBe(0);
	});

	it('moving the selection with an empty list does nothing', () => {
		modal.filterItems('zzz-no-match');
		modal.moveSelection(1);
		expect(modal.selectedIndex).toBe(0);
	});

	it('the preview of a card goes through CardRenderer with the note path as source', async () => {
		modal.loadCards(deckNote);
		modal.selectedIndex = 1;

		await modal.renderPreview();

		const call = (CardRenderer.render as jest.Mock).mock.calls[0];
		expect(call[0]).toBe(app);
		expect(call[2]).toEqual(cache['Deck.md'].frontmatter.cards[0]);
		expect(call[3]).toBe('Deck.md');
		expect(MarkdownRenderer.render).not.toHaveBeenCalled();
	});

	it('the preview of the Back entry is a hint, with no render', async () => {
		modal.loadCards(deckNote);
		modal.selectedIndex = 0;

		await modal.renderPreview();

		expect(modal.previewContainer.createDiv).toHaveBeenCalledWith(
			expect.objectContaining({ text: 'Go back to file list' }),
		);
		expect(CardRenderer.render).not.toHaveBeenCalled();
		expect(app.vault.cachedRead).not.toHaveBeenCalled();
	});

	it('with no match the preview says there is no selection', async () => {
		modal.filterItems('zzz-no-match');
		await modal.renderPreview();
		expect(modal.previewContainer.createDiv).toHaveBeenCalledWith(
			expect.objectContaining({ text: 'No selection' }),
		);
	});

	it('hovering a result selects it and schedules its preview', () => {
		const rows: any[] = [];
		modal.resultsContainer = createMockElement('div', {
			children: rows,
			createDiv: jest.fn(() => {
				const row = createMockElement('div', {
					addClass: jest.fn(),
					removeClass: jest.fn(),
					scrollIntoView: jest.fn(),
				});
				rows.push(row);
				return row;
			}),
		});
		modal.renderResults();
		const schedule = jest.spyOn(modal, 'schedulePreview');

		rows[1]._listeners['mouseenter']();

		expect(modal.selectedIndex).toBe(1);
		expect(rows[1].addClass).toHaveBeenCalledWith('is-selected');
		expect(rows[0].removeClass).toHaveBeenCalledWith('is-selected');
		expect(schedule).toHaveBeenCalled();
	});

	it('closing cancels a pending preview and unloads the preview component', async () => {
		modal.filterItems(''); // schedules a preview
		const unload = jest.spyOn(modal._component, 'unload');
		modal.onClose();
		await jest.advanceTimersByTimeAsync(500);

		expect(app.vault.cachedRead).not.toHaveBeenCalled();
		expect(unload).toHaveBeenCalled();
	});
});
