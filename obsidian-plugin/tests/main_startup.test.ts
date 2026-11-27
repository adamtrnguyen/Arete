import './test-setup';
import { App } from 'obsidian';
import AretePlugin from '@/main';

jest.mock('@infrastructure/arete/SyncService');
jest.mock('@infrastructure/arete/CheckService');

describe('plugin load', () => {
	let layoutReady: (() => void) | null;
	let plugin: AretePlugin;

	const load = async (data: Record<string, unknown>) => {
		jest.clearAllMocks();
		layoutReady = null;
		const app = new App();
		(app.workspace.onLayoutReady as jest.Mock).mockImplementation((cb) => {
			layoutReady = cb;
		});
		plugin = new AretePlugin(app, { dir: 'test-plugin-dir' } as any);
		plugin.loadData = jest.fn().mockResolvedValue(data);
		await plugin.onload();
	};

	test('waits for the layout, then refreshes stats exactly once', async () => {
		await load({});
		const refresh = jest.spyOn(plugin.statsService, 'refreshStats').mockResolvedValue([]);
		expect(refresh).not.toHaveBeenCalled();

		layoutReady!();
		expect(refresh).toHaveBeenCalledTimes(1);
	});

	test('a data.json from a release with the server keeps its settings, minus the server ones', async () => {
		await load({
			execution_mode: 'server',
			server_port: 9000,
			server_reload: true,
			backend: 'direct',
		});

		expect(plugin.settings).not.toHaveProperty('execution_mode');
		expect(plugin.settings).not.toHaveProperty('server_port');
		expect(plugin.settings).not.toHaveProperty('server_reload');
		expect(plugin.settings.backend).toBe('direct');
	});

	test('initializes statsCache on fresh install and persists it after stats refresh', async () => {
		await load({});
		expect(plugin.statsCache).toEqual({ concepts: {}, lastFetched: 0 });
		expect(plugin.statsService.getCache()).toBe(plugin.statsCache);

		const saveData = jest.fn().mockResolvedValue(undefined);
		plugin.saveData = saveData;
		const refreshedConcept = { filePath: 'A.md', totalCards: 1 } as any;
		jest.spyOn(plugin.statsService, 'refreshStats').mockImplementation(async () => {
			plugin.statsService.getCache().concepts['A.md'] = refreshedConcept;
			plugin.statsService.getCache().lastFetched = 12345;
			return [refreshedConcept];
		});

		layoutReady!();
		await new Promise((r) => setImmediate(r));

		expect(saveData).toHaveBeenCalledWith(
			expect.objectContaining({
				statsCache: { concepts: { 'A.md': refreshedConcept }, lastFetched: 12345 },
			}),
		);
	});
});
