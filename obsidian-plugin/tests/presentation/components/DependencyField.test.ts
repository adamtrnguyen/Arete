import '../../test-setup';
import { App, setIcon } from 'obsidian';
import { DependencyField } from '@/presentation/components/DependencyField';
import { DependencySelectionModal } from '@/presentation/modals/DependencySelectionModal';

jest.mock('@/presentation/modals/DependencySelectionModal', () => ({
	DependencySelectionModal: jest.fn().mockImplementation(() => ({ open: jest.fn() })),
}));

/** An element that remembers what was created inside it, so the rendered tree can be read. */
interface Rec {
	tag: string;
	opts: any;
	classes: Set<string>;
	children: Rec[];
	listeners: Record<string, () => void>;
	[k: string]: any;
}

function rec(tag: string, opts: any = {}): Rec {
	const el: Rec = {
		tag,
		opts,
		classes: new Set(opts.cls ? String(opts.cls).split(' ') : []),
		children: [],
		listeners: {},
		addClass(c: string) {
			el.classes.add(c);
		},
		empty() {
			el.children.length = 0;
		},
		addEventListener(name: string, cb: () => void) {
			el.listeners[name] = cb;
		},
		createDiv(o: any) {
			const c = rec('div', o);
			el.children.push(c);
			return c;
		},
		createSpan(o: any) {
			const c = rec('span', o);
			el.children.push(c);
			return c;
		},
		createEl(t: string, o: any) {
			const c = rec(t, o);
			el.children.push(c);
			return c;
		},
	};
	return el;
}

const find = (root: Rec, cls: string): Rec[] => {
	const out: Rec[] = [];
	const walk = (n: Rec) => {
		if (n.classes.has(cls)) out.push(n);
		n.children.forEach(walk);
	};
	walk(root);
	return out;
};

describe('DependencyField', () => {
	let app: App;
	let container: Rec;
	let onChange: jest.Mock;

	beforeEach(() => {
		jest.clearAllMocks();
		app = new App();
		container = rec('div');
		onChange = jest.fn();
	});

	const chips = () => find(container, 'arete-dep-chip');
	const chipTexts = () => chips().map((c) => c.children[1].opts.text);

	it('shows one chip per initial dependency, cards marked and iconed differently', () => {
		new DependencyField(container as any, app, ['arete_1', 'Some Note'], onChange);

		expect(chipTexts()).toEqual(['arete_1', 'Some Note']);
		expect(chips()[0].classes.has('is-card')).toBe(true);
		expect(chips()[1].classes.has('is-card')).toBe(false);
		const icons = (setIcon as jest.Mock).mock.calls.map((c) => c[1]);
		expect(icons.slice(0, 4)).toEqual(['id-card', 'x', 'file-text', 'x']);
		expect(onChange).not.toHaveBeenCalled();
	});

	it('removing a chip reports the remaining dependencies and redraws the list', () => {
		new DependencyField(container as any, app, ['a', 'arete_2', 'c'], onChange);

		find(chips()[1] as Rec, 'arete-dep-remove')[0].listeners['click']();

		expect(onChange).toHaveBeenCalledTimes(1);
		expect(onChange).toHaveBeenCalledWith(['a', 'c']);
		expect(chipTexts()).toEqual(['a', 'c']);
	});

	it('the add button opens the picker; a chosen card id is added and reported', () => {
		new DependencyField(container as any, app, ['a'], onChange);
		const add = find(container, 'arete-dep-add-btn')[0];
		expect(add.opts.text).toBe('+ Add Dependency');

		add.listeners['click']();

		const ctor = DependencySelectionModal as unknown as jest.Mock;
		expect(ctor).toHaveBeenCalledTimes(1);
		expect(ctor.mock.calls[0][0]).toBe(app);
		expect(ctor.mock.results[0].value.open).toHaveBeenCalled();

		const pick = ctor.mock.calls[0][1] as (r: string | null) => void;
		pick('arete_9');
		expect(onChange).toHaveBeenLastCalledWith(['a', 'arete_9']);
		expect(chips()[1].classes.has('is-card')).toBe(true);
	});

	it('a cancelled picker adds nothing', () => {
		new DependencyField(container as any, app, ['a'], onChange);
		find(container, 'arete-dep-add-btn')[0].listeners['click']();
		const pick = (DependencySelectionModal as unknown as jest.Mock).mock.calls[0][1];

		pick(null);

		expect(onChange).not.toHaveBeenCalled();
		expect(chipTexts()).toEqual(['a']);
	});

	it('picking a dependency that is already listed changes nothing', () => {
		new DependencyField(container as any, app, ['arete_1'], onChange);
		find(container, 'arete-dep-add-btn')[0].listeners['click']();
		const pick = (DependencySelectionModal as unknown as jest.Mock).mock.calls[0][1];

		pick('arete_1');

		expect(onChange).not.toHaveBeenCalled();
		expect(chipTexts()).toEqual(['arete_1']);
	});
});
