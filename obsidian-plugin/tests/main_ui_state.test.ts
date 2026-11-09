import './test-setup';
import { App } from 'obsidian';
import AretePlugin from '@/main';

jest.mock('@infrastructure/arete/SyncService');
jest.mock('@infrastructure/arete/CheckService');

describe('dashboard expansion state persistence', () => {
	let plugin: AretePlugin;
	let saveData: jest.Mock;

	beforeEach(async () => {
		jest.useFakeTimers();
		const app = new App();
		plugin = new AretePlugin(app, { dir: 'test-plugin-dir' } as any);
		plugin.loadData = jest.fn().mockResolvedValue({ ui_expanded_decks: ['Math'] });
		await plugin.onload();
		saveData = jest.fn().mockResolvedValue(undefined);
		plugin.saveData = saveData;
	});
	afterEach(() => {
		jest.useRealTimers();
	});

	function toggle(decks: string[]) {
		plugin.settings.ui_expanded_decks = decks;
		plugin.requestSaveUiState();
	}

	test('a burst of toggles writes data.json once, with the final state', async () => {
		toggle(['Math', 'Bio']);
		toggle(['Math', 'Bio', 'Chem']);
		toggle(['Bio']);
		expect(saveData).not.toHaveBeenCalled();

		await jest.advanceTimersByTimeAsync(1000);
		expect(saveData).toHaveBeenCalledTimes(1);
		expect(saveData.mock.calls[0][0].ui_expanded_decks).toEqual(['Bio']);
	});

	test('toggling back to the saved state writes nothing', async () => {
		toggle(['Math', 'Bio']);
		toggle(['Math']);
		await jest.advanceTimersByTimeAsync(5000);
		expect(saveData).not.toHaveBeenCalled();
	});

	test('a pending save is flushed on unload', () => {
		toggle([]);
		plugin.onunload();
		expect(saveData).toHaveBeenCalledTimes(1);
		expect(saveData.mock.calls[0][0].ui_expanded_decks).toEqual([]);
	});
});
