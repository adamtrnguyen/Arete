import { App, Notice, PluginSettingTab, requestUrl, Setting } from 'obsidian';
import AretePlugin from '@/main';
import { CheckResultModal } from '@presentation/modals/CheckResultModal';

export class AreteSettingTab extends PluginSettingTab {
	plugin: AretePlugin;

	constructor(app: App, plugin: AretePlugin) {
		super(app, plugin);
		this.plugin = plugin;
	}

	display(): void {
		const { containerEl } = this;
		containerEl.empty();

		containerEl.createEl('h2', { text: 'Arete Settings' });

		// ═══════════════════════════════════════════════════════════════════
		// GENERAL
		// ═══════════════════════════════════════════════════════════════════
		containerEl.createEl('h3', { text: 'General' });

		new Setting(containerEl)
			.setName('Debug Mode')
			.setDesc('Enable verbose logging and extra output.')
			.addToggle((toggle) =>
				toggle.setValue(this.plugin.settings.debug_mode).onChange(async (value) => {
					this.plugin.settings.debug_mode = value;
					await this.plugin.saveSettings();
					this.display(); // Re-render to show/hide debug options
				}),
			);

		new Setting(containerEl)
			.setName('Preview Renderer')
			.setDesc('"Obsidian" renders Markdown/LaTeX (Recommended). "Anki" passes raw text.')
			.addDropdown((dropdown) =>
				dropdown
					.addOption('obsidian', 'Obsidian (Markdown + LaTeX)')
					.addOption('anki', 'Anki (Raw HTML/Text)')
					.setValue(this.plugin.settings.renderer_mode)
					.onChange(async (value) => {
						this.plugin.settings.renderer_mode = value as 'obsidian' | 'anki';
						await this.plugin.saveSettings();
						this.plugin.templateRenderer.setMode(this.plugin.settings.renderer_mode);
					}),
			);

		// ═══════════════════════════════════════════════════════════════════
		// SYNC OPTIONS
		// ═══════════════════════════════════════════════════════════════════
		containerEl.createEl('h3', { text: 'Sync Options' });

		new Setting(containerEl)
			.setName('Sync on Save')
			.setDesc('Automatically sync the current file when you save (debounced).')
			.addToggle((toggle) =>
				toggle.setValue(this.plugin.settings.sync_on_save).onChange(async (value) => {
					this.plugin.settings.sync_on_save = value;
					await this.plugin.saveSettings();
				}),
			);

		if (this.plugin.settings.sync_on_save) {
			new Setting(containerEl)
				.setName('Debounce Delay (ms)')
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

		// ═══════════════════════════════════════════════════════════════════
		// PYTHON ENVIRONMENT
		// ═══════════════════════════════════════════════════════════════════
		containerEl.createEl('h3', { text: 'Python Environment' });

		new Setting(containerEl)
			.setName('Python Executable')
			.setDesc('Path to python3 or arete executable')
			.addText((text) =>
				text
					.setPlaceholder('python3')
					.setValue(this.plugin.settings.python_path)
					.onChange(async (value) => {
						this.plugin.settings.python_path = value;
						await this.plugin.saveSettings();
					}),
			);

		new Setting(containerEl)
			.setName('Arete Script Path')
			.setDesc('Absolute path to arete/main.py. Leave empty for global binary.')
			.addText((text) =>
				text
					.setPlaceholder('/path/to/arete/main.py')
					.setValue(this.plugin.settings.arete_script_path)
					.onChange(async (value) => {
						this.plugin.settings.arete_script_path = value;
						await this.plugin.saveSettings();
					}),
			);

		new Setting(containerEl)
			.setName('Project Root')
			.setDesc(
				'Absolute path to the Arete project root (containing pyproject.toml). Required for "uv run".',
			)
			.addText((text) =>
				text
					.setPlaceholder('/path/to/arete')
					.setValue(this.plugin.settings.project_root)
					.onChange(async (value) => {
						this.plugin.settings.project_root = value;
						await this.plugin.saveSettings();
					}),
			);

		new Setting(containerEl)
			.setName('Parallel Workers')
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
			.setName('Test Configuration')
			.setDesc('Verify Python executable is valid.')
			.addButton((button) =>
				button.setButtonText('Test').onClick(async () => {
					await this.plugin.testConfig();
				}),
			);

		// ═══════════════════════════════════════════════════════════════════
		// EXECUTION MODE
		// ═══════════════════════════════════════════════════════════════════
		containerEl.createEl('h3', { text: 'Execution Mode' });

		new Setting(containerEl)
			.setName('Mode')
			.setDesc(
				'CLI (Classic) spawns a process for each sync. Server (Faster) keeps a background process running.',
			)
			.addDropdown((dropdown) =>
				dropdown
					.addOption('cli', 'CLI (Run Once)')
					.addOption('server', 'Server (Background)')
					.setValue(this.plugin.settings.execution_mode)
					.onChange(async (value: 'cli' | 'server') => {
						const previousMode = this.plugin.settings.execution_mode;
						this.plugin.settings.execution_mode = value;
						await this.plugin.saveSettings();

						if (value === 'server' && previousMode === 'cli') {
							this.plugin.serverManager.start();
						} else if (value === 'cli' && previousMode === 'server') {
							this.plugin.serverManager.stop();
						}

						this.display(); // Re-render defaults
					}),
			);

		if (this.plugin.settings.execution_mode === 'server') {
			new Setting(containerEl)
				.setName('Server Port')
				.setDesc('Port for the Arete persistent server.')
				.addText((text) =>
					text
						.setValue(this.plugin.settings.server_port.toString())
						.onChange(async (value) => {
							const port = parseInt(value);
							if (!isNaN(port)) {
								this.plugin.settings.server_port = port;
								await this.plugin.saveSettings();
							}
						}),
				);

			new Setting(containerEl)
				.setName('Auto-Reload')
				.setDesc(
					'Automatically reload the server when code changes. (Requires Arete source/dev install).',
				)
				.addToggle((toggle) =>
					toggle.setValue(this.plugin.settings.server_reload).onChange(async (value) => {
						this.plugin.settings.server_reload = value;
						await this.plugin.saveSettings();
						new Notice('Server restart required to apply reload setting.');
					}),
				);

			new Setting(containerEl)
				.setName('Restart Server')
				.setDesc('Stop and start the Arete server immediately.')
				.addButton((button) =>
					button.setButtonText('Restart').onClick(async () => {
						await this.plugin.serverManager.restart();
					}),
				);
		}

		// ═══════════════════════════════════════════════════════════════════
		// ANKI CONNECTION
		// ═══════════════════════════════════════════════════════════════════
		containerEl.createEl('h3', { text: 'Anki Connection' });

		new Setting(containerEl)
			.setName('Backend')
			.setDesc('Sync driver for Anki.')
			.addDropdown((dropdown) =>
				dropdown
					.addOption('auto', 'Auto (Recommended)')
					.addOption('apy', 'Apy (Direct DB, Faster)')
					.addOption('ankiconnect', 'AnkiConnect (Requires Anki running)')
					.setValue(this.plugin.settings.backend)
					.onChange(async (value: 'auto' | 'apy' | 'ankiconnect') => {
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
					.onChange(async (value) => {
						this.plugin.settings.anki_connect_url = value;
						await this.plugin.saveSettings();
					}),
			)
			.addButton((button) =>
				button.setButtonText('Test').onClick(async () => {
					try {
						const response = await requestUrl({
							url: this.plugin.settings.anki_connect_url,
							method: 'POST',
							body: JSON.stringify({ action: 'version', version: 6 }),
							contentType: 'application/json',
						});
						if (response.json?.result) {
							new Notice(`✅ AnkiConnect v${response.json.result} connected!`, 3000);
						} else {
							new Notice('⚠️ AnkiConnect responded but version unknown', 5000);
						}
					} catch (e) {
						new Notice('❌ Cannot connect to AnkiConnect. Is Anki running?', 8000);
					}
				}),
			);

		new Setting(containerEl)
			.setName('Media Directory')
			.setDesc('Custom path for Anki media uploads (optional)')
			.addText((text) =>
				text
					.setPlaceholder('/path/to/Anki/collection.media')
					.setValue(this.plugin.settings.anki_media_dir)
					.onChange(async (value) => {
						this.plugin.settings.anki_media_dir = value;
						await this.plugin.saveSettings();
					}),
			);

		// ═══════════════════════════════════════════════════════════════════
		// CARD HEALTH ANALYSIS
		// ═══════════════════════════════════════════════════════════════════
		containerEl.createEl('h3', { text: 'Card Health Analysis' });

		new Setting(containerEl)
			.setName('Algorithm')
			.setDesc('Scoring algorithm for identifying problematic cards.')
			.addDropdown((dropdown) =>
				dropdown
					.addOption('sm2', 'Classic (SM-2)')
					.addOption('fsrs', 'FSRS (New Scheduler)')
					.setValue(this.plugin.settings.stats_algorithm)
					.onChange(async (value: 'sm2' | 'fsrs') => {
						this.plugin.settings.stats_algorithm = value;
						this.plugin.statsCache = { concepts: {}, lastFetched: 0 };
						await this.plugin.saveSettings();
						this.display(); // Re-render to show algorithm-specific options
					}),
			);

		new Setting(containerEl)
			.setName('Lapse Threshold')
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
				.setName('Ease Threshold (%)')
				.setDesc('Cards with ease below this are in "Ease Hell". Default: 250%')
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
				.setName('Difficulty Threshold (FSRS)')
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

		// ═══════════════════════════════════════════════════════════════════
		// GRAPH OF FORGETTING
		// ═══════════════════════════════════════════════════════════════════
		containerEl.createEl('h3', { text: 'Graph of Forgetting' });

		new Setting(containerEl)
			.setName('Enable Graph Coloring')
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
				.setName('Tag Prefix')
				.setDesc('Prefix for retention tags. Examples: high, med, low will be appended.')
				.addText((text) =>
					text
						.setPlaceholder('arete/retention')
						.setValue(this.plugin.settings.graph_tag_prefix)
						.onChange(async (value) => {
							this.plugin.settings.graph_tag_prefix = value;
							await this.plugin.saveSettings();
						}),
				);

			new Setting(containerEl)
				.setName('Clear All Tags')
				.setDesc('Remove all Arete retention tags from all files.')
				.addButton((button) =>
					button
						.setButtonText('Clear Tags')
						.setWarning()
						.onClick(async () => {
							if (confirm('Are you sure you want to remove all retention tags?')) {
								await this.plugin.graphService.clearAllTags();
							}
						}),
				);
		}

		// ═══════════════════════════════════════════════════════════════════
		// DEBUG (Conditional)
		// ═══════════════════════════════════════════════════════════════════
		if (this.plugin.settings.debug_mode) {
			containerEl.createEl('h3', { text: 'Debug' });

			new Setting(containerEl)
				.setName('Sample Check Modal')
				.setDesc('Opens a sample result modal for testing.')
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

		// ═══════════════════════════════════════════════════════════════════
		// HOTKEYS
		// ═══════════════════════════════════════════════════════════════════
		containerEl.createEl('h3', { text: 'Hotkeys' });
		containerEl.createEl('p', {
			text: 'Click Configure to open Obsidian Hotkey settings.',
			cls: 'setting-item-description',
		});

		const commands = [
			{ id: 'obsidian-2-anki:arete-sync', name: 'Sync' },
			{ id: 'obsidian-2-anki:arete-sync-current-file', name: 'Sync Current File' },
			{ id: 'obsidian-2-anki:arete-sync-prune', name: 'Sync (Prune Deleted Cards)' },
			{ id: 'obsidian-2-anki:arete-sync-force-all', name: 'Sync (Force Re-upload All)' },
		];

		commands.forEach((cmd) => {
			const command = (this.app as any).commands.findCommand(cmd.id);
			if (!command) return;

			const hotkeys = command.hotkeys || [];
			const hotkeyStr =
				hotkeys
					.map((h: any) => {
						const mods = h.modifiers.join('+');
						return `${mods}+${h.key}`;
					})
					.join(', ') || 'No hotkey set';

			new Setting(containerEl)
				.setName(cmd.name)
				.setDesc(`Current: ${hotkeyStr}`)
				.addButton((button) =>
					button.setButtonText('Configure').onClick(() => {
						(this.app as any).setting.openTabById('hotkeys');
						const hotkeysTab = (this.app as any).setting.activeTab;
						if (hotkeysTab && hotkeysTab.searchComponent) {
							hotkeysTab.searchComponent.setValue('arete');
							hotkeysTab.updateHotkeyVisibility();
						}
					}),
				);
		});
	}
}
