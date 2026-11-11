import './test-setup';
import * as path from 'path';
import { App, Notice } from 'obsidian';
import AretePlugin from '@/main';

jest.mock('@infrastructure/arete/SyncService');
jest.mock('@infrastructure/arete/CheckService');

type Handler = (...args: any[]) => void;

/** Load the plugin with every vault and metadata-cache handler captured by event name. */
async function loadPlugin() {
	const app = new App();
	const vaultHandlers: Record<string, Handler> = {};
	const metaHandlers: Record<string, Handler> = {};
	(app.vault as any).on = jest.fn((name: string, cb: Handler) => {
		vaultHandlers[name] = cb;
		return { name };
	});
	(app.metadataCache as any).on = jest.fn((name: string, cb: Handler) => {
		metaHandlers[name] = cb;
		return { name };
	});
	(app.vault.adapter as any).getBasePath = jest.fn().mockReturnValue('/vault');
	const plugin = new AretePlugin(app, { dir: 'test-plugin-dir' } as any);
	await plugin.onload();
	return { app, plugin, vaultHandlers, metaHandlers };
}

describe('sync on save', () => {
	beforeEach(() => {
		jest.useFakeTimers();
	});
	afterEach(() => {
		jest.useRealTimers();
	});

	async function setup() {
		const loaded = await loadPlugin();
		loaded.plugin.settings.sync_on_save = true;
		loaded.plugin.settings.sync_on_save_delay = 1000;
		(loaded.plugin.syncService.runSync as jest.Mock).mockResolvedValue('success');
		loaded.plugin.saveData = jest.fn().mockResolvedValue(undefined);
		return loaded;
	}

	/** The (prune, target, force) of every sync the plugin started. */
	const started = (plugin: AretePlugin) =>
		(plugin.syncService.runSync as jest.Mock).mock.calls.map((c) => c.slice(0, 3));

	test('saving A then B within the delay syncs both', async () => {
		const { plugin, vaultHandlers } = await setup();
		vaultHandlers.modify({ path: 'A.md' });
		await jest.advanceTimersByTimeAsync(500);
		vaultHandlers.modify({ path: 'B.md' });
		await jest.advanceTimersByTimeAsync(1000);

		expect(started(plugin)).toEqual([
			[false, path.join('/vault', 'A.md'), false],
			[false, path.join('/vault', 'B.md'), false],
		]);
	});

	test('repeated saves of one note sync it once', async () => {
		const { plugin, vaultHandlers } = await setup();
		vaultHandlers.modify({ path: 'A.md' });
		await jest.advanceTimersByTimeAsync(500);
		vaultHandlers.modify({ path: 'A.md' });
		await jest.advanceTimersByTimeAsync(2000);

		expect(started(plugin)).toHaveLength(1);
	});

	test('no sync fires after unload', async () => {
		const { plugin, vaultHandlers } = await setup();
		vaultHandlers.modify({ path: 'A.md' });
		vaultHandlers.modify({ path: 'B.md' });
		plugin.onunload();
		await jest.advanceTimersByTimeAsync(5000);

		expect(started(plugin)).toEqual([]);
	});

	test('a save during a running sync waits for it, then syncs once', async () => {
		const { plugin, vaultHandlers } = await setup();
		let finish!: (outcome: string) => void;
		(plugin.syncService.runSync as jest.Mock).mockImplementationOnce(
			() => new Promise((resolve) => (finish = resolve)),
		);
		void plugin.runSync();
		vaultHandlers.modify({ path: 'A.md' });
		vaultHandlers.modify({ path: 'B.md' });
		await jest.advanceTimersByTimeAsync(2000);

		expect(started(plugin)).toEqual([[false, null, false]]);
		finish('success');
		await jest.advanceTimersByTimeAsync(0);

		expect(started(plugin)).toEqual([
			[false, null, false],
			[false, null, false],
		]);
	});

	test('a manual sync during a running sync starts nothing and says so', async () => {
		const { plugin } = await setup();
		let finish!: (outcome: string) => void;
		(plugin.syncService.runSync as jest.Mock).mockImplementationOnce(
			() => new Promise((resolve) => (finish = resolve)),
		);
		void plugin.runSync();
		await plugin.runSync(true);

		expect(started(plugin)).toEqual([[false, null, false]]);
		expect(Notice).toHaveBeenCalledWith('A sync is already running.');
		finish('success');
	});
});

describe('dependency graph invalidation', () => {
	const cardNote = { frontmatter: { arete: true, cards: [{ id: 'arete_A' }] } };
	const plainNote = { frontmatter: { tags: ['x'] } };
	const noFrontmatter = { sections: [] as unknown[] };

	async function setup(cacheByPath: Record<string, unknown>) {
		const loaded = await loadPlugin();
		(loaded.app.metadataCache.getFileCache as jest.Mock).mockImplementation(
			(f: { path: string }) => cacheByPath[f.path],
		);
		loaded.plugin.dependencyResolver.invalidate = jest.fn();
		return loaded;
	}

	test('editing a note without cards keeps the graph', async () => {
		const { plugin, vaultHandlers, metaHandlers } = await setup({
			'plain.md': plainNote,
			'bare.md': noFrontmatter,
		});
		vaultHandlers.modify({ path: 'plain.md' });
		metaHandlers.changed({ path: 'plain.md' }, '', plainNote);
		vaultHandlers.modify({ path: 'bare.md' });
		metaHandlers.changed({ path: 'bare.md' }, '', noFrontmatter);
		expect(plugin.dependencyResolver.invalidate).not.toHaveBeenCalled();
	});

	test('editing a card note drops the graph', async () => {
		const { plugin, vaultHandlers } = await setup({ 'cards.md': cardNote });
		vaultHandlers.modify({ path: 'cards.md' });
		expect(plugin.dependencyResolver.invalidate).toHaveBeenCalled();
	});

	test('a note that just lost its cards drops the graph (cache still old at modify)', async () => {
		const { plugin, vaultHandlers, metaHandlers } = await setup({ 'n.md': cardNote });
		vaultHandlers.modify({ path: 'n.md' });
		expect(plugin.dependencyResolver.invalidate).toHaveBeenCalledTimes(1);
		metaHandlers.changed({ path: 'n.md' }, '', plainNote);
		expect(plugin.dependencyResolver.invalidate).toHaveBeenCalledTimes(1);
	});

	test('a note that just gained cards drops the graph once re-parsed', async () => {
		const { plugin, vaultHandlers, metaHandlers } = await setup({ 'n.md': plainNote });
		vaultHandlers.modify({ path: 'n.md' });
		expect(plugin.dependencyResolver.invalidate).not.toHaveBeenCalled();
		metaHandlers.changed({ path: 'n.md' }, '', cardNote);
		expect(plugin.dependencyResolver.invalidate).toHaveBeenCalledTimes(1);
	});

	test('an uncached note counts as possibly in the graph', async () => {
		const { plugin, vaultHandlers } = await setup({});
		vaultHandlers.modify({ path: 'new.md' });
		expect(plugin.dependencyResolver.invalidate).toHaveBeenCalled();
	});

	test('non-markdown edits keep the graph', async () => {
		const { plugin, vaultHandlers } = await setup({});
		vaultHandlers.modify({ path: 'image.png' });
		expect(plugin.dependencyResolver.invalidate).not.toHaveBeenCalled();
	});

	test('deleting a card note drops the graph; deleting a plain note does not', async () => {
		const { plugin, metaHandlers } = await setup({});
		metaHandlers.deleted({ path: 'plain.md' }, plainNote);
		expect(plugin.dependencyResolver.invalidate).not.toHaveBeenCalled();
		metaHandlers.deleted({ path: 'cards.md' }, cardNote);
		expect(plugin.dependencyResolver.invalidate).toHaveBeenCalledTimes(1);
		metaHandlers.deleted({ path: 'unknown.md' }, null);
		expect(plugin.dependencyResolver.invalidate).toHaveBeenCalledTimes(2);
	});

	test('renaming a card note drops the graph; renaming a plain note does not', async () => {
		const { plugin, vaultHandlers } = await setup({
			'new-cards.md': cardNote,
			'new-plain.md': plainNote,
		});
		vaultHandlers.rename({ path: 'new-plain.md' }, 'old-plain.md');
		expect(plugin.dependencyResolver.invalidate).not.toHaveBeenCalled();
		vaultHandlers.rename({ path: 'new-cards.md' }, 'old-cards.md');
		expect(plugin.dependencyResolver.invalidate).toHaveBeenCalledTimes(1);
	});
});
