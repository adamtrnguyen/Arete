import './test-setup';
import { App, setIcon, setTooltip } from 'obsidian';
import AretePlugin from '@/main';

describe('AretePlugin Lifecycle and Commands', () => {
	let plugin: AretePlugin;
	let app: App;

	beforeEach(() => {
		app = new App();
		plugin = new AretePlugin(app, { dir: 'test-plugin-dir' } as any);

		// Initialize settings to prevent undefined access during onload
		plugin.settings = {
			python_path: 'python3',
			debug_mode: false,
			backend: 'auto',
			workers: 4,
			anki_connect_url: 'http://localhost:8765',
			anki_media_dir: '',
			renderer_mode: 'obsidian',
			stats_algorithm: 'sm2',
			stats_lapse_threshold: 3,
			stats_ease_threshold: 2100,
			stats_difficulty_threshold: 0.9,
			graph_coloring_enabled: false,
			graph_tag_prefix: 'arete/retention',
			sync_on_save: false,
			sync_on_save_delay: 2000,
			ui_expanded_decks: [],
			ui_expanded_concepts: [],
			last_sync_time: null,
			execution_mode: 'cli',
			server_port: 8777,
			project_root: '',
			server_reload: false,
		};

		// Mock required methods
		plugin.loadSettings = jest.fn().mockImplementation(async () => {
			// Simulate loading settings (already set above)
		});
		plugin.addStatusBarItem = jest.fn().mockImplementation(() => ({
			empty: jest.fn(),
			setText: jest.fn(),
			addClass: jest.fn(),
			addEventListener: jest.fn(),
			createSpan: jest.fn().mockImplementation(() => ({ setText: jest.fn() })),
		}));
		plugin.addRibbonIcon = jest.fn().mockImplementation((icon, title, cb) => {
			return { icon, title, cb };
		});
		plugin.addCommand = jest.fn();
		plugin.addSettingTab = jest.fn();
	});

	test('onload initializes plugin components', async () => {
		await plugin.onload();
		expect(plugin.loadSettings).toHaveBeenCalled();
		expect(plugin.addStatusBarItem).toHaveBeenCalled();
		expect(plugin.addRibbonIcon).toHaveBeenCalled();
		expect(plugin.addCommand).toHaveBeenCalled();
		expect(plugin.addSettingTab).toHaveBeenCalled();
	});

	test('ribbon icon click triggers Sync', async () => {
		await plugin.onload();
		const ribbonMock = (plugin.addRibbonIcon as jest.Mock).mock.results[0].value;
		const runSyncSpy = jest.spyOn(plugin, 'runSync').mockImplementation();

		ribbonMock.cb({} as any);
		expect(runSyncSpy).toHaveBeenCalled();
		runSyncSpy.mockRestore();
	});

	test('command callbacks trigger correct methods', async () => {
		await plugin.onload();
		const commands = (plugin.addCommand as jest.Mock).mock.calls;

		const syncCmd = commands.find((c) => c[0].id === 'arete-sync')[0];
		const runSyncSpy = jest.spyOn(plugin, 'runSync').mockImplementation();
		syncCmd.callback();
		expect(runSyncSpy).toHaveBeenCalled();

		const integrityCmd = commands.find((c) => c[0].id === 'arete-check-integrity')[0];
		const integritySpy = jest.spyOn(plugin, 'checkVaultIntegrity').mockImplementation();
		integrityCmd.callback();
		expect(integritySpy).toHaveBeenCalled();

		const pruneCmd = commands.find((c) => c[0].id === 'arete-sync-prune')[0];
		pruneCmd.callback();
		expect(runSyncSpy).toHaveBeenCalledWith(true);

		runSyncSpy.mockRestore();
		integritySpy.mockRestore();
	});

	test('updateStatusBar draws a Lucide icon and a label, with no emoji', () => {
		plugin.statusBarItem = plugin.addStatusBarItem() as any;
		(setIcon as jest.Mock).mockClear();
		const icons = () => (setIcon as jest.Mock).mock.calls.map((c) => c[1]);
		const labels = () =>
			(plugin.statusBarItem.createSpan as jest.Mock).mock.calls
				.map((c) => c[0]?.text)
				.filter(Boolean);

		plugin.updateStatusBar('idle');
		expect(plugin.statusBarItem.empty).toHaveBeenCalled();
		expect(icons()).toEqual(['layers']);
		expect(labels()).toEqual(['Arete']);

		plugin.updateStatusBar('syncing');
		expect(plugin.statusBarItem.createSpan).toHaveBeenCalledWith(
			expect.objectContaining({ text: 'Syncing...' }),
		);
		expect(icons()).toContain('refresh-cw');

		plugin.updateStatusBar('error', 'Auth Failed');
		expect(icons()).toContain('circle-x');
		expect(labels()).toContain('Error');
		expect(setTooltip).toHaveBeenCalledWith(plugin.statusBarItem, 'Auth Failed');
		expect(plugin.statusBarItem.title).toBeUndefined();

		jest.useFakeTimers();
		plugin.updateStatusBar('success');
		jest.useRealTimers();
		expect(icons()).toContain('check');
		expect(labels()).toContain('Synced');

		const emoji = /\p{Extended_Pictographic}/u;
		for (const label of labels()) expect(label).not.toMatch(emoji);
	});

	test('the revert-to-idle timer is cancelled by a newer state and by unload', () => {
		jest.useFakeTimers();
		try {
			plugin.statusBarItem = plugin.addStatusBarItem() as any;
			plugin.updateStatusBar('success');
			expect(jest.getTimerCount()).toBe(1);

			plugin.updateStatusBar('syncing');
			expect(jest.getTimerCount()).toBe(0);

			plugin.updateStatusBar('success');
			plugin.onunload();
			expect(jest.getTimerCount()).toBe(0);
		} finally {
			jest.useRealTimers();
		}
	});

	test('onunload empties status bar', () => {
		plugin.statusBarItem = plugin.addStatusBarItem() as any;
		plugin.onunload();
		expect(plugin.statusBarItem.empty).toHaveBeenCalled();
	});
});
