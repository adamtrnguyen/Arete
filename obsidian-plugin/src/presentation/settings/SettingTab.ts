import { App, Plugin, PluginSettingTab, Setting } from 'obsidian';
import { CheckResultHost, CheckResultModal } from '@presentation/modals/CheckResultModal';
import { AretePluginSettings } from '@/domain/settings';
import { StatsCache } from '@/domain/stats';
import { TemplateRenderer } from '@application/services/TemplateRenderer';
import { GraphService } from '@application/services/GraphService';

/** What the settings tab needs from the plugin (main.ts satisfies it; no import of main). */
export type SettingsHost = Plugin &
	CheckResultHost & {
		settings: AretePluginSettings;
		statsCache: StatsCache;
		templateRenderer: TemplateRenderer;
		graphService: GraphService;
		saveSettings(): Promise<void>;
		testConfig(): Promise<void>;
		testAnkiConnect(): Promise<void>;
	};

/** Quiet period after the last keystroke in a text field before the settings are saved. */
const TEXT_SAVE_DELAY_MS = 500;

export class AreteSettingTab extends PluginSettingTab {
	plugin: SettingsHost;
	private textSaveTimer: ReturnType<typeof setTimeout> | null = null;

	constructor(app: App, plugin: SettingsHost) {
		super(app, plugin);
		this.plugin = plugin;
	}

	/** Save once typing in a text field has paused; a burst of keystrokes writes once. */
	private saveSoon(): void {
		if (this.textSaveTimer) clearTimeout(this.textSaveTimer);
		this.textSaveTimer = setTimeout(() => void this.flushSave(), TEXT_SAVE_DELAY_MS);
	}

	/** Write a pending text-field change now. */
	private async flushSave(): Promise<void> {
		if (!this.textSaveTimer) return;
		clearTimeout(this.textSaveTimer);
		this.textSaveTimer = null;
		await this.plugin.saveSettings();
	}

	hide(): void {
		void this.flushSave();
	}

	display(): void {
		const { containerEl } = this;
		containerEl.empty();

		// General settings come first, under no heading.
		new Setting(containerEl)
			.setName('Debug mode')
			.setDesc('Enable verbose logging and extra output.')
			.addToggle((toggle) =>
				toggle.setValue(this.plugin.settings.debug_mode).onChange(async (value) => {
					this.plugin.settings.debug_mode = value;
					await this.plugin.saveSettings();
					this.display(); // Re-render to show/hide debug options
				}),
			);

		new Setting(containerEl)
			.setName('Preview renderer')
			.setDesc('"Obsidian" renders Markdown/LaTeX (Recommended). "Anki" passes raw text.')
			.addDropdown((dropdown) =>
				dropdown
					.addOption('obsidian', 'Obsidian (Markdown + LaTeX)')
					.addOption('anki', 'Anki (raw HTML/text)')
					.setValue(this.plugin.settings.renderer_mode)
					.onChange(async (value) => {
						this.plugin.settings.renderer_mode = value as 'obsidian' | 'anki';
						await this.plugin.saveSettings();
						this.plugin.templateRenderer.setMode(this.plugin.settings.renderer_mode);
					}),
			);

		new Setting(containerEl).setName('Sync').setHeading();

		new Setting(containerEl)
			.setName('Sync on save')
			.setDesc('Automatically sync the current file when you save (debounced).')
			.addToggle((toggle) =>
				toggle.setValue(this.plugin.settings.sync_on_save).onChange(async (value) => {
					this.plugin.settings.sync_on_save = value;
					await this.plugin.saveSettings();
				}),
			);

		if (this.plugin.settings.sync_on_save) {
			new Setting(containerEl)
				.setName('Debounce delay (ms)')
				.setDesc('Wait this long after last edit before syncing.')
				.addSlider((slider) =>
					slider
						.setLimits(500, 5000, 500)
						.setValue(this.plugin.settings.sync_on_save_delay)
						.setDynamicTooltip()
						.onChange(async (value) => {
							this.plugin.settings.sync_on_save_delay = value;
							await this.plugin.saveSettings();
						}),
				);
		}

		new Setting(containerEl).setName('Python environment').setHeading();

		new Setting(containerEl)
			.setName('Python executable')
			.setDesc('Path to python3 or arete executable')
			.addText((text) =>
				text
					.setPlaceholder('python3')
					.setValue(this.plugin.settings.python_path)
					.onChange((value) => {
						this.plugin.settings.python_path = value;
						this.saveSoon();
					}),
			);

		new Setting(containerEl)
			.setName('Project root')
			.setDesc(
				'Absolute path to the Arete project root (containing pyproject.toml). Required for "uv run".',
			)
			.addText((text) =>
				text
					.setPlaceholder('/path/to/arete')
					.setValue(this.plugin.settings.project_root)
					.onChange((value) => {
						this.plugin.settings.project_root = value;
						this.saveSoon();
					}),
			);

		new Setting(containerEl)
			.setName('Parallel workers')
			.setDesc('Number of sync workers. Higher is faster but may stress Anki.')
			.addSlider((slider) =>
				slider
					.setLimits(1, 16, 1)
					.setValue(this.plugin.settings.workers)
					.setDynamicTooltip()
					.onChange(async (value) => {
						this.plugin.settings.workers = value;
						await this.plugin.saveSettings();
					}),
			);

		new Setting(containerEl)
			.setName('Test configuration')
			.setDesc('Verify that the Python executable is valid.')
			.addButton((button) =>
				button.setButtonText('Test').onClick(async () => {
					await this.plugin.testConfig();
				}),
			);

		new Setting(containerEl).setName('Anki connection').setHeading();

		new Setting(containerEl)
			.setName('Backend')
			.setDesc('Sync driver for Anki.')
			.addDropdown((dropdown) =>
				dropdown
					.addOption('auto', 'Auto (recommended)')
					.addOption('direct', 'Direct (Anki database, requires Anki closed)')
					.addOption('ankiconnect', 'AnkiConnect (requires Anki running)')
					.setValue(this.plugin.settings.backend)
					.onChange(async (value: 'auto' | 'direct' | 'ankiconnect') => {
						this.plugin.settings.backend = value;
						await this.plugin.saveSettings();
					}),
			);

		new Setting(containerEl)
			.setName('AnkiConnect URL')
			.setDesc('URL for AnkiConnect API')
			.addText((text) =>
				text
					.setPlaceholder('http://localhost:8765')
					.setValue(this.plugin.settings.anki_connect_url)
					.onChange((value) => {
						this.plugin.settings.anki_connect_url = value;
						this.saveSoon();
					}),
			)
			.addButton((button) =>
				button.setButtonText('Test').onClick(async () => {
					await this.plugin.testAnkiConnect();
				}),
			);

		new Setting(containerEl)
			.setName('Media directory')
			.setDesc('Custom path for Anki media uploads (optional)')
			.addText((text) =>
				text
					.setPlaceholder('/path/to/Anki/collection.media')
					.setValue(this.plugin.settings.anki_media_dir)
					.onChange((value) => {
						this.plugin.settings.anki_media_dir = value;
						this.saveSoon();
					}),
			);

		new Setting(containerEl).setName('Card health analysis').setHeading();

		new Setting(containerEl)
			.setName('Algorithm')
			.setDesc('Scoring algorithm for identifying problematic cards.')
			.addDropdown((dropdown) =>
				dropdown
					.addOption('sm2', 'Classic (SM-2)')
					.addOption('fsrs', 'FSRS (new scheduler)')
					.setValue(this.plugin.settings.stats_algorithm)
					.onChange(async (value: 'sm2' | 'fsrs') => {
						this.plugin.settings.stats_algorithm = value;
						this.plugin.statsCache = { concepts: {}, lastFetched: 0 };
						await this.plugin.saveSettings();
						this.display(); // Re-render to show algorithm-specific options
					}),
			);

		new Setting(containerEl)
			.setName('Lapse threshold')
			.setDesc('Cards with this many lapses are marked problematic.')
			.addSlider((slider) =>
				slider
					.setLimits(1, 20, 1)
					.setValue(this.plugin.settings.stats_lapse_threshold)
					.setDynamicTooltip()
					.onChange(async (value) => {
						this.plugin.settings.stats_lapse_threshold = value;
						await this.plugin.saveSettings();
					}),
			);

		if (this.plugin.settings.stats_algorithm === 'sm2') {
			new Setting(containerEl)
				.setName('Ease threshold (%)')
				.setDesc('Cards with ease below this are in "ease hell". Default: 250%')
				.addSlider((slider) =>
					slider
						.setLimits(130, 300, 10)
						.setValue(this.plugin.settings.stats_ease_threshold / 10)
						.setDynamicTooltip()
						.onChange(async (value) => {
							this.plugin.settings.stats_ease_threshold = value * 10;
							await this.plugin.saveSettings();
						}),
				);
		} else {
			new Setting(containerEl)
				.setName('Difficulty threshold (FSRS)')
				.setDesc('Cards with difficulty above this (0-100%) are problematic.')
				.addSlider((slider) =>
					slider
						.setLimits(50, 100, 5)
						.setValue(this.plugin.settings.stats_difficulty_threshold * 100)
						.setDynamicTooltip()
						.onChange(async (value) => {
							this.plugin.settings.stats_difficulty_threshold = value / 100;
							await this.plugin.saveSettings();
						}),
				);
		}

		new Setting(containerEl).setName('Graph of forgetting').setHeading();

		new Setting(containerEl)
			.setName('Enable graph coloring')
			.setDesc(
				'Automatically add status tags (#arete/retention/...) to notes based on Anki stats.',
			)
			.addToggle((toggle) =>
				toggle
					.setValue(this.plugin.settings.graph_coloring_enabled)
					.onChange(async (value) => {
						this.plugin.settings.graph_coloring_enabled = value;
						await this.plugin.saveSettings();
						this.display();
					}),
			);

		if (this.plugin.settings.graph_coloring_enabled) {
			new Setting(containerEl)
				.setName('Tag prefix')
				.setDesc('Prefix for retention tags. Examples: high, med, low will be appended.')
				.addText((text) =>
					text
						.setPlaceholder('arete/retention')
						.setValue(this.plugin.settings.graph_tag_prefix)
						.onChange((value) => {
							this.plugin.settings.graph_tag_prefix = value;
							this.saveSoon();
						}),
				);

			new Setting(containerEl)
				.setName('Clear all tags')
				.setDesc('Remove all Arete retention tags from all files.')
				.addButton((button) =>
					button
						.setButtonText('Clear tags')
						.setWarning()
						.onClick(async () => {
							if (confirm('Are you sure you want to remove all retention tags?')) {
								await this.plugin.graphService.clearAllTags();
							}
						}),
				);
		}

		if (this.plugin.settings.debug_mode) {
			new Setting(containerEl).setName('Debug').setHeading();

			new Setting(containerEl)
				.setName('Sample check modal')
				.setDesc('Open a sample result modal for testing.')
				.addButton((button) =>
					button.setButtonText('Open').onClick(() => {
						new CheckResultModal(
							this.app,
							this.plugin,
							{ ok: true, stats: { deck: 'Debug', cards_found: 0 } },
							'debug-file.md',
						).open();
					}),
				);
		}
	}
}
