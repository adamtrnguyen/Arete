import '../test-setup';
import { App } from 'obsidian';
import AretePlugin from '@/main';
import { debugLog, setDebugLogSwitch } from '@domain/debugLog';
import { AreteClient } from '@infrastructure/arete/AreteClient';
import { DEFAULT_SETTINGS } from '@domain/settings';

jest.mock('@infrastructure/arete/SyncService');
jest.mock('@infrastructure/arete/CheckService');

describe('debugLog', () => {
	let log: jest.SpyInstance;
	beforeEach(() => {
		log = jest.spyOn(console, 'log').mockImplementation(() => undefined);
	});
	afterEach(() => {
		log.mockRestore();
		setDebugLogSwitch(() => false);
	});

	test('is silent by default and when the switch is off', () => {
		debugLog('hidden');
		setDebugLogSwitch(() => false);
		debugLog('hidden');
		expect(log).not.toHaveBeenCalled();
	});

	test('logs when the switch is on, reading it on every call', () => {
		let on = true;
		setDebugLogSwitch(() => on);
		debugLog('shown', { a: 1 });
		on = false;
		debugLog('hidden');
		expect(log).toHaveBeenCalledTimes(1);
		expect(log).toHaveBeenCalledWith('shown', { a: 1 });
	});

	test('plugin load and a server request log nothing with debug_mode off', async () => {
		const plugin = new AretePlugin(new App(), { dir: 'test-plugin-dir' } as any);
		plugin.loadData = jest.fn().mockResolvedValue({ debug_mode: false });
		await plugin.onload();
		await new AreteClient({ ...DEFAULT_SETTINGS, execution_mode: 'cli' })
			.invokeCLI('/anki/unknown', { nids: [1, 2, 3] })
			.catch((): void => undefined);
		expect(log).not.toHaveBeenCalled();
	});

	test('plugin load logs its settings with debug_mode on', async () => {
		const plugin = new AretePlugin(new App(), { dir: 'test-plugin-dir' } as any);
		plugin.loadData = jest.fn().mockResolvedValue({ debug_mode: true });
		await plugin.onload();
		expect(log).toHaveBeenCalledWith('[Arete] Settings loaded:', plugin.settings);
	});
});
