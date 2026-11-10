/**
 * The card gutter runs on every editor update, so these tests pin what CodeMirror
 * sees: whether the marker set changed, and whether each marker compares equal.
 * CodeMirror rebuilds a gutter element's DOM only for markers whose `eq` is false.
 */
jest.unmock('@codemirror/state');
// The real package, loaded after giving the shared document stub the `body.style`
// that @codemirror/view reads at load time.
jest.mock('@codemirror/view', () => {
	const body = (global as any).document.body;
	body.style = body.style ?? {};
	return jest.requireActual('@codemirror/view');
});
jest.mock('obsidian', () => ({
	Notice: jest.fn(),
	setIcon: jest.fn(),
	setTooltip: jest.fn(),
	// parseYaml is Obsidian's; a real YAML loader stands in for it.
	parseYaml: (text: string) => jest.requireActual('js-yaml').load(text),
}));

import { EditorState, RangeSet, TransactionSpec } from '@codemirror/state';
import type { GutterMarker } from '@codemirror/view';
import type { AnkiCardStats } from '@/domain/stats';
import { cardRangesField, createCardGutter } from '@/presentation/extensions/CardGutterExtension';
import { CardParserService } from '@/application/services/CardParserService';
import { ankiCardStats, card, noteYaml } from '../../factories';

type Markers = RangeSet<GutterMarker>;
type FakeView = { state: EditorState };

/** The `markers` function of the gutter config inside the extension array. */
function markersFn(ext: unknown): (view: FakeView) => Markers {
	const stack: unknown[] = [ext];
	while (stack.length) {
		const e: any = stack.pop();
		if (Array.isArray(e)) stack.push(...e);
		else if (e && typeof e.value?.markers === 'function') return e.value.markers;
	}
	throw new Error('no gutter config found in extension');
}

function entries(set: Markers): { from: number; marker: any }[] {
	const out: { from: number; marker: any }[] = [];
	for (const c = set.iter(); c.value; c.next()) out.push({ from: c.from, marker: c.value });
	return out;
}

const cardMarkers = (set: Markers) => entries(set).filter((e) => 'lineIndex' in e.marker);

const NIDS = [1762277751241, 1762277751465, 1762277751777];
const threeCards = () =>
	noteYaml({
		cards: NIDS.map((nid, i) => card({ Front: `Question ${i}`, Back: `Answer ${i}`, nid })),
	});

function setup(doc: string, thresholds?: () => any, algorithm: 'fsrs' | 'sm2' = 'fsrs') {
	const statsByNid = new Map<number, AnkiCardStats>(
		NIDS.map((nid) => [nid, ankiCardStats({ noteId: nid, cardId: nid })]),
	);
	const getCardStats = jest.fn((nid: number | null) =>
		nid ? (statsByNid.get(nid) ?? null) : null,
	);
	const ext = createCardGutter(jest.fn(), getCardStats as any, algorithm, thresholds);
	const markers = markersFn(ext);
	const view: FakeView = { state: EditorState.create({ doc, extensions: ext }) };
	const apply = (spec: TransactionSpec) => {
		view.state = view.state.update(spec).state;
	};
	return {
		ext,
		view,
		markers: () => markers(view),
		markersFn: markers,
		getCardStats,
		statsByNid,
		apply,
	};
}

describe('card gutter markers', () => {
	it('returns the same set when the document, ranges and stats are unchanged', () => {
		const g = setup(threeCards());
		const before = g.markers();
		g.apply({ selection: { anchor: 5 } });
		const after = g.markers();

		expect(after).toBe(before);
		expect(RangeSet.eq([before], [after])).toBe(true);
		expect(cardMarkers(after).length).toBeGreaterThan(0);
	});

	it('looks stats up once per card, not once per line', () => {
		const g = setup(threeCards());
		g.getCardStats.mockClear();
		g.apply({ selection: { anchor: 5 } });
		g.markers();

		expect(cardMarkers(g.markers()).length).toBeGreaterThan(NIDS.length);
		// Two markers() calls above, three cards each.
		expect(g.getCardStats).toHaveBeenCalledTimes(2 * NIDS.length);
	});

	it('rebuilds when a stats object is replaced, and only that card compares unequal', () => {
		const g = setup(threeCards());
		const before = g.markers();
		g.statsByNid.set(NIDS[1], ankiCardStats({ noteId: NIDS[1], difficulty: 0.9 }));
		const after = g.markers();

		expect(after).not.toBe(before);
		expect(RangeSet.eq([before], [after])).toBe(false);
		const old = new Map(entries(before).map((e) => [e.from, e.marker]));
		for (const { from, marker } of entries(after)) {
			const same = old.get(from).compare(marker);
			expect(same).toBe(marker.index !== 1);
		}
	});

	it('places markers on the new card lines after an edit and keeps untouched cards equal', () => {
		const g = setup(threeCards());
		const before = g.markers();
		// Add a line inside card 0: card 0 grows, cards 1 and 2 move down one line.
		const at = g.view.state.doc.toString().indexOf('Question 0') + 'Question 0'.length;
		g.apply({ changes: { from: at, insert: '\n      more' } });
		const after = g.markers();

		const doc = g.view.state.doc;
		const { ranges } = CardParserService.parseCards(doc.toString());
		for (const { from, marker } of cardMarkers(after)) {
			const range = ranges[marker.index];
			expect(from).toBe(doc.line(range.startLine + marker.lineIndex + 1).from);
		}

		const oldByKey = new Map(
			cardMarkers(before).map((e) => [`${e.marker.index}:${e.marker.lineIndex}`, e.marker]),
		);
		for (const { marker } of cardMarkers(after)) {
			const old = oldByKey.get(`${marker.index}:${marker.lineIndex}`);
			const equal = !!old && old.compare(marker);
			expect(equal).toBe(marker.index !== 0);
		}
	});

	it('keeps a separate cache per editor view', () => {
		const a = setup(threeCards());
		// A second editor using the same extension instance.
		const viewB: FakeView = {
			state: EditorState.create({
				doc: noteYaml({ cards: [card({ nid: NIDS[0] })] }),
				extensions: a.ext,
			}),
		};
		const fromA = a.markers();
		const fromB = a.markersFn(viewB);

		expect(new Set(cardMarkers(fromB).map((e) => e.marker.index))).toEqual(new Set([0]));
		expect(a.markers()).toBe(fromA);
		expect(a.markersFn(viewB)).toBe(fromB);
	});
});

describe('cardRangesField', () => {
	it('equals parseCards of the current document after any sequence of edits', () => {
		// Deterministic pseudo-random edits, biased toward the closing `---` line where
		// the rule for keeping the previous value has its boundary.
		let seed = 7;
		const rand = (n: number) => {
			seed ^= seed << 13;
			seed ^= seed >>> 17;
			seed ^= seed << 5;
			return (seed >>> 0) % n;
		};
		const pieces = [
			'x',
			'\n',
			'-',
			'---',
			'\n---\n',
			'cards:',
			'\n  - Front: q',
			"\n      nid: '42'",
		];
		let kept = 0;
		for (let round = 0; round < 40; round++) {
			let state = EditorState.create({ doc: threeCards(), extensions: [cardRangesField] });
			for (let k = 0; k < 15; k++) {
				const text = state.doc.toString();
				const close = text.indexOf('\n---\n', 3);
				const from = Math.min(
					text.length,
					close >= 0 && rand(3) > 0 ? close + rand(9) : rand(text.length + 1),
				);
				const to = Math.min(text.length, from + (rand(4) === 0 ? rand(5) : 0));
				const insert = rand(5) === 0 ? '' : pieces[rand(pieces.length)];
				if (from === to && !insert) continue;
				const prev = state.field(cardRangesField);
				state = state.update({ changes: { from, to, insert } }).state;
				if (state.field(cardRangesField) === prev) kept++;
				expect(state.field(cardRangesField)).toEqual(
					CardParserService.parseCards(state.doc.toString()),
				);
			}
		}
		// The reuse path ran often enough for the comparison above to mean something.
		expect(kept).toBeGreaterThan(50);
	});

	it('keeps its value and the gutter its markers for an edit after the frontmatter', () => {
		const g = setup(threeCards());
		const result = g.view.state.field(cardRangesField);
		const before = g.markers();
		g.apply({ changes: { from: g.view.state.doc.length, insert: '\nmore body\n' } });

		expect(g.view.state.field(cardRangesField)).toBe(result);
		expect(g.markers()).toBe(before);
		const doc = g.view.state.doc;
		for (const { from, marker } of cardMarkers(before)) {
			const range = result.ranges[marker.index];
			expect(from).toBe(doc.line(range.startLine + marker.lineIndex + 1).from);
		}
	});
});

describe('gutter marker eq', () => {
	const g = setup(threeCards());
	const all = entries(g.markers());
	const start = all.find((e) => e.marker.isStart && e.marker.index === 0)!.marker;
	const skip = all.find((e) => !('lineIndex' in e.marker))!.marker;
	const variant = (m: any, field: string, value: unknown) =>
		Object.assign(Object.create(Object.getPrototypeOf(m)), m, { [field]: value });

	it('is true for a marker with the same data', () => {
		expect(start.eq(variant(start, 'onClick', (): void => undefined))).toBe(true);
		expect(skip.eq(variant(skip, 'targetLine', skip.targetLine))).toBe(true);
	});

	it.each([
		['index', (m: any) => m.index + 1],
		['isStart', (m: any) => !m.isStart],
		['isEnd', (m: any) => !m.isEnd],
		['lineIndex', (m: any) => m.lineIndex + 1],
		['totalLines', (m: any) => m.totalLines + 1],
		['stats', (m: any) => ({ ...m.stats })],
		['algorithm', () => 'sm2'],
		['isSynced', (m: any) => !m.isSynced],
		['state', () => 'error'],
		['diffState', () => 'error'],
		['lapseState', () => 'error'],
	])('card marker differs when %s differs', (field, change) => {
		expect(start.eq(variant(start, field, change(start)))).toBe(false);
	});

	it('skip marker differs when its target line or view differs', () => {
		expect(skip.eq(variant(skip, 'targetLine', skip.targetLine + 1))).toBe(false);
		expect(skip.eq(variant(skip, 'view', {}))).toBe(false);
	});

	it('markers of different kinds never compare equal', () => {
		expect(start.eq(skip)).toBe(false);
		expect(skip.eq(start)).toBe(false);
	});
});

/** A DOM element with what `toDOM` and its listeners use. */
function fakeElement(): any {
	const listeners: Record<string, () => void> = {};
	const classes = new Set<string>();
	return {
		listeners,
		style: {},
		dataset: {},
		className: '',
		title: '',
		textContent: '',
		classList: {
			add: (c: string) => classes.add(c),
			remove: (c: string) => classes.delete(c),
			contains: (c: string) => classes.has(c),
		},
		appendChild: jest.fn(),
		querySelector: jest.fn((): null => null),
		addEventListener: (name: string, cb: () => void) => {
			listeners[name] = cb;
		},
	};
}

describe('card gutter hover and click', () => {
	const doc = (global as any).document;
	let createElement: any;

	beforeEach(() => {
		createElement = doc.createElement;
		doc.createElement = jest.fn(() => fakeElement());
		doc.querySelectorAll = jest.fn((): unknown[] => []);
	});
	afterEach(() => {
		doc.createElement = createElement;
		delete doc.querySelectorAll;
	});

	it('touches only the lines of its own editor, not other panes', () => {
		const g = setup(threeCards());
		const marker = cardMarkers(g.markers())[0].marker;
		const ownEditor = {
			dom: { querySelectorAll: jest.fn((): unknown[] => []) },
			state: g.view.state,
		};
		const el = marker.toDOM(ownEditor);

		el.listeners.mouseenter();
		el.listeners.mouseleave();
		el.listeners.click.call(el, { preventDefault: jest.fn(), stopPropagation: jest.fn() });

		expect(ownEditor.dom.querySelectorAll).toHaveBeenCalledWith(
			`.arete-gutter-marker[data-card-index="${marker.index}"]`,
		);
		expect(doc.querySelectorAll).not.toHaveBeenCalled();
	});

	it('a click shows no notice: the card ids are diagnostics, for debugLog (rules N6, G1)', () => {
		const { Notice } = jest.requireMock('obsidian');
		(Notice as jest.Mock).mockClear();
		const g = setup(threeCards());
		const marker = cardMarkers(g.markers())[0].marker;
		const el = marker.toDOM({
			dom: { querySelectorAll: jest.fn((): unknown[] => []) },
			state: g.view.state,
		});

		el.listeners.click.call(el, { preventDefault: jest.fn(), stopPropagation: jest.fn() });

		expect(Notice).not.toHaveBeenCalled();
	});
});

describe('card gutter state', () => {
	const stateOf = (g: ReturnType<typeof setup>) =>
		Object.fromEntries(
			cardMarkers(g.markers())
				.filter((e) => e.marker.isStart)
				.map((e) => [e.marker.index, e.marker.state]),
		);

	it('a synced card with healthy stats is success', () => {
		expect(stateOf(setup(threeCards()))).toEqual({ 0: 'success', 1: 'success', 2: 'success' });
	});

	it('a card without stats is accent and an unsynced card is muted', () => {
		const g = setup(threeCards());
		g.statsByNid.delete(NIDS[1]);
		expect(stateOf(g)[1]).toBe('accent');
		const unsynced = setup(
			noteYaml({ cards: [card({ Front: 'Question 0', Back: 'Answer 0' })] }),
		);
		expect(stateOf(unsynced)[0]).toBe('muted');
	});

	it('a lapse warns and the user threshold sets where it errs', () => {
		const lapses = (n: number) => (g: ReturnType<typeof setup>) =>
			g.statsByNid.set(
				NIDS[0],
				ankiCardStats({ noteId: NIDS[0], cardId: NIDS[0], lapses: n }),
			);
		const base = {
			stats_lapse_threshold: 3,
			stats_difficulty_threshold: 0.9,
			stats_ease_threshold: 2100,
		};
		const g = setup(threeCards(), () => base);
		lapses(2)(g);
		expect(stateOf(g)[0]).toBe('warning');
		lapses(3)(g);
		expect(stateOf(g)[0]).toBe('error');

		const lenient = setup(threeCards(), () => ({ ...base, stats_lapse_threshold: 6 }));
		lapses(3)(lenient);
		expect(stateOf(lenient)[0]).toBe('warning');
	});

	it('a card due long ago, with only low retrievability, is a warning, not an error', () => {
		const g = setup(threeCards());
		g.statsByNid.set(
			NIDS[0],
			ankiCardStats({ noteId: NIDS[0], cardId: NIDS[0], lapses: 0, retrievability: 0.04 }),
		);
		expect(stateOf(g)[0]).toBe('warning');
	});

	it('rebuilds the markers when only a threshold changed, and not otherwise', () => {
		let lapseThreshold = 3;
		const g = setup(threeCards(), () => ({
			stats_lapse_threshold: lapseThreshold,
			stats_difficulty_threshold: 0.9,
			stats_ease_threshold: 2100,
		}));
		g.statsByNid.set(NIDS[0], ankiCardStats({ noteId: NIDS[0], cardId: NIDS[0], lapses: 3 }));
		const before = g.markers();
		expect(g.markers()).toBe(before);
		lapseThreshold = 4;
		const after = g.markers();
		expect(after).not.toBe(before);
		expect(stateOf(g)[0]).toBe('warning');
	});

	it('SM-2 ease drives the figure shown beside the badge', () => {
		const g = setup(threeCards(), undefined, 'sm2');
		g.statsByNid.set(NIDS[0], ankiCardStats({ noteId: NIDS[0], cardId: NIDS[0], ease: 1500 }));
		const m = cardMarkers(g.markers()).find(
			(e) => e.marker.isStart && e.marker.index === 0,
		)!.marker;
		expect(m.diffState).toBe('warning');
	});
});

describe('card gutter DOM', () => {
	const doc = (global as any).document;
	let createElement: any;
	const obsidian = jest.requireMock('obsidian');

	beforeEach(() => {
		createElement = doc.createElement;
		doc.createElement = jest.fn(() => fakeElement());
		obsidian.setTooltip.mockClear();
		obsidian.setIcon.mockClear();
	});
	afterEach(() => {
		doc.createElement = createElement;
	});

	const dom = (card: any) => {
		const g = setup(threeCards());
		g.statsByNid.set(NIDS[0], ankiCardStats({ noteId: NIDS[0], cardId: NIDS[0], ...card }));
		const marker = cardMarkers(g.markers()).find(
			(e) => e.marker.isStart && e.marker.index === 0,
		)!.marker;
		const view = {
			dom: { querySelectorAll: jest.fn((): unknown[] => []) },
			state: g.view.state,
		};
		return { el: marker.toDOM(view), view };
	};

	it('marks state with classes and sets no inline style', () => {
		const { el } = dom({ lapses: 4 });
		const added: string[] = [];
		// the fake keeps classes in a set; read them back through contains()
		for (const c of ['is-start', 'is-synced', 'mod-error', 'mod-warning', 'mod-success']) {
			if (el.classList.contains(c)) added.push(c);
		}
		expect(added).toEqual(['is-start', 'is-synced', 'mod-error']);
		expect(el.style).toEqual({});
		expect(el.classList.contains('arete-gutter-start')).toBe(false);
	});

	it('draws the bar and the stat figures without inline colour', () => {
		const { el } = dom({ lapses: 1, difficulty: 0.95 });
		const made = (doc.createElement as jest.Mock).mock.results.map((r: any) => r.value);
		expect(made.every((e: any) => Object.keys(e.style).length === 0)).toBe(true);
		const stats = made.filter((e: any) => e.className === 'arete-gutter-stat');
		expect(stats).toHaveLength(2);
		expect(stats[0].classList.contains('mod-error')).toBe(true); // difficulty 0.95 >= 0.9
		expect(stats[1].classList.contains('mod-warning')).toBe(true); // one lapse
		expect(el.appendChild).toHaveBeenCalled();
	});

	it('sets the tooltip through setTooltip, not a title', () => {
		const { el } = dom({ lapses: 2 });
		expect(el.title).toBe('');
		expect(jest.requireMock('obsidian').setTooltip).toHaveBeenCalledWith(
			el,
			expect.stringContaining('Lapses: 2'),
		);
	});

	it('the skip button is an icon, built without innerHTML', () => {
		const g = setup(threeCards());
		const skip = entries(g.markers()).find((e) => !('lineIndex' in e.marker))!.marker;
		const el = skip.toDOM();
		expect(jest.requireMock('obsidian').setIcon).toHaveBeenCalledWith(el, 'arrow-down');
		expect(el.innerHTML).toBeUndefined();
		expect(jest.requireMock('obsidian').setTooltip).toHaveBeenCalledWith(
			el,
			'Skip to end of frontmatter',
		);
	});

	it('hover and click toggle is-hovered and is-active on its own editor only', () => {
		const { el, view } = dom({});
		const row = { toggleClass: jest.fn() };
		view.dom.querySelectorAll.mockReturnValue([row]);
		el.listeners.mouseenter();
		expect(row.toggleClass.mock.calls).toEqual([
			['is-hovered', true],
			['is-active', false],
		]);
		row.toggleClass.mockClear();
		el.listeners.mouseleave();
		expect(row.toggleClass.mock.calls).toEqual([
			['is-hovered', false],
			['is-active', false],
		]);
	});
});
