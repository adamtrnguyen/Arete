import { App, ButtonComponent, Modal } from 'obsidian';
import { PruneReport } from '@/domain/syncReport';

/** How many notes the modal lists by name; the rest are counted. */
export const PRUNE_LIST_LIMIT = 10;

/**
 * Asks before prune deletes. Lists the notes (Arete id and deck) and decks the preview
 * found, and resolves `ask()` with true only when Delete is pressed. Cancel has the
 * focus, so Enter does not delete.
 */
export class PruneConfirmModal extends Modal {
	private answer: ((confirmed: boolean) => void) | null = null;

	constructor(
		app: App,
		private preview: PruneReport,
	) {
		super(app);
	}

	/** Open the modal; resolves true on Delete, false on Cancel or close. */
	ask(): Promise<boolean> {
		return new Promise((resolve) => {
			this.answer = resolve;
			this.open();
		});
	}

	onOpen() {
		const { contentEl } = this;
		const { notes, decks, note_count: noteCount, deck_count: deckCount } = this.preview;
		this.setTitle('Delete from Anki?');

		if (noteCount > 0) {
			contentEl.createEl('p', {
				text:
					noteCount === 1
						? 'This Anki note is no longer in the vault and will be deleted, with its review history:'
						: `These ${noteCount} Anki notes are no longer in the vault and will be deleted, with their review history:`,
			});
			const list = contentEl.createEl('ul');
			for (const note of notes.slice(0, PRUNE_LIST_LIMIT)) {
				list.createEl('li', { text: `${note.arete_id} in ${note.deck}` });
			}
			if (noteCount > PRUNE_LIST_LIMIT) {
				contentEl.createEl('p', { text: `And ${noteCount - PRUNE_LIST_LIMIT} more.` });
			}
		}
		if (deckCount > 0) {
			contentEl.createEl('p', {
				text:
					deckCount === 1
						? 'This deck holds only those notes and will be deleted:'
						: `These ${deckCount} decks hold only those notes and will be deleted:`,
			});
			const list = contentEl.createEl('ul');
			for (const deck of decks) list.createEl('li', { text: deck });
		}

		const buttons = contentEl.createDiv({ cls: 'modal-button-container' });
		new ButtonComponent(buttons)
			.setButtonText('Delete')
			.setWarning()
			.onClick(() => this.finish(true));
		const cancel = new ButtonComponent(buttons)
			.setButtonText('Cancel')
			.onClick(() => this.finish(false));
		cancel.buttonEl.focus();
	}

	onClose() {
		this.contentEl.empty();
		this.resolve(false);
	}

	private finish(confirmed: boolean) {
		this.resolve(confirmed);
		this.close();
	}

	/** Answer `ask()` once; Escape and the close button answer no. */
	private resolve(confirmed: boolean) {
		const answer = this.answer;
		this.answer = null;
		answer?.(confirmed);
	}
}
