import '../../test-setup';
import { createMockElement } from '../../test-setup';
import { App } from 'obsidian';
import { CardStatsModal } from '@/presentation/modals/CardStatsModal';
import { DEFAULT_SETTINGS } from '@/domain/settings';
import { installNativeObsidian } from '../../mocks/nativeObsidian';
import type { NativeRecord } from '../../mocks/nativeObsidian';

interface Node {
	tag: string;
	opts: any;
	style: Record<string, string>;
	children: Node[];
	[k: string]: any;
}

function node(tag: string, opts: any = {}): Node {
	const n: Node = {
		tag,
		opts,
		style: {},
		children: [],
		attrs: {},
		addClass: jest.fn(),
		empty: jest.fn(() => {
			n.children.length = 0;
		}),
		appendChild(c: Node) {
			n.children.push(c);
			return c;
		},
		createDiv(o?: any) {
			const c = node('div', o);
			n.children.push(c);
			return c;
		},
		createSpan(o?: any) {
			const c = node('span', o);
			n.children.push(c);
			return c;
		},
		createEl(t: string, o?: any) {
			const c = node(t, o);
			n.children.push(c);
			return c;
		},
	};
	return n;
}

function svgNode(tag: string): any {
	const n: any = {
		tag,
		style: {},
		attrs: {} as Record<string, string>,
		children: [] as any[],
		appendChild(c: any) {
			n.children.push(c);
			return c;
		},
		setAttribute(k: string, v: string) {
			n.attrs[k] = v;
		},
	};
	return n;
}

const walk = (n: Node, fn: (n: Node) => void) => {
	fn(n);
	n.children.forEach((c) => walk(c, fn));
};

/** label -> {value, flag} for every two-line stat cell; flag is the mod-* class on the value. */
function statCells(root: Node) {
	const cells: Record<string, { value: string; flag?: string }> = {};
	walk(root, (n) => {
		if (
			n.children.length === 2 &&
			n.children.every((c) => c.opts?.text !== undefined && c.children.length === 0)
		) {
			cells[n.children[0].opts.text] = {
				value: n.children[1].opts.text,
				flag: n.children[1].addClass.mock.calls
					.flat()
					.find((c: string) => c === 'mod-warning' || c === 'mod-error'),
			};
		}
	});
	return cells;
}

const texts = (root: Node) => {
	const out: string[] = [];
	walk(root, (n) => {
		if (n.opts?.text !== undefined) out.push(n.opts.text);
	});
	return out;
};

describe('CardStatsModal', () => {
	let svgs: any[];
	let rec: NativeRecord;

	beforeEach(() => {
		jest.clearAllMocks();
		rec = installNativeObsidian();
		svgs = [];
		(document as any).createElementNS = jest.fn((_ns: string, tag: string) => {
			const el = svgNode(tag);
			if (tag === 'svg') svgs.push(el);
			return el;
		});
	});

	function open(card: any, settings = DEFAULT_SETTINGS) {
		const modal: any = new CardStatsModal(new App(), card, settings);
		modal.contentEl = node('div');
		modal.modalEl = createMockElement('div');
		modal.close = jest.fn();
		modal.onOpen();
		return modal;
	}

	const fullCard = {
		difficulty: 0.55,
		stability: 12.34,
		retrievability: 0.9,
		desiredRetention: 0.9,
		intervalGrowth: 2.5,
		pressFatigue: 0.1,
		scheduleAdherence: 0.973,
		daysOverdue: 2,
		lapses: 0,
		lapseRate: 0.0833,
		reps: 12,
		averageTime: 4200,
	};

	it('formats every figure of a healthy card, with no warning flags', () => {
		const modal = open(fullCard);
		const cells = statCells(modal.contentEl);

		expect(cells['Difficulty (1-10)'].value).toBe('5.5');
		expect(cells['Stability'].value).toBe('12.3 days');
		expect(cells['Retrievability'].value).toBe('90.0%');
		expect(cells['Interval growth'].value).toBe('2.50x');
		expect(cells['Press fatigue (hard %)'].value).toBe('10%');
		expect(cells['Schedule adherence'].value).toBe('97.3%');
		expect(cells['Days overdue'].value).toBe('2d');
		expect(cells['Total lapses'].value).toBe('0');
		expect(cells['Lapse rate'].value).toBe('8.3%');
		expect(cells['Total reps'].value).toBe('12');
		expect(cells['Average time'].value).toBe('4.2s');
		for (const [label, cell] of Object.entries(cells)) {
			if (label.startsWith('Optimal')) continue;
			expect(cell.flag).toBeUndefined();
		}
	});

	it('flags the figures that cross a warning threshold', () => {
		const modal = open({
			...fullCard,
			difficulty: 0.8,
			stability: 3,
			retrievability: 0.5,
			intervalGrowth: 1.1,
			pressFatigue: 0.4,
			daysOverdue: 8,
			lapses: 6,
		});
		const cells = statCells(modal.contentEl);

		expect(cells['Difficulty (1-10)'].flag).toBe('mod-warning'); // 0.7 <= 0.8 < 0.9
		expect(cells['Stability'].flag).toBe('mod-warning');
		expect(cells['Retrievability'].flag).toBe('mod-warning'); // never an error
		expect(cells['Interval growth'].flag).toBe('mod-warning');
		expect(cells['Press fatigue (hard %)'].flag).toBe('mod-error');
		expect(cells['Days overdue'].flag).toBe('mod-error');
		expect(cells['Total lapses'].flag).toBe('mod-error'); // 6 >= 3
	});

	it('values just inside every cut-off are not flagged', () => {
		const modal = open({
			...fullCard,
			difficulty: 0.69,
			stability: 7,
			retrievability: 0.85,
			intervalGrowth: 1.2,
			pressFatigue: 0.3,
			daysOverdue: 7,
			lapses: 0,
		});
		const cells = statCells(modal.contentEl);
		expect(Object.keys(cells)).toContain('Days overdue');
		for (const [label, cell] of Object.entries(cells)) {
			// the curve legend (Optimal / Target) is coloured by design
			if (label.startsWith('Optimal')) continue;
			expect(cell.flag).toBeUndefined();
		}
	});

	it('shows placeholders for a card with no stats', () => {
		const modal = open({});
		const cells = statCells(modal.contentEl);

		expect(cells['Difficulty (1-10)'].value).toBe('-');
		expect(cells['Stability'].value).toBe('-');
		expect(cells['Retrievability'].value).toBe('-');
		expect(cells['Interval growth'].value).toBe('N/A');
		expect(cells['Press fatigue (hard %)'].value).toBe('N/A');
		expect(cells['Schedule adherence'].value).toBe('N/A');
		expect(cells['Days overdue'].value).toBe('-');
		expect(cells['Total lapses'].value).toBe('0');
		expect(cells['Lapse rate'].value).toBe('-');
		expect(cells['Total reps'].value).toBe('0');
		expect(cells['Average time'].value).toBe('-');
	});

	describe('forgetting curve', () => {
		it('is replaced by a placeholder when there is no usable stability', () => {
			for (const stability of [undefined, null, 0.1]) {
				svgs.length = 0;
				const modal = open({ stability });
				expect(svgs).toHaveLength(0);
				expect(texts(modal.contentEl)).toContain('Not enough review history');
			}
		});

		it('draws a 51-point curve that falls from 100% as R(t) = 1 / (1 + t / 9S)', () => {
			open({ ...fullCard, stability: 10 });
			expect(svgs).toHaveLength(1);

			const curve = svgs[0].children.find((c: any) => c.tag === 'polyline');
			const points = curve.attrs.points
				.split(' ')
				.map((p: string) => p.split(',').map(Number));
			expect(points).toHaveLength(51);
			// plot area: left 45, top 15, width 250, height 170; time axis 0..max(3S, 7) = 30 days
			expect(points[0]).toEqual([45, 15]);
			const [x50, y50] = points[50];
			expect(x50).toBeCloseTo(295, 6);
			expect(y50).toBeCloseTo(15 + (1 - 1 / (1 + 30 / 90)) * 170, 6);
		});

		it('uses a 7-day axis for a short-lived memory', () => {
			open({ ...fullCard, stability: 1 });
			const labels = svgs[0].children
				.filter((c: any) => c.tag === 'text')
				.map((c: any) => c.textContent);
			expect(labels).toEqual(['0d', '4d', '7d', '100%', '50%', '0%']);
		});

		it('marks the target retention and the optimal interval in the legend', () => {
			const modal = open({ ...fullCard, stability: 10, desiredRetention: 0.8 });
			// interval = 9 * S * (1 / target - 1) = 90 * 0.25
			expect(texts(modal.contentEl)).toEqual(
				expect.arrayContaining(['Optimal: 22.5d', 'Target: 80%']),
			);
		});

		it('defaults to 90% target and 100% retrievability when the card has neither', () => {
			const modal = open({ stability: 10 });
			expect(texts(modal.contentEl)).toContain('Target: 90%');
			const dot = svgs[0].children.find((c: any) => c.tag === 'circle');
			expect(dot.attrs.cx).toBe('45');
			expect(dot.attrs.class).toBe('arete-curve-point mod-success');
		});

		it('marks the current point as a warning when retrievability is under target', () => {
			open({ ...fullCard, stability: 10, retrievability: 0.5, desiredRetention: 0.9 });
			const dot = svgs[0].children.find((c: any) => c.tag === 'circle');
			expect(dot.attrs.class).toBe('arete-curve-point mod-warning');
			// elapsed = 9 * 10 * (1 / 0.5 - 1) = 90 days, clamped to the right edge (axis is 30 days)
			expect(Number(dot.attrs.cx)).toBe(295);
		});
	});

	describe('rating distribution and flags', () => {
		it('shows the four rating counts, zero for a missing one', () => {
			const modal = open({ ...fullCard, answerDistribution: { 1: 3, 3: 10, 4: 2 } });
			const all = texts(modal.contentEl);
			const start = all.indexOf('Again');
			expect(all.slice(start, start + 8)).toEqual([
				'Again',
				'3',
				'Hard',
				'0',
				'Good',
				'10',
				'Easy',
				'2',
			]);
		});

		it('omits the distribution when the card has none', () => {
			open(fullCard);
			expect(rec.settings.map((x) => x.name)).not.toContain('Rating distribution');
		});

		it('warns about overlearning only when flagged', () => {
			expect(texts(open(fullCard).contentEl)).not.toContain('Overlearning detected');
			expect(texts(open({ ...fullCard, isOverlearning: true }).contentEl)).toContain(
				'Overlearning detected',
			);
		});
	});

	it('the Close button closes the modal', () => {
		const modal = open(fullCard);
		expect(rec.buttons).toHaveLength(1);
		expect(rec.buttons[0].text).toBe('Close');
		rec.buttons[0].onClick!();
		expect(modal.close).toHaveBeenCalled();
	});

	describe('native look', () => {
		it('sets its title through setTitle, in sentence case, and builds no heading element', () => {
			const modal = open(fullCard);
			expect(modal.setTitle).toHaveBeenCalledWith('Card memory insights');
			const tags: string[] = [];
			walk(modal.contentEl, (n) => tags.push(n.tag));
			expect(tags.filter((t) => /^h[1-6]$/.test(t))).toEqual([]);
		});

		it('draws section headings as Setting headings in sentence case', () => {
			open({ ...fullCard, answerDistribution: { 1: 1 } });
			expect(rec.settings.filter((x) => x.heading).map((x) => x.name)).toEqual([
				'Memory state',
				'Forgetting curve',
				'Learning dynamics',
				'Review history',
				'Rating distribution',
			]);
		});

		it('sets no inline style on any element and no emoji in text', () => {
			const modal = open({ ...fullCard, isOverlearning: true, answerDistribution: { 1: 1 } });
			const styled: Node[] = [];
			walk(modal.contentEl, (n) => {
				if (Object.keys(n.style).length) styled.push(n);
			});
			expect(styled).toEqual([]);
			expect(texts(modal.contentEl).join('\n')).not.toMatch(/\p{Extended_Pictographic}/u);
		});

		it('shows the overlearning flag with a zap icon', () => {
			open({ ...fullCard, isOverlearning: true });
			expect(rec.icons.map((i) => i.icon)).toContain('zap');
		});

		it('colours the curve with classes, not attributes or styles', () => {
			open({ ...fullCard, stability: 10 });
			for (const el of svgs[0].children) {
				expect(el.attrs).not.toHaveProperty('stroke');
				expect(el.attrs).not.toHaveProperty('fill');
				expect(el.style).toEqual({});
			}
		});

		it('follows the lapse threshold setting', () => {
			const lapses = (settings = DEFAULT_SETTINGS) =>
				statCells(open({ ...fullCard, lapses: 3 }, settings).contentEl)['Total lapses']
					.flag;
			expect(lapses()).toBe('mod-error');
			expect(lapses({ ...DEFAULT_SETTINGS, stats_lapse_threshold: 4 })).toBe('mod-warning');
		});
	});

	it('closing empties the content', () => {
		const modal = open(fullCard);
		modal.onClose();
		expect(modal.contentEl.empty).toHaveBeenCalled();
	});
});
