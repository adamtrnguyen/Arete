import { App, ButtonComponent, Modal, setIcon } from 'obsidian';
import * as path from 'path';

/** What this modal needs from the plugin (main.ts satisfies it; no import of main). */
export interface CheckResultHost {
	runCheck(filePath: string): Promise<void>;
	runFix(filePath: string): Promise<void>;
}

export class CheckResultModal extends Modal {
	result: any;
	plugin: CheckResultHost;
	filePath: string;

	constructor(app: App, plugin: CheckResultHost, result: any, filePath: string) {
		super(app);
		this.plugin = plugin;
		this.result = result;
		this.filePath = filePath;
	}

	onOpen() {
		const { contentEl, modalEl } = this;
		modalEl.addClass('arete-check-modal');
		contentEl.addClass('arete-modal-content');

		this.setTitle('Arete file check');
		contentEl.createDiv({ text: path.basename(this.filePath), cls: 'arete-filename' });

		if (this.result.ok) {
			const status = contentEl.createDiv({ cls: 'arete-success' });
			setIcon(status.createSpan(), 'check');
			status.createSpan({ text: 'Valid' });

			const ul = contentEl.createEl('ul', { cls: 'arete-stats' });

			const li1 = ul.createEl('li');
			li1.createSpan({ text: 'Deck', cls: 'arete-stats-label' });
			li1.createSpan({ text: this.result.stats.deck || 'None', cls: 'arete-stats-val' });

			const li2 = ul.createEl('li');
			li2.createSpan({ text: 'Cards found', cls: 'arete-stats-label' });
			li2.createSpan({
				text: this.result.stats.cards_found.toString(),
				cls: 'arete-stats-val',
			});
		} else {
			const status = contentEl.createDiv({ cls: 'arete-error' });
			setIcon(status.createSpan(), 'circle-x');
			status.createSpan({ text: 'Validation failed' });

			const table = contentEl.createEl('table', { cls: 'arete-error-table' });
			const head = table.createEl('thead');
			const row = head.createEl('tr');
			row.createEl('th', { text: 'Line' });
			row.createEl('th', { text: 'Error message' });

			const body = table.createEl('tbody');
			let fixable = false;

			this.result.errors.forEach((err: any) => {
				const tr = body.createEl('tr');
				tr.createEl('td', { text: err.line.toString(), cls: 'arete-error-line' });
				tr.createEl('td', { text: err.message, cls: 'arete-err-msg' });

				// Detect fixable errors
				if (
					err.message.includes('Tab Character Error') ||
					err.message.includes("Missing 'cards' list")
				) {
					fixable = true;
				}
			});

			if (fixable) {
				const btnContainer = contentEl.createDiv({ cls: 'arete-btn-container' });
				const btn = new ButtonComponent(btnContainer)
					.setButtonText('Auto-fix issues')
					.setCta()
					.onClick(async () => {
						btn.setDisabled(true);
						btn.setButtonText('Fixing...');
						await this.plugin.runFix(this.filePath);
						this.close();
						// Re-run check
						this.plugin.runCheck(this.filePath);
					});
			}

			contentEl.createDiv({
				text: 'Please check the console for more details.',
				cls: 'arete-footer-note',
			});
		}
	}

	onClose() {
		const { contentEl } = this;
		contentEl.empty();
	}
}
