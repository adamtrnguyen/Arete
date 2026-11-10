import '../../test-setup';
import { App, Setting } from 'obsidian';
import AretePlugin from '@/main';
import { AreteSettingTab } from '@presentation/settings/SettingTab';

describe('AreteSettingTab Interaction Tests', () => {
	let plugin: AretePlugin;
	let app: App;
	let settingTab: AreteSettingTab;

	beforeEach(() => {
		jest.clearAllMocks();
		app = new App();
		plugin = new AretePlugin(app, { dir: 'test-plugin-dir' } as any);
		plugin.settings = {
			python_path: 'python',
			debug_mode: false,
			backend: 'auto',
			workers: 4,
		} as any;
		plugin.saveSettings = jest.fn().mockResolvedValue(undefined);
		plugin.testConfig = jest.fn().mockResolvedValue(undefined);
		settingTab = new AreteSettingTab(app, plugin);

		(app as any).setting = {
			openTabById: jest.fn(),
			activeTab: {
				searchComponent: { setValue: jest.fn() },
				updateHotkeyVisibility: jest.fn(),
			},
		};
		(app as any).commands = {
			listCommands: jest.fn().mockReturnValue([]),
			findCommand: jest.fn().mockReturnValue({ hotkeys: [] }),
		};
	});

	const findSettingByName = (name: string) => {
		return (Setting as jest.Mock).mock.results
			.map((r) => r.value)
			.find((s) => s.setName.mock.calls.some((c: any) => c[0] === name));
	};

	test('Python executable setting updates correctly', async () => {
		settingTab.display();
		const setting = findSettingByName('Python executable');
		expect(setting).toBeDefined();

		setting.mockText._onChange('new-python');
		expect(plugin.settings.python_path).toBe('new-python');
		expect(plugin.saveSettings).not.toHaveBeenCalled();

		settingTab.hide();
		expect(plugin.saveSettings).toHaveBeenCalledTimes(1);
	});

	test('Debug mode toggle updates correctly', async () => {
		settingTab.display();
		const setting = findSettingByName('Debug mode');
		expect(setting).toBeDefined();

		await setting.mockToggle._onChange(true);
		expect(plugin.settings.debug_mode).toBe(true);
		expect(plugin.saveSettings).toHaveBeenCalled();
	});

	test('Anki Backend dropdown updates correctly', async () => {
		settingTab.display();
		const setting = findSettingByName('Backend');
		expect(setting).toBeDefined();

		await setting.mockDropdown._onChange('direct');
		expect(plugin.settings.backend).toBe('direct');
		expect(plugin.saveSettings).toHaveBeenCalled();
	});

	test('Parallel workers slider updates correctly', async () => {
		settingTab.display();
		const setting = findSettingByName('Parallel workers');
		expect(setting).toBeDefined();

		await setting.mockSlider._onChange(8);
		expect(plugin.settings.workers).toBe(8);
		expect(plugin.saveSettings).toHaveBeenCalled();
	});

	test('builds no hotkey section and touches no private API', () => {
		settingTab.display();
		const names = (Setting as jest.Mock).mock.results.flatMap((r) =>
			r.value.setName.mock.calls.map((c: any) => c[0]),
		);
		expect(names).not.toContain('Hotkeys');
		for (const result of (Setting as jest.Mock).mock.results) {
			const labels = result.value.mockButton?.setButtonText.mock.calls.flat() ?? [];
			expect(labels).not.toContain('Configure');
		}
		expect((app as any).commands.findCommand).not.toHaveBeenCalled();
		expect((app as any).setting.openTabById).not.toHaveBeenCalled();
	});

	test('headings are Setting rows, with no top-level or General heading', () => {
		settingTab.display();
		const headings = (Setting as jest.Mock).mock.results
			.map((r) => r.value)
			.filter((s) => s.setHeading.mock.calls.length > 0)
			.map((s) => s.setName.mock.calls[0][0]);
		expect(headings).toEqual([
			'Sync',
			'Python environment',
			'Anki connection',
			'Card health analysis',
			'Graph of forgetting',
		]);
		expect(settingTab.containerEl.createEl).not.toHaveBeenCalled();
	});

	test('setting names and headings are in sentence case', () => {
		plugin.settings.debug_mode = true;
		settingTab.display();
		for (const result of (Setting as jest.Mock).mock.results) {
			for (const [name] of result.value.setName.mock.calls) {
				const words = (name as string).split(' ').slice(1);
				for (const word of words) {
					expect(
						['AnkiConnect', 'Anki', 'FSRS', 'Python'].includes(word) ||
							!/^[A-Z][a-z]/.test(word),
					).toBe(true);
				}
			}
		}
	});

	test('a burst of typing in a text field saves once, after a pause', () => {
		jest.useFakeTimers();
		try {
			settingTab.display();
			const setting = findSettingByName('Project root');
			for (const text of ['/a', '/ab', '/abc']) setting.mockText._onChange(text);
			expect(plugin.saveSettings).not.toHaveBeenCalled();

			jest.advanceTimersByTime(600);
			expect(plugin.saveSettings).toHaveBeenCalledTimes(1);
			expect(plugin.settings.project_root).toBe('/abc');
		} finally {
			jest.useRealTimers();
		}
	});

	test('AnkiConnect Test button delegates to the plugin', async () => {
		plugin.testAnkiConnect = jest.fn().mockResolvedValue(undefined);
		settingTab.display();
		const setting = findSettingByName('AnkiConnect URL');
		await setting.mockButton._onClick();
		expect(plugin.testAnkiConnect).toHaveBeenCalled();
	});

	test('Anki Connect URL setting updates correctly', async () => {
		settingTab.display();
		const setting = findSettingByName('AnkiConnect URL');
		expect(setting).toBeDefined();

		setting.mockText._onChange('http://some-anki:1234');
		expect(plugin.settings.anki_connect_url).toBe('http://some-anki:1234');
		settingTab.hide();
		expect(plugin.saveSettings).toHaveBeenCalledTimes(1);
	});

	test('Anki Media Directory setting updates correctly', async () => {
		settingTab.display();
		const setting = findSettingByName('Media directory');
		expect(setting).toBeDefined();

		setting.mockText._onChange('/tmp/media');
		expect(plugin.settings.anki_media_dir).toBe('/tmp/media');
		settingTab.hide();
		expect(plugin.saveSettings).toHaveBeenCalledTimes(1);
	});

	test('Test Config button calls plugin method', async () => {
		settingTab.display();
		const testBtn = findSettingByName('Test configuration');
		expect(testBtn).toBeDefined();
		await testBtn.mockButton._onClick();
		expect(plugin.testConfig).toHaveBeenCalled();
	});

	test('Open Sample Modal button works', async () => {
		plugin.settings.debug_mode = true;
		settingTab.display();
		const debugSetting = findSettingByName('Sample check modal');
		expect(debugSetting).toBeDefined();

		await debugSetting.mockButton._onClick();
		// Just verify it doesn't crash
	});
});
