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
	// parseYaml is unavailable under jest; CardParserService falls back to its regex.
	parseYaml: () => {
		throw new Error('parseYaml unavailable');
	},
}));

import { EditorState, RangeSet, TransactionSpec } from '@codemirror/state';
import type { GutterMarker } from '@codemirror/view';
import type { AnkiCardStats } from '@/domain/stats';
import { createCardGutter } from '@/presentation/extensions/CardGutterExtension';
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

function setup(doc: string) {
	const statsByNid = new Map<number, AnkiCardStats>(
		NIDS.map((nid) => [nid, ankiCardStats({ noteId: nid, cardId: nid })]),
	);
	const getCardStats = jest.fn((nid: number | null) =>
		nid ? (statsByNid.get(nid) ?? null) : null,
	);
	const ext = createCardGutter(jest.fn(), getCardStats as any, 'fsrs');
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
