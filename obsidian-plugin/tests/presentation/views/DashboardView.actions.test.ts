import '../../test-setup';
import { App, MarkdownView, Notice, TFile } from 'obsidian';
import { DashboardView } from '@/presentation/views/DashboardView';
import { DEFAULT_SETTINGS } from '@/domain/settings';
import { installNativeObsidian } from '../../mocks/nativeObsidian';
import type { NativeRecord } from '../../mocks/nativeObsidian';
import { createDomElement, MockDomElement } from '../../helpers/domMock';

type El = MockDomElement;
const el = (tag: string, opts: any = {}): El => createDomElement(tag, opts);

const all = (root: El, pred: (n: El) => boolean): El[] => {
	const out: El[] = [];
	const walk = (n: El) => {
		if (pred(n)) out.push(n);
		n.children.forEach(walk);
	};
	walk(root);
	return out;
};
const notices = () => (Notice as unknown as jest.Mock).mock.calls.map((c) => c[0]);
const flush = () => new Promise((r) => setImmediate(r));

describe('DashboardView actions', () => {
	let app: any;
	let plugin: any;
	let view: any;
	let rec: NativeRecord;

	beforeEach(() => {
		jest.clearAllMocks();
		rec = installNativeObsidian();
		app = new App();
		plugin = {
			settings: { ...DEFAULT_SETTINGS, ui_expanded_decks: [], ui_expanded_concepts: [] },
			statsService: { getCache: jest.fn(() => ({ concepts: {}, lastFetched: 0 })) },
			leechService: { getLeeches: jest.fn((): any[] => []), suspendCard: jest.fn() },
			areteClient: {
				getDeckNames: jest.fn().mockResolvedValue(['Math', 'Bio']),
				buildStudyQueue: jest.fn(),
				createQueueDeck: jest.fn(),
			},
		};
		view = new DashboardView({} as any, plugin);
		view.app = app;
	});

	describe('leeches tab', () => {
		const leech = (o: any = {}) => ({
			cardId: 11,
			noteId: 1,
			front: '<b>Front</b> text',
			back: 'b',
			filePath: 'A.md',
			fileName: 'A.md',
			deck: 'D',
			deckName: 'D',
			lapses: 4,
			ease: 0,
			issue: '',
			...o,
		});

		it('says the deck is healthy when there are no leeches', async () => {
			const root = el('div');
			await view.renderLeeches(root);
			expect(
				all(root, (n) => n.textContent === 'No leeches found! Your deck is healthy.'),
			).toHaveLength(1);
			expect(all(root, (n) => n.tag === 'table')).toHaveLength(0);
		});

		it('lists each leech with tags stripped, FSRS difficulty even when ease is non-zero, and lapses flagged', async () => {
			plugin.settings.stats_algorithm = 'fsrs';
			plugin.leechService.getLeeches.mockReturnValue([
				leech({ ease: 2500, difficulty: 0.92, lapses: 11 }),
				leech({ cardId: 12, ease: 2100, difficulty: 0.73, lapses: 10 }),
				leech({ cardId: 13, ease: 2500, difficulty: undefined, lapses: 2 }),
			]);
			const root = el('div');

			await view.renderLeeches(root);

			const rows = all(root, (n) => n.tag === 'tr').slice(1); // after the header row
			const cells = rows.map((r) =>
				r.children.map((c) => c.children[0]?.textContent ?? c.textContent),
			);
			expect(cells[0].slice(0, 3)).toEqual(['Front text', '9.2 diff', '11']);
			expect(cells[1].slice(1, 3)).toEqual(['7.3 diff', '10']);
			expect(cells[2].slice(1, 3)).toEqual(['-', '2']);
			const diffFlag = (i: number) => rows[i].children[1].addClass.mock.calls[0]?.[0];
			expect([diffFlag(0), diffFlag(1), diffFlag(2)]).toEqual([
				'mod-error',
				'mod-warning',
				undefined,
			]);
			const flag = (i: number) => rows[i].children[2].addClass.mock.calls[0]?.[0];
			expect([flag(0), flag(1), flag(2)]).toEqual(['mod-error', 'mod-error', 'mod-warning']);
			// the full front stays available on hover
			expect(rec.tooltips.map((t) => t.text)).toContain('<b>Front</b> text');
		});

		it('shows SM-2 ease when algorithm is sm2 and treats ease 0 as missing', async () => {
			plugin.settings.stats_algorithm = 'sm2';
			plugin.leechService.getLeeches.mockReturnValue([
				leech({ cardId: 11, ease: 1900, difficulty: 0.9, lapses: 4 }),
				leech({ cardId: 12, ease: 0, difficulty: 0.9, lapses: 4 }),
			]);
			const root = el('div');

			await view.renderLeeches(root);

			const rows = all(root, (n) => n.tag === 'tr').slice(1);
			const cells = rows.map((r) =>
				r.children.map((c) => c.children[0]?.textContent ?? c.textContent),
			);
			expect(cells[0][1]).toBe('190% ease');
			expect(rows[0].children[1].addClass).toHaveBeenCalledWith('mod-warning');
			expect(cells[1][1]).toBe('-');
			expect(rows[1].children[1].addClass).not.toHaveBeenCalled();
		});

		it('flags the difficulty cell with the shared cut-offs, as every other view does', async () => {
			plugin.leechService.getLeeches.mockReturnValue([
				leech({ cardId: 12, difficulty: 0.73, lapses: 0 }),
				leech({ cardId: 13, difficulty: 0.92, lapses: 0 }),
				leech({ cardId: 14, difficulty: 0.4, lapses: 0 }),
			]);
			const root = el('div');

			await view.renderLeeches(root);

			const rows = all(root, (n) => n.tag === 'tr').slice(1);
			const flag = (i: number) => rows[i].children[1].addClass.mock.calls[0]?.[0];
			expect([flag(0), flag(1), flag(2)]).toEqual(['mod-warning', 'mod-error', undefined]);
		});

		it('Suspend marks the row done when Anki accepts it', async () => {
			plugin.leechService.getLeeches.mockReturnValue([leech()]);
			plugin.leechService.suspendCard.mockResolvedValue(true);
			const root = el('div');
			await view.renderLeeches(root);

			await rec.buttons[0].onClick!();

			expect(plugin.leechService.suspendCard).toHaveBeenCalledWith(11);
			expect(notices()).toEqual(['Card suspended.']);
			expect(rec.buttons[0].text).toBe('Suspended');
			expect(rec.buttons[0].disabled).toBe(true);
		});

		it('Suspend restores the button and says so when Anki refuses', async () => {
			plugin.leechService.getLeeches.mockReturnValue([leech()]);
			plugin.leechService.suspendCard.mockResolvedValue(false);
			const root = el('div');
			await view.renderLeeches(root);

			await rec.buttons[0].onClick!();

			expect(notices()).toEqual(['Failed to suspend card.']);
			expect(rec.buttons[0].text).toBe('Suspend');
			expect(rec.buttons[0].disabled).toBe(false);
		});

		it('Suspend disables while in flight and restores the button when suspendCard rejects', async () => {
			const errSpy = jest.spyOn(console, 'error').mockImplementation(() => {});
			let rejectSuspend!: (err: Error) => void;
			plugin.leechService.getLeeches.mockReturnValue([leech()]);
			plugin.leechService.suspendCard.mockReturnValue(
				new Promise<boolean>((_resolve, reject) => (rejectSuspend = reject)),
			);
			const root = el('div');
			await view.renderLeeches(root);

			const pending = rec.buttons[0].onClick!();
			expect(rec.buttons[0].text).toBe('...');
			expect(rec.buttons[0].disabled).toBe(true);

			rejectSuspend(new Error('Anki offline'));
			await pending;

			expect(errSpy).toHaveBeenCalledWith(
				'[Arete] Failed to suspend card:',
				expect.any(Error),
			);
			expect(notices()).toEqual(['Failed to suspend card.']);
			expect(rec.buttons[0].text).toBe('Suspend');
			expect(rec.buttons[0].disabled).toBe(false);
			errSpy.mockRestore();
		});

		it('clicking a front opens its note at the card', async () => {
			plugin.leechService.getLeeches.mockReturnValue([leech()]);
			view.goToCard = jest.fn();
			const root = el('div');
			await view.renderLeeches(root);

			all(root, (n) => n.textContent === 'Front text')[0].onclick!();

			expect(view.goToCard).toHaveBeenCalledWith('A.md', '<b>Front</b> text', 1);
		});
	});

	describe('queue builder', () => {
		const result = {
			deck: 'Math',
			dueCount: 2,
			totalWithPrereqs: 3,
			queue: [
				{ position: 1, id: 'p', title: 'Prereq', file: 'P.md', isPrereq: true },
				{ position: 2, id: 'a', title: 'Due A', file: 'A.md', isPrereq: false },
				{ position: 3, id: 'b', title: 'Due B', file: 'B.md', isPrereq: false },
			],
		};
		const build = async (root: El) => {
			view.renderQueueBuilder(root);
			await flush();
			const [deckSetting, depthSetting, maxSetting] = rec.settings.slice(-3);
			const btnIndex = rec.buttons.length - 1;
			return {
				dropdown: deckSetting.dropdown,
				depth: depthSetting.text,
				max: maxSetting.text,
				get btn() {
					return rec.buttons[btnIndex];
				},
			};
		};

		it('offers All decks first, then every deck Anki has', async () => {
			const root = el('div');
			const { dropdown } = await build(root);
			expect(dropdown.options.map((o: any) => o.text)).toEqual(['All decks', 'Math', 'Bio']);
		});

		it('starts with depth 2 and 50 cards', async () => {
			const { depth, max } = await build(el('div'));
			expect([depth.value, max.value]).toEqual(['2', '50']);
			expect([
				depth.inputEl.min,
				depth.inputEl.max,
				max.inputEl.min,
				max.inputEl.max,
			]).toEqual(['1', '5', '10', '200']);
		});

		it('builds with the chosen deck, depth and cap, then lists the queue', async () => {
			plugin.areteClient.buildStudyQueue.mockResolvedValue(result);
			const root = el('div');
			const ui = await build(root);
			ui.dropdown._onChange('Math');
			ui.depth._onChange('3');
			ui.max._onChange('25');

			await ui.btn.onClick!();

			expect(plugin.areteClient.buildStudyQueue).toHaveBeenCalledWith('Math', 3, 25);
			const results = all(root, (n) => n.opts?.cls === 'arete-queue-results')[0];
			expect(all(results, (n) => n.textContent === 'Math')).toHaveLength(1);
			expect(all(results, (n) => n.textContent === ' — 2 due, 3 total')).toHaveLength(1);
			expect(
				all(results, (n) => ['Prereq', 'Due A', 'Due B'].includes(n.textContent)).map(
					(n) => n.textContent,
				),
			).toEqual(['Prereq', 'Due A', 'Due B']);
			expect(all(results, (n) => n.textContent === 'Prerequisite')).toHaveLength(1);
			expect(notices()).toEqual(['Queue built: 3 cards']);
			expect(ui.btn.text).toBe('Build queue');
			expect(ui.btn.disabled).toBe(false);
		});

		it('an empty deck choice means all decks, and unreadable numbers fall back to 2 and 50', async () => {
			plugin.areteClient.buildStudyQueue.mockResolvedValue(result);
			const ui = await build(el('div'));
			ui.depth._onChange('abc');
			ui.max._onChange('');

			await ui.btn.onClick!();

			expect(plugin.areteClient.buildStudyQueue).toHaveBeenCalledWith(null, 2, 50);
		});

		it('shows the button as busy while the queue is being built', async () => {
			let finish!: (r: typeof result) => void;
			plugin.areteClient.buildStudyQueue.mockReturnValue(new Promise((r) => (finish = r)));
			const ui = await build(el('div'));

			const pending = ui.btn.onClick!();
			expect(ui.btn.text).toBe('Building...');
			expect(ui.btn.disabled).toBe(true);

			finish(result);
			await pending;
			expect(ui.btn.text).toBe('Build queue');
		});

		it('Send to Anki passes the queue ids in order and reports the outcome', async () => {
			plugin.areteClient.buildStudyQueue.mockResolvedValue(result);
			plugin.areteClient.createQueueDeck
				.mockResolvedValueOnce(true)
				.mockResolvedValueOnce(false);
			const root = el('div');
			const ui = await build(root);
			await ui.btn.onClick!();
			const send = rec.buttons[1];
			expect(send.text).toBe('Send to Anki');

			await send.onClick!();
			await send.onClick!();

			expect(plugin.areteClient.createQueueDeck).toHaveBeenCalledWith(['p', 'a', 'b']);
			expect(notices().slice(-2)).toEqual(['Queue sent to Anki.', 'Failed to create deck.']);
		});

		it('a failed build says so, logs, and frees the button', async () => {
			const log = jest.spyOn(console, 'error').mockImplementation(() => {});
			plugin.areteClient.buildStudyQueue.mockRejectedValue(new Error('server down'));
			const ui = await build(el('div'));

			await ui.btn.onClick!();

			expect(notices()).toEqual(['Failed to build queue.']);
			expect(ui.btn.disabled).toBe(false);
			expect(log).toHaveBeenCalledWith(
				'[Arete] Failed to build the queue:',
				expect.any(Error),
			);
			log.mockRestore();
		});

		it('disables Send to Anki while creating the queue deck and ignores stale deck loads', async () => {
			let resolveDecks!: (decks: string[]) => void;
			plugin.areteClient.getDeckNames.mockReturnValueOnce(
				new Promise<string[]>((r) => (resolveDecks = r)),
			);
			const root1 = el('div');
			view.renderQueueBuilder(root1);
			const firstDropdown = rec.settings[0].dropdown;
			await view.onClose();
			resolveDecks(['LateDeck']);
			await flush();
			expect(firstDropdown.options.map((o: any) => o.text)).not.toContain('LateDeck');

			plugin.areteClient.buildStudyQueue.mockResolvedValue(result);
			let finishSend!: (ok: boolean) => void;
			plugin.areteClient.createQueueDeck.mockReturnValue(
				new Promise<boolean>((r) => (finishSend = r)),
			);
			const root2 = el('div');
			const ui = await build(root2);
			await ui.btn.onClick!();
			const lastBtn = () => rec.buttons[rec.buttons.length - 1];
			const sending = lastBtn().onClick!();
			expect(lastBtn().disabled).toBe(true);
			expect(lastBtn().text).toBe('Sending...');
			finishSend(true);
			await sending;
			expect(lastBtn().disabled).toBe(false);
			expect(lastBtn().text).toBe('Send to Anki');
		});

		it('clicking a queued title opens its note', async () => {
			plugin.areteClient.buildStudyQueue.mockResolvedValue(result);
			view.openFile = jest.fn();
			const root = el('div');
			const ui = await build(root);
			await ui.btn.onClick!();

			all(root, (n) => n.textContent === 'Due B')[0].onclick!();

			expect(view.openFile).toHaveBeenCalledWith('B.md');
		});

		it('disables and guards Send to Anki when the built queue is empty', async () => {
			plugin.areteClient.buildStudyQueue.mockResolvedValue({
				deck: 'Math',
				dueCount: 0,
				totalWithPrereqs: 0,
				queue: [],
			});
			const root = el('div');
			const ui = await build(root);
			await ui.btn.onClick!();

			const send = rec.buttons[1];
			expect(send.disabled).toBe(true);
			await send.onClick!();
			expect(plugin.areteClient.createQueueDeck).not.toHaveBeenCalled();
		});

		it('ignores a stale buildStudyQueue result after the view closes', async () => {
			let finish!: (r: typeof result) => void;
			plugin.areteClient.buildStudyQueue.mockReturnValue(new Promise((r) => (finish = r)));
			const root = el('div');
			const ui = await build(root);

			const pending = ui.btn.onClick!();
			await view.onClose();
			finish(result);
			await pending;

			const results = all(root, (n) => n.opts?.cls === 'arete-queue-results')[0];
			expect(results.children).toHaveLength(0);
			expect(notices()).toEqual([]);
		});
	});

	describe('opening notes', () => {
		const editorFor = (content: string) => {
			const editor = {
				getValue: () => content,
				offsetToPos: (o: number) => ({ line: 0, ch: o }),
				setCursor: jest.fn(),
				scrollIntoView: jest.fn(),
				setSelection: jest.fn(),
			};
			const md: any = new MarkdownView({} as any);
			md.editor = editor;
			app.workspace.getActiveViewOfType.mockReturnValue(md);
			return editor;
		};
		const file = () => Object.assign(new TFile(), { path: 'A.md' });
		let openFile: jest.Mock;

		beforeEach(() => {
			openFile = jest.fn().mockResolvedValue(undefined);
			app.workspace.getLeaf = jest.fn(() => ({ openFile }));
		});

		it('openFile opens a note that exists and ignores a path that is not a file', async () => {
			const f = file();
			app.vault.getAbstractFileByPath = jest.fn((p: string) => (p === 'A.md' ? f : null));

			await view.openFile('A.md');
			expect(openFile).toHaveBeenCalledWith(f);

			openFile.mockClear();
			await view.openFile('gone.md');
			expect(openFile).not.toHaveBeenCalled();
		});

		it('goToCard matches full normalized front to disambiguate shared prefixes and falls back to noteId', async () => {
			app.vault.getAbstractFileByPath = jest.fn(() => file());
			const content =
				'---\ncards:\n  - Front: What is force?\n  - Front: What is entropy?\n    anki:\n      nid: 999888\n---';
			const editor = editorFor(content);

			await view.goToCard('A.md', '  What   is\nentropy?');
			expect(editor.setCursor).toHaveBeenCalledWith({
				line: 0,
				ch: content.indexOf('What is entropy?'),
			});
			expect(editor.scrollIntoView).toHaveBeenCalled();

			editor.setCursor.mockClear();
			await view.goToCard('A.md', 'Completely absent front', 999888);
			expect(editor.setCursor).toHaveBeenCalledWith({
				line: 0,
				ch: content.indexOf('999888'),
			});
		});

		it('goToCard leaves the cursor alone when the text is not found', async () => {
			app.vault.getAbstractFileByPath = jest.fn(() => file());
			const editor = editorFor('nothing relevant');
			await view.goToCard('A.md', 'Missing words');
			expect(editor.setCursor).not.toHaveBeenCalled();
		});

		it('goToIssue selects the problem text, without the "Card #n:" prefix', async () => {
			const editor = editorFor('abc broken ref def');
			await view.goToIssue(file(), 'Card #3: broken ref');

			expect(editor.setCursor).toHaveBeenCalledWith({ line: 0, ch: 4 });
			expect(editor.setSelection).toHaveBeenCalledWith(
				{ line: 0, ch: 4 },
				{ line: 0, ch: 14 },
			);
		});

		it('goToIssue says so when it cannot find the text', async () => {
			const editor = editorFor('abc');
			await view.goToIssue(file(), 'Card #3: a very long missing sentence');

			expect(editor.setSelection).not.toHaveBeenCalled();
			expect(notices()).toEqual([
				'Could not automatically find text: "a very long missing ..."',
			]);
		});

		it('goToIssue jumps straight to the given line for YAML errors without searching text', async () => {
			const editor: any = editorFor('---\nbad: [\n---');
			editor.lineCount = () => 3;
			await view.goToIssue(file(), 'INVALID YAML', 1);

			expect(editor.setCursor).toHaveBeenCalledWith({ line: 1, ch: 0 });
			expect(editor.scrollIntoView).toHaveBeenCalledWith(
				{ from: { line: 1, ch: 0 }, to: { line: 1, ch: 0 } },
				true,
			);
			expect(editor.setSelection).not.toHaveBeenCalled();
			expect(notices()).toEqual([]);

			// Clamps out-of-range line numbers to the last line in the document.
			await view.goToIssue(file(), 'INVALID YAML', 99);
			expect(editor.setCursor).toHaveBeenLastCalledWith({ line: 2, ch: 0 });
		});
	});
});
