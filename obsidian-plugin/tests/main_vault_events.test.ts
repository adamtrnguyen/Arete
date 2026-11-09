import './test-setup';
import { App } from 'obsidian';
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
		loaded.plugin.runSync = jest.fn().mockResolvedValue(undefined);
		return loaded;
	}

	test('saving A then B within the delay syncs both', async () => {
		const { plugin, vaultHandlers } = await setup();
		vaultHandlers.modify({ path: 'A.md' });
		await jest.advanceTimersByTimeAsync(500);
		vaultHandlers.modify({ path: 'B.md' });
		await jest.advanceTimersByTimeAsync(1000);

		expect(plugin.runSync).toHaveBeenCalledTimes(2);
		expect(plugin.runSync).toHaveBeenCalledWith(false, '/vault/A.md', false);
		expect(plugin.runSync).toHaveBeenCalledWith(false, '/vault/B.md', false);
	});

	test('repeated saves of one note sync it once', async () => {
		const { plugin, vaultHandlers } = await setup();
		vaultHandlers.modify({ path: 'A.md' });
		await jest.advanceTimersByTimeAsync(500);
		vaultHandlers.modify({ path: 'A.md' });
		await jest.advanceTimersByTimeAsync(2000);

		expect(plugin.runSync).toHaveBeenCalledTimes(1);
	});

	test('no sync fires after unload', async () => {
		const { plugin, vaultHandlers } = await setup();
		vaultHandlers.modify({ path: 'A.md' });
		vaultHandlers.modify({ path: 'B.md' });
		plugin.onunload();
		await jest.advanceTimersByTimeAsync(5000);

		expect(plugin.runSync).not.toHaveBeenCalled();
	});
});
