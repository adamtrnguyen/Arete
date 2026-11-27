import { App, ButtonComponent, Modal } from 'obsidian';

export interface ConfirmOptions {
	title: string;
	message: string;
	confirmText?: string;
	cancelText?: string;
	warning?: boolean;
}

/** Native Obsidian confirmation dialog replacing window.confirm(). */
export class ConfirmModal extends Modal {
	private options: ConfirmOptions;
	private resolve?: (confirmed: boolean) => void;
	private settled = false;

	constructor(app: App, options: ConfirmOptions) {
		super(app);
		this.options = options;
	}

	ask(): Promise<boolean> {
		return new Promise<boolean>((resolve) => {
			this.resolve = resolve;
			this.open();
		});
	}

	private finish(confirmed: boolean): void {
		if (this.settled) return;
		this.settled = true;
		this.resolve?.(confirmed);
	}

	onOpen(): void {
		const { contentEl } = this;
		contentEl.empty();
		this.setTitle(this.options.title);
		contentEl.createEl('p', { text: this.options.message });

		const buttons = contentEl.createDiv({ cls: 'modal-button-container' });
		const cancel = new ButtonComponent(buttons)
			.setButtonText(this.options.cancelText ?? 'Cancel')
			.onClick(() => {
				this.finish(false);
				this.close();
			});

		const confirm = new ButtonComponent(buttons)
			.setButtonText(this.options.confirmText ?? 'Confirm')
			.onClick(() => {
				this.finish(true);
				this.close();
			});
		if (this.options.warning !== false) {
			confirm.setWarning();
		} else {
			confirm.setCta();
		}

		cancel.buttonEl.focus();
	}

	onClose(): void {
		this.finish(false);
		this.contentEl.empty();
	}
}
