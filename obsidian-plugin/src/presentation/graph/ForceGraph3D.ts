/**
 * The one place the 3D graph library is loaded, and the one place theme colours are
 * resolved for it. Both graph views draw through this module.
 */

/** 3d-force-graph's kapsule call form: `ForceGraph3D()(element)`. */
export type ForceGraph3DFactory = () => (element: HTMLElement) => ForceGraph3DInstance;

/**
 * The 3d-force-graph instance methods the views call; each setter returns the instance.
 * The package's own typings import three's, which this project does not install.
 */
export type ForceGraph3DInstance = {
	[K in
		| 'width'
		| 'height'
		| 'backgroundColor'
		| 'graphData'
		| 'nodeLabel'
		| 'nodeColor'
		| 'nodeVal'
		| 'linkColor'
		| 'linkWidth'
		| 'linkDirectionalArrowLength'
		| 'linkDirectionalArrowRelPos'
		| 'onNodeClick'
		| 'onNodeHover'
		| 'd3Force'
		| 'zoomToFit']: (...args: any[]) => ForceGraph3DInstance;
} & { _destructor?: () => void };

/**
 * `require('3d-force-graph')`, called on the first 3D render rather than at module top:
 * the package pulls in three.js, most of the bundle, and esbuild evaluates it on the
 * first call of this function instead of at plugin load. The package is ESM-only, so the
 * required value can be its namespace object (`{ default }`) or the function; take either.
 */
export function loadForceGraph3D(): ForceGraph3DFactory {
	const mod = require('3d-force-graph') as ForceGraph3DFactory | { default: ForceGraph3DFactory };
	return 'default' in mod ? mod.default : mod;
}

/** The colours a 3D drawing needs, as strings three.js can parse. */
export interface GraphColors {
	/** Scene background: the pane colour, so the canvas sits in the theme. */
	background: string;
	/** `--graph-node` */
	node: string;
	/** `--graph-node-focused` */
	focused: string;
	/** `--graph-node-unresolved` */
	unresolved: string;
	/** `--graph-line` */
	line: string;
	/** `--color-green` */
	green: string;
	/** `--color-orange` */
	orange: string;
}

/** Variable behind each colour, with the value used where Obsidian does not define it. */
const COLOR_VARS: Record<keyof GraphColors, [name: string, fallback: string]> = {
	background: ['--background-primary', '#1e1e1e'],
	node: ['--graph-node', '#999999'],
	focused: ['--graph-node-focused', '#a88bfa'],
	unresolved: ['--graph-node-unresolved', '#666666'],
	line: ['--graph-line', '#555555'],
	green: ['--color-green', '#08b94e'],
	orange: ['--color-orange', '#e9973f'],
};

/** `rgb()`, `rgba()` or `#hex`: forms three.js and 3d-force-graph read as they are. */
const DIRECT_FORM = /^(rgba?\(|#)/i;

/**
 * Rewrite a computed colour that three.js may not parse (`color(srgb …)`, `oklch(…)`) as
 * `rgba(…)`, by painting it on a one-pixel canvas. Null when no canvas is available.
 */
function viaCanvas(css: string): string | null {
	try {
		const ctx = createEl('canvas', { attr: { width: '1', height: '1' } }).getContext('2d', {
			willReadFrequently: true,
		});
		if (!ctx) return null;
		ctx.fillStyle = css;
		ctx.fillRect(0, 0, 1, 1);
		const [r, g, b, a] = ctx.getImageData(0, 0, 1, 1).data;
		return `rgba(${r}, ${g}, ${b}, ${Math.round((a / 255) * 100) / 100})`;
	} catch {
		return null;
	}
}

/**
 * Read the theme's graph colours from `host`'s computed style.
 *
 * WebGL cannot read CSS, so a 3D drawing calls this once when it is built (and again on a
 * theme change), not per frame and not per node. Each variable is applied to a probe
 * element's `color`, so any form the theme writes (`hsl(var(--accent-h) …)`, `color-mix`,
 * a bare variable chain) arrives resolved.
 */
export function resolveGraphColors(host: HTMLElement): GraphColors {
	const out = {} as GraphColors;
	for (const key of Object.keys(COLOR_VARS) as (keyof GraphColors)[]) {
		out[key] = COLOR_VARS[key][1];
	}
	let probe: HTMLElement | null = null;
	try {
		probe = host.createSpan();
		const computed = (host.win ?? window).getComputedStyle.bind(host.win ?? window);
		for (const key of Object.keys(COLOR_VARS) as (keyof GraphColors)[]) {
			const [name, fallback] = COLOR_VARS[key];
			probe.style.color = `var(${name}, ${fallback})`;
			const resolved = computed(probe).color;
			if (!resolved) continue;
			out[key] = DIRECT_FORM.test(resolved) ? resolved : (viaCanvas(resolved) ?? fallback);
		}
	} catch {
		// A host that cannot be measured keeps the fallbacks.
	} finally {
		probe?.remove?.();
	}
	return out;
}
