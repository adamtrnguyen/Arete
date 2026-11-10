import './test-setup';
import { App, Notice } from 'obsidian';
import AretePlugin from '@/main';
import { SyncService } from '@infrastructure/arete/SyncService';
import { CheckService } from '@infrastructure/arete/CheckService';

// Mock Services
jest.mock('@infrastructure/arete/SyncService');
jest.mock('@infrastructure/arete/CheckService');

describe('AretePlugin Composition', () => {
	let plugin: AretePlugin;
	let app: App;

	beforeEach(async () => {
		jest.clearAllMocks();
		app = new App();
		(app.vault.adapter as any).getBasePath = jest.fn().mockReturnValue('/mock/vault/path');

		plugin = new AretePlugin(app, { dir: 'test-plugin-dir' } as any);
		plugin.statusBarItem = plugin.addStatusBarItem() as any;

		// Trigger onload to initialize services
		await plugin.onload();
	});

	test('onload initializes services', () => {
		expect(SyncService).toHaveBeenCalledWith(app, expect.anything(), expect.anything());
		expect(CheckService).toHaveBeenCalledWith(app, expect.anything()); // settings
		expect(plugin.syncService).toBeInstanceOf(SyncService);
		expect(plugin.checkService).toBeInstanceOf(CheckService);
	});

	test('commands delegate to SyncService', async () => {
		const commands = (global as any).registeredCommands;

		// runSync delegation
		await commands['arete-sync'].callback();
		expect(plugin.syncService.runSync).toHaveBeenCalled();

		// prune delegation
		await commands['arete-sync-prune'].callback();
		expect(plugin.syncService.runSync).toHaveBeenCalledWith(
			true,
			null,
			false,
			expect.any(Function),
		);
	});

	test('commands delegate to CheckService', async () => {
		const commands = (global as any).registeredCommands;

		// check-file delegation
		const mockView = { file: { path: 'test.md' } };
		await commands['arete-check-file'].editorCallback(null, mockView);
		expect(plugin.checkService.getCheckResult).toHaveBeenCalledWith('/mock/vault/path/test.md');

		// integrity: the shared frontmatter check
		const scan = jest.spyOn(plugin.linkCheckerService, 'findAllUnparsedFrontmatter');
		await commands['arete-check-integrity'].callback();
		expect(scan).toHaveBeenCalled();
	});
});

describe('Reload Note Types command', () => {
	test('drops the cached note types', async () => {
		const app = new App();
		const plugin = new AretePlugin(app, { dir: 'test-plugin-dir' } as any);
		await plugin.onload();
		const clear = jest.spyOn(plugin.templateRenderer, 'clearModelCache');

		(global as any).registeredCommands['arete-reload-note-types'].callback();
		expect(clear).toHaveBeenCalledTimes(1);
	});
});

describe('AretePlugin.showCard (obsidian://arete)', () => {
	let plugin: AretePlugin;
	let app: App;
	const file = { path: 'FlashAttention.md', basename: 'FlashAttention' };
	const openFile = jest.fn();

	beforeEach(() => {
		jest.clearAllMocks();
		app = new App();
		plugin = new AretePlugin(app, { dir: 'test-plugin-dir' } as any);
		(app.vault as any).getFileByPath = jest.fn((p: string) => (p === file.path ? file : null));
		(app.workspace as any).getLeaf = jest.fn().mockReturnValue({ openFile });
		(app.metadataCache.getFileCache as jest.Mock).mockReturnValue({
			frontmatter: { cards: [{ Front: 'drafted' }, { id: 'arete_B', Front: 'synced' }] },
		});
		plugin.activateYamlEditorView = jest.fn().mockResolvedValue(undefined);
	});

	test('opens the note and shows the card named by id, with or without .md', async () => {
		await plugin.showCard('FlashAttention', 'arete_B');
		expect(openFile).toHaveBeenCalledWith(file);
		expect(plugin.activateYamlEditorView).toHaveBeenCalledWith(1);
	});

	test('accepts a 1-based position for a card with no id yet', async () => {
		await plugin.showCard('FlashAttention.md', '1');
		expect(plugin.activateYamlEditorView).toHaveBeenCalledWith(0);
	});

	test('says so when the note or card does not exist', async () => {
		await plugin.showCard('Missing.md', '1');
		await plugin.showCard('FlashAttention.md', '9');
		expect(Notice).toHaveBeenCalledWith('No note found at Missing.md.');
		expect(Notice).toHaveBeenCalledWith('FlashAttention has no card 9.');
		expect(plugin.activateYamlEditorView).not.toHaveBeenCalled();
	});
});

/** Words that keep their capitals in a sentence-case UI string. */
const PROPER_NOUNS = new Set([
	'Arete',
	'Anki',
	'Obsidian',
	'FSRS',
	'AnkiConnect',
	'YAML',
	'Debug:',
	'Graph:',
]);

/** True when every word after the first is lowercase or a proper noun. */
function isSentenceCase(text: string): boolean {
	return text
		.split(/\s+/)
		.slice(1)
		.every((word) => {
			const bare = word.replace(/^[("]+|[)":.,]+$/g, '');
			return !/^[A-Z][a-z]/.test(bare) || PROPER_NOUNS.has(bare);
		});
}

describe('AretePlugin native look', () => {
	let ribbon: Array<{ icon: string; title: string }>;

	beforeEach(async () => {
		jest.clearAllMocks();
		(global as any).registeredCommands = {};
		const app = new App();
		const plugin = new AretePlugin(app, { dir: 'test-plugin-dir' } as any);
		ribbon = [];
		plugin.addRibbonIcon = jest.fn().mockImplementation((icon: string, title: string) => {
			ribbon.push({ icon, title });
			return (global as any).mockCreateMockElement('div');
		}) as any;
		await plugin.onload();
	});

	test('every command name is in sentence case', () => {
		const names = Object.values((global as any).registeredCommands).map((c: any) => c.name);
		expect(names.length).toBeGreaterThan(10);
		for (const name of names) expect(isSentenceCase(name)).toBe(true);
	});

	test('ribbon labels are in sentence case', () => {
		expect(ribbon.length).toBe(2);
		for (const { title } of ribbon) expect(isSentenceCase(title)).toBe(true);
	});

	test('command ids are unchanged', () => {
		expect(Object.keys((global as any).registeredCommands).sort()).toEqual(
			[
				'arete-check-file',
				'arete-check-integrity',
				'arete-graph-clear',
				'arete-open-dashboard',
				'arete-open-global-graph',
				'arete-open-local-graph',
				'arete-reload-note-types',
				'arete-sync',
				'arete-sync-current-file',
				'arete-sync-force-all',
				'arete-sync-prune',
				'arete-sync-stats',
				'open-yaml-editor',
			].sort(),
		);
	});
});
