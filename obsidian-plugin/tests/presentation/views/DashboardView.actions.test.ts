import '../../test-setup';
import { App, MarkdownView, Notice, TFile } from 'obsidian';
import { DashboardView } from '@/presentation/views/DashboardView';
import { DEFAULT_SETTINGS } from '@/domain/settings';
import { installNativeObsidian } from '../../mocks/nativeObsidian';
import type { NativeRecord } from '../../mocks/nativeObsidian';

/** An element that remembers what was created inside it, with the properties views set. */
interface El {
	tag: string;
	opts: any;
	style: Record<string, string>;
	children: El[];
	textContent: string;
	title?: string;
	value: string;
	disabled?: boolean;
	onclick?: () => any;
	[k: string]: any;
}

function el(tag: string, opts: any = {}): El {
	const n: El = {
		tag,
		opts,
		style: {},
		children: [],
		textContent: opts.text ?? '',
		value: opts.value ?? '',
		empty() {
			n.children.length = 0;
		},
		addClass: jest.fn(),
		createDiv(o?: any) {
			const c = el('div', o);
			n.children.push(c);
			return c;
		},
		createSpan(o?: any) {
			const c = el('span', o);
			n.children.push(c);
			return c;
		},
		createEl(t: string, o?: any) {
			const c = el(t, o);
			n.children.push(c);
			return c;
		},
	};
	return n;
}

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

		it('lists each leech with tags stripped, the right metric, and lapses flagged by the shared cut-offs', async () => {
			plugin.leechService.getLeeches.mockReturnValue([
				leech({ ease: 2100, lapses: 11 }),
				leech({ cardId: 12, ease: 0, difficulty: 0.73, lapses: 10 }),
				leech({ cardId: 13, ease: 0, difficulty: undefined, lapses: 2 }),
			]);
			const root = el('div');

			await view.renderLeeches(root);

			const rows = all(root, (n) => n.tag === 'tr').slice(1); // after the header row
			const cells = rows.map((r) =>
				r.children.map((c) => c.children[0]?.textContent ?? c.textContent),
			);
			expect(cells[0].slice(0, 3)).toEqual(['Front text', '210% ease', '11']);
			expect(cells[1].slice(1, 3)).toEqual(['7.3 diff', '10']);
			expect(cells[2].slice(1, 3)).toEqual(['-', '2']);
			const flag = (i: number) => rows[i].children[2].addClass.mock.calls[0]?.[0];
			expect([flag(0), flag(1), flag(2)]).toEqual(['mod-error', 'mod-error', 'mod-warning']);
			// the full front stays available on hover
			expect(rec.tooltips.map((t) => t.text)).toContain('<b>Front</b> text');
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

		it('clicking a front opens its note at the card', async () => {
			plugin.leechService.getLeeches.mockReturnValue([leech()]);
			view.goToCard = jest.fn();
			const root = el('div');
			await view.renderLeeches(root);

			all(root, (n) => n.textContent === 'Front text')[0].onclick!();

			expect(view.goToCard).toHaveBeenCalledWith('A.md', '<b>Front</b> text');
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
			const inputs = all(root, (n) => n.tag === 'input');
			return {
				select: all(root, (n) => n.tag === 'select')[0],
				depth: inputs[0],
				max: inputs[1],
				get btn() {
					return rec.buttons[0];
				},
			};
		};

		it('offers All decks first, then every deck Anki has', async () => {
			const root = el('div');
			const { select } = await build(root);
			expect(select.children.map((o) => o.opts.text)).toEqual(['All decks', 'Math', 'Bio']);
		});

		it('starts with depth 2 and 50 cards', async () => {
			const { depth, max } = await build(el('div'));
			expect([depth.value, max.value]).toEqual(['2', '50']);
			expect([depth.min, depth.max, max.min, max.max]).toEqual(['1', '5', '10', '200']);
		});

		it('builds with the chosen deck, depth and cap, then lists the queue', async () => {
			plugin.areteClient.buildStudyQueue.mockResolvedValue(result);
			const root = el('div');
			const ui = await build(root);
			ui.select.value = 'Math';
			ui.depth.value = '3';
			ui.max.value = '25';

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
			ui.depth.value = 'abc';
			ui.max.value = '';

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

		it('clicking a queued title opens its note', async () => {
			plugin.areteClient.buildStudyQueue.mockResolvedValue(result);
			view.openFile = jest.fn();
			const root = el('div');
			const ui = await build(root);
			await ui.btn.onClick!();

			all(root, (n) => n.textContent === 'Due B')[0].onclick!();

			expect(view.openFile).toHaveBeenCalledWith('B.md');
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

		it('goToCard puts the cursor on the first word of the front', async () => {
			app.vault.getAbstractFileByPath = jest.fn(() => file());
			const editor = editorFor('intro\n  What is entropy?');

			await view.goToCard('A.md', '  What   is\nentropy?');

			expect(editor.setCursor).toHaveBeenCalledWith({ line: 0, ch: 'intro\n  '.length });
			expect(editor.scrollIntoView).toHaveBeenCalled();
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
	});
});
