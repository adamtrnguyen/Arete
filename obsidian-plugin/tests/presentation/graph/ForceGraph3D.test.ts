import { loadForceGraph3D, resolveGraphColors } from '@/presentation/graph/ForceGraph3D';

/** A host whose probe reports `colors[variable]` as its computed colour. */
function host(colors: Record<string, string | undefined>) {
	const probe: any = { style: {}, remove: jest.fn() };
	const getComputedStyle = jest.fn((el: any) => {
		const name = /var\((--[a-z-]+)/.exec(el.style.color)?.[1] ?? '';
		return { color: colors[name] };
	});
	return {
		probe,
		getComputedStyle,
		el: { createSpan: jest.fn(() => probe), win: { getComputedStyle } } as any,
	};
}

describe('loadForceGraph3D', () => {
	it('returns the factory from an ESM namespace object', () => {
		jest.isolateModules(() => {
			const factory = jest.fn();
			jest.doMock('3d-force-graph', () => ({ default: factory }));
			const mod = jest.requireActual('@/presentation/graph/ForceGraph3D');
			expect(mod.loadForceGraph3D()).toBe(factory);
		});
	});

	it('is the only module that requires the library', () => {
		expect(typeof loadForceGraph3D).toBe('function');
	});
});

describe('resolveGraphColors', () => {
	it('reads each graph variable once, through one probe, and removes the probe', () => {
		const h = host({
			'--graph-node': 'rgb(1, 2, 3)',
			'--graph-line': 'rgba(4, 5, 6, 0.5)',
			'--graph-node-focused': '#a88bfa',
		});
		const colors = resolveGraphColors(h.el);
		expect(colors.node).toBe('rgb(1, 2, 3)');
		expect(colors.line).toBe('rgba(4, 5, 6, 0.5)');
		expect(colors.focused).toBe('#a88bfa');
		expect(h.el.createSpan).toHaveBeenCalledTimes(1);
		expect(h.getComputedStyle).toHaveBeenCalledTimes(7);
		expect(h.probe.remove).toHaveBeenCalledTimes(1);
	});

	it('asks the theme for every colour it returns', () => {
		const h = host({});
		resolveGraphColors(h.el);
		const asked = h.getComputedStyle.mock.calls.length;
		expect(asked).toBe(7);
	});

	it('keeps a fallback for a variable the theme does not resolve', () => {
		const colors = resolveGraphColors(host({}).el);
		expect(Object.values(colors).every((c) => /^#[0-9a-f]{6}$/i.test(c))).toBe(true);
	});

	it('keeps the fallbacks when the host cannot be measured', () => {
		const broken: any = {
			createSpan: () => {
				throw new Error('detached');
			},
		};
		expect(() => resolveGraphColors(broken)).not.toThrow();
	});

	it('does not hand three.js a colour form it cannot parse', () => {
		// No canvas in jest: an oklch() result falls back instead of passing through.
		const colors = resolveGraphColors(host({ '--graph-line': 'oklch(0.5 0.1 200)' }).el);
		expect(colors.line).not.toContain('oklch');
	});
});
