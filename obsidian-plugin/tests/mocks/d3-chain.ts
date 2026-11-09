/**
 * A d3 stand-in that lets a view's render code run in jest. Every method chains; `data(arr)`
 * starts a selection bound to `arr` with one fake element per datum; `on` records handlers;
 * `attr` writes are logged in `attrLog`. Elements have a working `classList`.
 * Use as `jest.mock('d3', () => jest.requireActual('../../mocks/d3-chain'))`.
 */

export interface FakeElement {
	classList: { add(c: string): void; remove(c: string): void; contains(c: string): boolean };
}

export function fakeElement(): FakeElement {
	const classes = new Set<string>();
	return {
		classList: {
			add: (c) => void classes.add(c),
			remove: (c) => void classes.delete(c),
			contains: (c) => classes.has(c),
		},
	};
}

/** Every `attr(name, …)` call, in order. */
export const attrLog: string[] = [];
/** Every selection created by `data(arr)`, in order. */
export const boundSelections: any[] = [];

function selection(data: any[], els: FakeElement[]): any {
	const state = { data, els, handlers: {} as Record<string, (...a: any[]) => any> };
	const own: Record<string, any> = {
		data: (d?: any[]) => {
			if (d === undefined) return state.data;
			const s = selection(
				d,
				d.map(() => fakeElement()),
			);
			boundSelections.push(s);
			return s;
		},
		each: (fn: (this: FakeElement, d: any, i: number) => void) => {
			state.data.forEach((d, i) => fn.call(state.els[i], d, i));
			return proxy;
		},
		nodes: () => state.els,
		node: () => state.els[0],
		on: (name: string, fn: any) => {
			state.handlers[name] = fn;
			return proxy;
		},
		attr: (name: string) => {
			attrLog.push(name);
			return proxy;
		},
		handlers: state.handlers,
	};
	const proxy: any = new Proxy(function () {}, {
		get(_t, k: string) {
			if (k === 'then') return undefined;
			if (k in own) return own[k];
			return () => proxy;
		},
		apply: () => proxy,
	});
	return proxy;
}

const root = () => selection([], [fakeElement()]);

/** Every selection `select()` returned, in order; `node()` is its element. */
export const selected: any[] = [];
export const select = () => {
	const s = root();
	selected.push(s);
	return s;
};
export const zoom = root;
export const drag = root;
export const forceSimulation = root;
export const forceLink = root;
export const forceManyBody = root;
export const forceCenter = root;
export const forceCollide = root;
export const forceX = root;
export const forceY = root;
export const zoomIdentity = root();
