import '../../test-setup';
import { App, Notice, Setting, requestUrl } from 'obsidian';
import { AreteSettingTab } from '@presentation/settings/SettingTab';
import { DEFAULT_SETTINGS } from '@domain/settings';

describe('AreteSettingTab: what each control writes and triggers', () => {
	let app: any;
	let plugin: any;
	let tab: AreteSettingTab;

	beforeEach(() => {
		jest.clearAllMocks();
		app = new App();
		app.setting = { openTabById: jest.fn(), activeTab: null };
		app.commands = { findCommand: jest.fn().mockReturnValue(null) };
		plugin = {
			settings: { ...DEFAULT_SETTINGS },
			statsCache: { concepts: { 'A.md': {} }, lastFetched: 5 },
			saveSettings: jest.fn().mockResolvedValue(undefined),
			testConfig: jest.fn().mockResolvedValue(undefined),
			templateRenderer: { setMode: jest.fn() },
			serverManager: {
				start: jest.fn(),
				stop: jest.fn(),
				restart: jest.fn().mockResolvedValue(undefined),
			},
			graphService: { clearAllTags: jest.fn().mockResolvedValue(undefined) },
		};
		tab = new AreteSettingTab(app, plugin);
	});

	const settings = () => (Setting as jest.Mock).mock.results.map((r) => r.value);
	const names = () => settings().map((s) => s.setName.mock.calls[0][0]);
	const byName = (name: string) => {
		const found = settings().filter((s) => s.setName.mock.calls[0][0] === name);
		return found[found.length - 1];
	};
	const render = () => tab.display();
	const headings = () =>
		(tab as any).containerEl.createEl.mock.calls
			.filter((c: any[]) => c[0] === 'h3')
			.map((c: any[]) => c[1].text);

	describe('visibility rules', () => {
		it('shows the base sections and hides the conditional settings by default', () => {
			plugin.settings.debug_mode = false;
			plugin.settings.sync_on_save = false;
			plugin.settings.execution_mode = 'cli';
			plugin.settings.graph_coloring_enabled = false;
			render();

			expect(headings()).toEqual([
				'General',
				'Sync Options',
				'Python Environment',
				'Execution Mode',
				'Anki Connection',
				'Card Health Analysis',
				'Graph of Forgetting',
				'Hotkeys',
			]);
			for (const hidden of [
				'Debounce Delay (ms)',
				'Server Port',
				'Auto-Reload',
				'Restart Server',
				'Tag Prefix',
				'Clear All Tags',
				'Sample Check Modal',
			]) {
				expect(names()).not.toContain(hidden);
			}
		});

		it('shows each conditional setting when its switch is on', () => {
			plugin.settings.debug_mode = true;
			plugin.settings.sync_on_save = true;
			plugin.settings.execution_mode = 'server';
			plugin.settings.graph_coloring_enabled = true;
			render();

			for (const shown of [
				'Debounce Delay (ms)',
				'Server Port',
				'Auto-Reload',
				'Restart Server',
				'Tag Prefix',
				'Clear All Tags',
				'Sample Check Modal',
			]) {
				expect(names()).toContain(shown);
			}
			expect(headings()).toContain('Debug');
		});

		it('shows the SM-2 ease threshold for sm2 and the FSRS difficulty threshold otherwise', () => {
			plugin.settings.stats_algorithm = 'sm2';
			render();
			expect(names()).toContain('Ease Threshold (%)');
			expect(names()).not.toContain('Difficulty Threshold (FSRS)');

			jest.clearAllMocks();
			plugin.settings.stats_algorithm = 'fsrs';
			render();
			expect(names()).toContain('Difficulty Threshold (FSRS)');
			expect(names()).not.toContain('Ease Threshold (%)');
		});
	});

	describe('values written to settings', () => {
		it('preview renderer: saves, then switches the template renderer mode', async () => {
			render();
			await byName('Preview Renderer').mockDropdown._onChange('anki');
			expect(plugin.settings.renderer_mode).toBe('anki');
			expect(plugin.saveSettings).toHaveBeenCalledTimes(1);
			expect(plugin.templateRenderer.setMode).toHaveBeenCalledWith('anki');
		});

		it('sync on save and its delay', async () => {
			plugin.settings.sync_on_save = true;
			render();
			await byName('Sync on Save').mockToggle._onChange(false);
			expect(plugin.settings.sync_on_save).toBe(false);

			const slider = byName('Debounce Delay (ms)').mockSlider;
			expect(slider.setLimits).toHaveBeenCalledWith(500, 5000, 500);
			expect(slider.setValue).toHaveBeenCalledWith(DEFAULT_SETTINGS.sync_on_save_delay);
			await slider._onChange(1500);
			expect(plugin.settings.sync_on_save_delay).toBe(1500);
		});

		it('project root and workers', async () => {
			render();
			await byName('Project Root').mockText._onChange('/work/arete');
			expect(plugin.settings.project_root).toBe('/work/arete');

			const slider = byName('Parallel Workers').mockSlider;
			expect(slider.setLimits).toHaveBeenCalledWith(1, 16, 1);
		});

		it('ease threshold is shown in percent and stored x10', async () => {
			plugin.settings.stats_algorithm = 'sm2';
			plugin.settings.stats_ease_threshold = 2100;
			render();
			const slider = byName('Ease Threshold (%)').mockSlider;
			expect(slider.setValue).toHaveBeenCalledWith(210);
			await slider._onChange(250);
			expect(plugin.settings.stats_ease_threshold).toBe(2500);
		});

		it('FSRS difficulty threshold is shown 0-100 and stored 0-1', async () => {
			plugin.settings.stats_algorithm = 'fsrs';
			plugin.settings.stats_difficulty_threshold = 0.9;
			render();
			const slider = byName('Difficulty Threshold (FSRS)').mockSlider;
			expect(slider.setValue).toHaveBeenCalledWith(90);
			await slider._onChange(75);
			expect(plugin.settings.stats_difficulty_threshold).toBe(0.75);
		});

		it('lapse threshold', async () => {
			render();
			await byName('Lapse Threshold').mockSlider._onChange(7);
			expect(plugin.settings.stats_lapse_threshold).toBe(7);
		});

		it('changing the algorithm discards the cached stats and re-renders', async () => {
			plugin.settings.stats_algorithm = 'sm2';
			render();
			const dropdown = byName('Algorithm').mockDropdown;
			(Setting as jest.Mock).mockClear();

			await dropdown._onChange('fsrs');

			expect(plugin.settings.stats_algorithm).toBe('fsrs');
			expect(plugin.statsCache).toEqual({ concepts: {}, lastFetched: 0 });
			expect(plugin.saveSettings).toHaveBeenCalled();
			expect(names()).toContain('Difficulty Threshold (FSRS)');
		});

		it('graph colouring: toggle saves and re-renders; prefix is stored', async () => {
			plugin.settings.graph_coloring_enabled = false;
			render();
			await byName('Enable Graph Coloring').mockToggle._onChange(true);
			expect(plugin.settings.graph_coloring_enabled).toBe(true);
			expect(names()).toContain('Tag Prefix');

			await byName('Tag Prefix').mockText._onChange('my/ret');
			expect(plugin.settings.graph_tag_prefix).toBe('my/ret');
		});
	});

	describe('Clear All Tags', () => {
		beforeEach(() => {
			plugin.settings.graph_coloring_enabled = true;
		});
		afterEach(() => {
			delete (globalThis as any).confirm;
		});

		it('is styled as a warning and clears tags only after confirmation', async () => {
			(globalThis as any).confirm = jest.fn().mockReturnValue(true);
			render();
			const button = byName('Clear All Tags').mockButton;
			expect(button.setWarning).toHaveBeenCalled();

			await button._onClick();

			expect((globalThis as any).confirm).toHaveBeenCalledWith(
				'Are you sure you want to remove all retention tags?',
			);
			expect(plugin.graphService.clearAllTags).toHaveBeenCalledTimes(1);
		});

		it('does nothing when the user declines', async () => {
			(globalThis as any).confirm = jest.fn().mockReturnValue(false);
			render();
			await byName('Clear All Tags').mockButton._onClick();
			expect(plugin.graphService.clearAllTags).not.toHaveBeenCalled();
		});
	});

	describe('execution mode', () => {
		it('cli to server starts the server', async () => {
			plugin.settings.execution_mode = 'cli';
			render();
			await byName('Mode').mockDropdown._onChange('server');
			expect(plugin.settings.execution_mode).toBe('server');
			expect(plugin.serverManager.start).toHaveBeenCalledTimes(1);
			expect(plugin.serverManager.stop).not.toHaveBeenCalled();
			expect(names()).toContain('Server Port');
		});

		it('server to cli stops the server', async () => {
			plugin.settings.execution_mode = 'server';
			render();
			await byName('Mode').mockDropdown._onChange('cli');
			expect(plugin.settings.execution_mode).toBe('cli');
			expect(plugin.serverManager.stop).toHaveBeenCalledTimes(1);
			expect(plugin.serverManager.start).not.toHaveBeenCalled();
		});

		it('re-selecting the current mode starts and stops nothing', async () => {
			plugin.settings.execution_mode = 'server';
			render();
			await byName('Mode').mockDropdown._onChange('server');
			expect(plugin.serverManager.start).not.toHaveBeenCalled();
			expect(plugin.serverManager.stop).not.toHaveBeenCalled();
		});

		it('server port accepts a number and ignores text', async () => {
			plugin.settings.execution_mode = 'server';
			plugin.settings.server_port = 8777;
			render();
			const text = byName('Server Port').mockText;
			expect(text.setValue).toHaveBeenCalledWith('8777');

			await text._onChange('9001');
			expect(plugin.settings.server_port).toBe(9001);

			plugin.saveSettings.mockClear();
			await text._onChange('abc');
			expect(plugin.settings.server_port).toBe(9001);
			expect(plugin.saveSettings).not.toHaveBeenCalled();
		});

		it('auto-reload saves and warns that a restart is needed', async () => {
			plugin.settings.execution_mode = 'server';
			render();
			await byName('Auto-Reload').mockToggle._onChange(true);
			expect(plugin.settings.server_reload).toBe(true);
			expect(Notice).toHaveBeenCalledWith('Server restart required to apply reload setting.');
		});

		it('restart button restarts the server', async () => {
			plugin.settings.execution_mode = 'server';
			render();
			await byName('Restart Server').mockButton._onClick();
			expect(plugin.serverManager.restart).toHaveBeenCalledTimes(1);
		});
	});

	describe('AnkiConnect test button', () => {
		const click = async () => {
			plugin.settings.anki_connect_url = 'http://anki:8765';
			render();
			await byName('AnkiConnect URL').mockButton._onClick();
		};

		it('posts a version request to the configured URL and reports the version', async () => {
			(requestUrl as jest.Mock).mockResolvedValue({ json: { result: 6 } });
			await click();

			const req = (requestUrl as jest.Mock).mock.calls[0][0];
			expect(req.url).toBe('http://anki:8765');
			expect(req.method).toBe('POST');
			expect(JSON.parse(req.body)).toEqual({ action: 'version', version: 6 });
			expect(Notice).toHaveBeenCalledWith('✅ AnkiConnect v6 connected!', 3000);
		});

		it('reports a response without a version', async () => {
			(requestUrl as jest.Mock).mockResolvedValue({ json: {} });
			await click();
			expect(Notice).toHaveBeenCalledWith(
				'⚠️ AnkiConnect responded but version unknown',
				5000,
			);
		});

		it('reports a connection failure', async () => {
			(requestUrl as jest.Mock).mockRejectedValue(new Error('ECONNREFUSED'));
			await click();
			expect(Notice).toHaveBeenCalledWith(
				'❌ Cannot connect to AnkiConnect. Is Anki running?',
				8000,
			);
		});
	});

	describe('hotkeys section', () => {
		it('describes each registered sync command with its current hotkey', () => {
			app.commands.findCommand.mockImplementation((id: string) => {
				if (id === 'obsidian-2-anki:arete-sync')
					return { hotkeys: [{ modifiers: ['Mod', 'Shift'], key: 'A' }] };
				if (id === 'obsidian-2-anki:arete-sync-prune') return {};
				return null;
			});
			render();

			expect(names().slice(-2)).toEqual(['Sync', 'Sync (Prune Deleted Cards)']);
			expect(byName('Sync').setDesc).toHaveBeenCalledWith('Current: Mod+Shift+A');
			expect(byName('Sync (Prune Deleted Cards)').setDesc).toHaveBeenCalledWith(
				'Current: No hotkey set',
			);
		});

		it('Configure opens the hotkeys tab filtered to arete', async () => {
			const searchComponent = { setValue: jest.fn() };
			app.setting.activeTab = { searchComponent, updateHotkeyVisibility: jest.fn() };
			app.commands.findCommand.mockReturnValue({ hotkeys: [] });
			render();

			await byName('Sync').mockButton._onClick();

			expect(app.setting.openTabById).toHaveBeenCalledWith('hotkeys');
			expect(searchComponent.setValue).toHaveBeenCalledWith('arete');
			expect(app.setting.activeTab.updateHotkeyVisibility).toHaveBeenCalled();
		});

		it('Configure still opens the tab when the hotkeys tab has no search box', async () => {
			app.setting.activeTab = null;
			app.commands.findCommand.mockReturnValue({ hotkeys: [] });
			render();
			await byName('Sync').mockButton._onClick();
			expect(app.setting.openTabById).toHaveBeenCalledWith('hotkeys');
		});
	});
});
