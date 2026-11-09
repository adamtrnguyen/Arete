import './test-setup';
import { App } from 'obsidian';
import AretePlugin from '@/main';
import { ServerManager } from '@infrastructure/arete/ServerManager';

jest.mock('@infrastructure/arete/SyncService');
jest.mock('@infrastructure/arete/CheckService');
jest.mock('@infrastructure/arete/ServerManager');

describe('server start at plugin load', () => {
	let layoutReady: (() => Promise<void>) | null;
	let plugin: AretePlugin;

	beforeEach(async () => {
		jest.clearAllMocks();
		layoutReady = null;
		const app = new App();
		(app.workspace.onLayoutReady as jest.Mock).mockImplementation((cb) => {
			layoutReady = cb;
		});
		plugin = new AretePlugin(app, { dir: 'test-plugin-dir' } as any);
		plugin.loadData = jest.fn().mockResolvedValue({ execution_mode: 'server' });
		await plugin.onload();
	});

	test('waits for the layout, then starts the server exactly once', async () => {
		const start = (ServerManager as jest.Mock).mock.instances[0].start as jest.Mock;
		start.mockResolvedValue(undefined);
		expect(start).not.toHaveBeenCalled();

		await layoutReady!();
		expect(start).toHaveBeenCalledTimes(1);
		expect(start).toHaveBeenCalledWith(true);
	});
});
