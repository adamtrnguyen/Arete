import '../../test-setup';
import { createMockElement } from '../../test-setup';
import { App, MarkdownRenderer, Platform } from 'obsidian';
import { DependencySelectionModal } from '@/presentation/modals/DependencySelectionModal';

function file(path: string) {
	const basename = path.replace(/\.md$/, '').split('/').pop()!;
	return { path, basename };
}

describe('DependencySelectionModal search and preview', () => {
	let app: any;
	let modal: any;
	const files = [
		file('Algebra.md'),
		...Array.from({ length: 30 }, (_, i) => file(`math/Topic ${i}.md`)),
		file('Biology.md'),
	];

	beforeEach(() => {
		jest.useFakeTimers();
		jest.clearAllMocks();
		app = new App();
		app.vault.getMarkdownFiles = jest.fn(() => files);
		app.vault.cachedRead = jest.fn(async (f: { path: string }) => `# ${f.path}`);
		app.vault.read = jest.fn();
		modal = new DependencySelectionModal(app as App, jest.fn());
		modal.resultsContainer = createMockElement('div', { children: [] });
		modal.previewContainer = createMockElement('div');
		modal.searchInput = { setPlaceholder: jest.fn(), setValue: jest.fn() };
		modal.loadFiles();
	});
	afterEach(() => {
		jest.useRealTimers();
	});

	const ids = () => modal.filteredItems.map((i: { id: string }) => i.id);

	test('matches label or path, case-insensitively, in vault order, at most 20', () => {
		modal.filterItems('TOPIC');
		expect(ids()).toEqual(Array.from({ length: 20 }, (_, i) => `math/Topic ${i}.md`));

		modal.filterItems('MATH/topic 2');
		expect(ids()).toEqual(
			['math/Topic 2.md', 'math/Topic 20.md', 'math/Topic 21.md'].concat(
				Array.from({ length: 8 }, (_, i) => `math/Topic ${22 + i}.md`),
			),
		);

		modal.filterItems('bio');
		expect(ids()).toEqual(['Biology.md']);

		modal.filterItems('');
		expect(ids()).toEqual(files.slice(0, 20).map((f) => f.path));
	});

	test('arrowing through results renders one preview, from the cache, after a pause', async () => {
		await jest.advanceTimersByTimeAsync(200); // the initial preview
		app.vault.cachedRead.mockClear();
		(MarkdownRenderer.render as jest.Mock).mockClear();

		for (let i = 0; i < 5; i++) modal.moveSelection(1);
		expect(app.vault.cachedRead).not.toHaveBeenCalled();

		await jest.advanceTimersByTimeAsync(200);
		expect(app.vault.cachedRead).toHaveBeenCalledTimes(1);
		expect(app.vault.cachedRead).toHaveBeenCalledWith(files[5]);
		expect(app.vault.read).not.toHaveBeenCalled();
		expect(MarkdownRenderer.render).toHaveBeenCalledTimes(1);
	});

	test('a preview superseded while its file is read renders nothing', async () => {
		let release!: (s: string) => void;
		app.vault.cachedRead.mockImplementationOnce(
			() => new Promise<string>((r) => (release = r)),
		);
		(MarkdownRenderer.render as jest.Mock).mockClear();

		const slow = modal.renderPreview(); // Algebra, still reading
		modal.selectedIndex = 1;
		await modal.renderPreview(); // Topic 0, finishes first
		release('# Algebra');
		await slow;

		expect(MarkdownRenderer.render).toHaveBeenCalledTimes(1);
		expect((MarkdownRenderer.render as jest.Mock).mock.calls[0][3]).toBe('math/Topic 0.md');
	});
});

describe('DependencySelectionModal key hint', () => {
	afterEach(() => {
		Platform.isMacOS = false;
	});

	/** A modal with its lists mocked; `onOpen` is not run, so no layout is needed. */
	function setup() {
		const app: any = new App();
		app.vault.getMarkdownFiles = jest.fn(() => [file('Algebra.md')]);
		app.metadataCache.getFileCache = jest.fn(() => ({
			frontmatter: { cards: [{ id: 'arete_1', Front: 'q' }] },
		}));
		const modal: any = new DependencySelectionModal(app, jest.fn());
		modal.resultsContainer = createMockElement('div', { children: [] });
		modal.previewContainer = createMockElement('div');
		modal.searchInput = { setPlaceholder: jest.fn(), setValue: jest.fn() };
		return modal;
	}

	const hint = (modal: any): string => {
		const calls = (modal.searchInput.setPlaceholder as jest.Mock).mock.calls;
		return calls[calls.length - 1][0];
	};

	test.each([
		[true, 'Cmd'],
		[false, 'Ctrl'],
	])('on macOS=%s the file hint names %s', (isMacOS, name) => {
		Platform.isMacOS = isMacOS;
		const modal = setup();
		modal.loadFiles();
		expect(hint(modal)).toBe(
			`Search files... (Enter to select, ${name}+Enter to browse cards)`,
		);
	});

	test('the browse shortcut is registered on Mod, so Cmd on macOS and Ctrl elsewhere', () => {
		const modal = setup();
		const files = jest.spyOn(modal, 'loadFiles').mockImplementation(() => {});
		modal.searchContainer = createMockElement('div');
		modal.onOpen();
		files.mockRestore();
		modal.resultsContainer = createMockElement('div', { children: [] });
		const registered = (modal.scope.register as jest.Mock).mock.calls;
		const modEnter = registered.find(([mods, key]) => mods.join() === 'Mod' && key === 'Enter');
		expect(modEnter).toBeDefined();
		expect(registered.some(([mods]) => mods.some((m: string) => /ctrl|meta/i.test(m)))).toBe(
			false,
		);

		modal.loadFiles();
		const event = { preventDefault: jest.fn() };
		modEnter[2](event);
		expect(event.preventDefault).toHaveBeenCalled();
		expect(hint(modal)).toBe('Searching cards in Algebra...');
	});
});
