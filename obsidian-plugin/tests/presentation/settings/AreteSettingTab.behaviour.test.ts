import '../../test-setup';
import { App, ButtonComponent, Setting, requestUrl } from 'obsidian';
import { AreteSettingTab } from '@presentation/settings/SettingTab';
import { ConfirmModal } from '@presentation/modals/ConfirmModal';
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
		let clearedCache: { concepts: Record<string, unknown>; lastFetched: number } = {
			concepts: { 'A.md': {} },
			lastFetched: 5,
		};
		plugin = {
			settings: { ...DEFAULT_SETTINGS },
			statsCache: clearedCache,
			statsService: {
				clearCache: jest.fn(() => {
					clearedCache = { concepts: {}, lastFetched: 0 };
				}),
				getCache: jest.fn(() => clearedCache),
				recomputeProblematicCards: jest.fn(),
			},
			saveSettings: jest.fn().mockResolvedValue(undefined),
			testConfig: jest.fn().mockResolvedValue(undefined),
			testAnkiConnect: jest.fn().mockResolvedValue(undefined),
			templateRenderer: { setMode: jest.fn() },
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
			.filter((c: any[]) => /^h[1-6]$/.test(c[0]))
			.map((c: any[]) => c[1].text);
	const headingRows = () =>
		settings()
			.filter((s) => s.setHeading.mock.calls.length > 0)
			.map((s) => s.setName.mock.calls[0][0]);

	describe('visibility rules', () => {
		it('shows the base sections and hides the conditional settings by default', () => {
			plugin.settings.debug_mode = false;
			plugin.settings.sync_on_save = false;
			plugin.settings.graph_coloring_enabled = false;
			render();

			expect(headings()).toEqual([]);
			expect(headingRows()).toEqual([
				'Sync',
				'Python environment',
				'Anki connection',
				'Card health analysis',
				'Graph of forgetting',
			]);
			for (const hidden of [
				'Debounce delay (ms)',
				'Tag prefix',
				'Clear all tags',
				'Sample check modal',
			]) {
				expect(names()).not.toContain(hidden);
			}
		});

		it('shows each conditional setting when its switch is on', () => {
			plugin.settings.debug_mode = true;
			plugin.settings.sync_on_save = true;
			plugin.settings.graph_coloring_enabled = true;
			render();

			for (const shown of [
				'Debounce delay (ms)',
				'Tag prefix',
				'Clear all tags',
				'Sample check modal',
			]) {
				expect(names()).toContain(shown);
			}
			expect(headingRows()).toContain('Debug');
		});

		it('shows the SM-2 ease threshold for sm2 and the FSRS difficulty threshold otherwise', () => {
			plugin.settings.stats_algorithm = 'sm2';
			render();
			expect(names()).toContain('Ease threshold (%)');
			expect(names()).not.toContain('Difficulty threshold (FSRS)');

			jest.clearAllMocks();
			plugin.settings.stats_algorithm = 'fsrs';
			render();
			expect(names()).toContain('Difficulty threshold (FSRS)');
			expect(names()).not.toContain('Ease threshold (%)');
		});
	});

	describe('values written to settings', () => {
		it('preview renderer: saves, then switches the template renderer mode', async () => {
			render();
			await byName('Preview renderer').mockDropdown._onChange('anki');
			expect(plugin.settings.renderer_mode).toBe('anki');
			expect(plugin.saveSettings).toHaveBeenCalledTimes(1);
			expect(plugin.templateRenderer.setMode).toHaveBeenCalledWith('anki');
		});

		it('sync on save toggles re-render and updates its delay', async () => {
			plugin.settings.sync_on_save = false;
			render();
			expect(names()).not.toContain('Debounce delay (ms)');

			await byName('Sync on save').mockToggle._onChange(true);
			expect(plugin.settings.sync_on_save).toBe(true);
			expect(names()).toContain('Debounce delay (ms)');

			const slider = byName('Debounce delay (ms)').mockSlider;
			expect(slider.setLimits).toHaveBeenCalledWith(500, 5000, 500);
			expect(slider.setValue).toHaveBeenCalledWith(DEFAULT_SETTINGS.sync_on_save_delay);
			await slider._onChange(1500);
			expect(plugin.settings.sync_on_save_delay).toBe(1500);
		});

		it('project root and workers', async () => {
			render();
			byName('Project root').mockText._onChange('/work/arete');
			expect(plugin.settings.project_root).toBe('/work/arete');
			expect(plugin.saveSettings).not.toHaveBeenCalled();
			tab.hide();
			expect(plugin.saveSettings).toHaveBeenCalledTimes(1);

			const slider = byName('Parallel workers').mockSlider;
			expect(slider.setLimits).toHaveBeenCalledWith(1, 16, 1);
		});

		it('ease threshold is shown in percent, states Default: 210%, stores x10, and recomputes stats', async () => {
			plugin.settings.stats_algorithm = 'sm2';
			plugin.settings.stats_ease_threshold = 2100;
			render();
			const easeSetting = byName('Ease threshold (%)');
			expect(easeSetting.setDesc).toHaveBeenCalledWith(
				expect.stringContaining('Default: 210%'),
			);
			const slider = easeSetting.mockSlider;
			expect(slider.setValue).toHaveBeenCalledWith(210);
			await slider._onChange(250);
			expect(plugin.settings.stats_ease_threshold).toBe(2500);
			expect(plugin.statsService.recomputeProblematicCards).toHaveBeenCalledWith(
				plugin.settings,
			);
		});

		it('FSRS difficulty threshold is shown 0-100, stored 0-1, and recomputes stats', async () => {
			plugin.settings.stats_algorithm = 'fsrs';
			plugin.settings.stats_difficulty_threshold = 0.9;
			render();
			const slider = byName('Difficulty threshold (FSRS)').mockSlider;
			expect(slider.setValue).toHaveBeenCalledWith(90);
			await slider._onChange(75);
			expect(plugin.settings.stats_difficulty_threshold).toBe(0.75);
			expect(plugin.statsService.recomputeProblematicCards).toHaveBeenCalledWith(
				plugin.settings,
			);
		});

		it('lapse threshold recomputes problematic stats and saves', async () => {
			render();
			await byName('Lapse threshold').mockSlider._onChange(7);
			expect(plugin.settings.stats_lapse_threshold).toBe(7);
			expect(plugin.statsService.recomputeProblematicCards).toHaveBeenCalledWith(
				plugin.settings,
			);
		});

		it('changing the algorithm discards the cached stats and re-renders', async () => {
			plugin.settings.stats_algorithm = 'sm2';
			render();
			const dropdown = byName('Algorithm').mockDropdown;
			(Setting as jest.Mock).mockClear();

			await dropdown._onChange('fsrs');

			expect(plugin.settings.stats_algorithm).toBe('fsrs');
			expect(plugin.statsService.clearCache).toHaveBeenCalledTimes(1);
			expect(plugin.statsCache).toEqual({ concepts: {}, lastFetched: 0 });
			expect(plugin.saveSettings).toHaveBeenCalled();
			expect(names()).toContain('Difficulty threshold (FSRS)');
		});

		it('graph colouring: toggle saves and re-renders; prefix is stored', async () => {
			plugin.settings.graph_coloring_enabled = false;
			render();
			await byName('Enable graph coloring').mockToggle._onChange(true);
			expect(plugin.settings.graph_coloring_enabled).toBe(true);
			expect(names()).toContain('Tag prefix');

			byName('Tag prefix').mockText._onChange('my/ret');
			expect(plugin.settings.graph_tag_prefix).toBe('my/ret');
			tab.hide();
			expect(plugin.saveSettings).toHaveBeenCalled();
		});
	});

	describe('Clear all tags', () => {
		beforeEach(() => {
			plugin.settings.graph_coloring_enabled = true;
			(ButtonComponent as any).instances.length = 0;
		});

		it('is styled as a warning and clears tags only after ConfirmModal confirmation', async () => {
			const openSpy = jest.spyOn(ConfirmModal.prototype, 'open').mockImplementation(function (
				this: ConfirmModal,
			) {
				this.onOpen();
			});
			render();
			const button = byName('Clear all tags').mockButton;
			expect(button.setWarning).toHaveBeenCalled();

			const pending = button._onClick();
			const confirmBtn = (ButtonComponent as any).instances[1];
			confirmBtn.clickHandler();
			await pending;

			expect(plugin.graphService.clearAllTags).toHaveBeenCalledTimes(1);
			openSpy.mockRestore();
		});

		it('does nothing when the user cancels ConfirmModal', async () => {
			const openSpy = jest.spyOn(ConfirmModal.prototype, 'open').mockImplementation(function (
				this: ConfirmModal,
			) {
				this.onOpen();
			});
			render();
			const pending = byName('Clear all tags').mockButton._onClick();
			const cancelBtn = (ButtonComponent as any).instances[0];
			cancelBtn.clickHandler();
			await pending;

			expect(plugin.graphService.clearAllTags).not.toHaveBeenCalled();
			openSpy.mockRestore();
		});
	});

	it('offers no execution mode, server port or server restart', () => {
		plugin.settings.debug_mode = true;
		render();
		for (const gone of [
			'Execution mode',
			'Mode',
			'Server port',
			'Auto-reload',
			'Restart server',
		]) {
			expect(names()).not.toContain(gone);
		}
	});

	describe('AnkiConnect test button', () => {
		it('delegates to the plugin, which owns the request and the notices', async () => {
			render();
			await byName('AnkiConnect URL').mockButton._onClick();
			expect(plugin.testAnkiConnect).toHaveBeenCalledTimes(1);
			expect(requestUrl).not.toHaveBeenCalled();
		});
	});

	describe('hotkeys', () => {
		it('builds no hotkey rows and reads no private command API', () => {
			render();
			expect(names()).not.toContain('Sync (Prune Deleted Cards)');
			expect(names()).not.toContain('Hotkeys');
			expect(app.commands.findCommand).not.toHaveBeenCalled();
			expect(app.setting.openTabById).not.toHaveBeenCalled();
		});
	});
});
