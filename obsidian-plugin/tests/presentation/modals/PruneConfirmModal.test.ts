import '../../test-setup';
import { App } from 'obsidian';
import { PRUNE_LIST_LIMIT, PruneConfirmModal } from '@presentation/modals/PruneConfirmModal';
import { PruneReport } from '@/domain/syncReport';

const { ButtonComponent } = jest.requireMock('obsidian');

const preview = (noteCount: number, decks: string[] = []): PruneReport => ({
	status: 'unconfirmed',
	message: '',
	notes: Array.from({ length: noteCount }, (_, i) => ({
		nid: i + 1,
		arete_id: `arete_${i}`,
		deck: 'Old::Deck',
	})),
	decks,
	note_count: noteCount,
	deck_count: decks.length,
});

/** Every text the modal wrote into its content, in order. */
function texts(modal: PruneConfirmModal): string[] {
	const out: string[] = [];
	const walk = (el: any) => {
		for (const [, opts] of el.createEl.mock.calls) if (opts?.text) out.push(opts.text);
		for (const r of el.createEl.mock.results) walk(r.value);
	};
	walk(modal.contentEl);
	return out;
}

describe('PruneConfirmModal', () => {
	beforeEach(() => {
		ButtonComponent.instances.length = 0;
	});

	test('lists the notes with their Arete id and deck, then the decks', () => {
		const modal = new PruneConfirmModal(new App(), preview(2, ['Old']));
		void modal.ask();
		modal.onOpen();

		expect(modal.setTitle).toHaveBeenCalledWith('Delete from Anki?');
		const shown = texts(modal);
		expect(shown).toContain('arete_0 in Old::Deck');
		expect(shown).toContain('arete_1 in Old::Deck');
		expect(shown).toContain('Old');
		expect(shown.join(' ')).toMatch(/These 2 Anki notes/);
	});

	test('a long list shows the first entries and counts the rest', () => {
		const modal = new PruneConfirmModal(new App(), preview(PRUNE_LIST_LIMIT + 3));
		void modal.ask();
		modal.onOpen();
		const shown = texts(modal);
		expect(shown.filter((t) => t.startsWith('arete_'))).toHaveLength(PRUNE_LIST_LIMIT);
		expect(shown).toContain('And 3 more.');
	});

	test('Delete is the warning button and Cancel, not Delete, has the focus', () => {
		const modal = new PruneConfirmModal(new App(), preview(1));
		void modal.ask();
		modal.onOpen();

		const [del, cancel] = ButtonComponent.instances;
		expect(del.setButtonText).toHaveBeenCalledWith('Delete');
		expect(del.setWarning).toHaveBeenCalled();
		expect(cancel.setButtonText).toHaveBeenCalledWith('Cancel');
		expect(cancel.buttonEl.focus).toHaveBeenCalled();
		expect(del.buttonEl.focus).not.toHaveBeenCalled();
		expect(modal.contentEl.createEl).not.toHaveBeenCalledWith('button', expect.anything());
	});

	test('Delete answers yes; Cancel and closing answer no', async () => {
		const yes = new PruneConfirmModal(new App(), preview(1));
		const askedYes = yes.ask();
		yes.onOpen();
		ButtonComponent.instances[0].clickHandler();
		yes.onClose();
		expect(await askedYes).toBe(true);

		ButtonComponent.instances.length = 0;
		const no = new PruneConfirmModal(new App(), preview(1));
		const askedNo = no.ask();
		no.onOpen();
		ButtonComponent.instances[1].clickHandler();
		expect(await askedNo).toBe(false);

		const closed = new PruneConfirmModal(new App(), preview(1));
		const askedClosed = closed.ask();
		closed.onOpen();
		closed.onClose();
		expect(await askedClosed).toBe(false);
	});
});
