// Every CSS variable src/styles reads must be one Obsidian defines (rule N7). A name Obsidian
// does not define resolves to nothing: the property falls back to its initial value, and the
// element can turn invisible. Each name below resolved to a value in a live Obsidian
// (skills/gui-test). Add a name only after checking it there.
// tests/test-setup.ts mocks fs, so the real module is taken explicitly (rule Q3).
import * as path from 'path';

const fs = jest.requireActual('fs') as typeof import('fs');

const styles = path.resolve(__dirname, '../../src/styles');

const OBSIDIAN_VARIABLES = new Set([
	'--anim-duration-fast',
	'--anim-duration-superfast',
	'--background-modifier-border',
	'--background-modifier-border-focus',
	'--background-modifier-hover',
	'--background-primary',
	'--background-primary-alt',
	'--background-secondary',
	'--background-secondary-alt',
	'--border-width',
	'--code-size',
	'--color-blue',
	'--color-cyan',
	'--color-green',
	'--color-orange',
	'--color-pink',
	'--color-purple',
	'--color-red',
	'--color-yellow',
	'--cursor',
	'--cursor-link',
	'--font-bold',
	'--font-extrabold',
	'--font-medium',
	'--font-monospace',
	'--font-normal',
	'--font-semibold',
	'--font-smallest',
	'--font-text',
	'--font-text-size',
	'--font-ui-medium',
	'--font-ui-small',
	'--font-ui-smaller',
	'--graph-line',
	'--graph-node',
	'--graph-node-focused',
	'--graph-node-unresolved',
	'--graph-text',
	'--icon-xs',
	'--interactive-accent',
	'--interactive-accent-hover',
	'--layer-cover',
	'--layer-popover',
	'--line-height-normal',
	'--radius-l',
	'--radius-m',
	'--radius-s',
	'--shadow-l',
	'--shadow-s',
	'--size-2-1',
	'--size-2-2',
	'--size-2-3',
	'--size-4-1',
	'--size-4-12',
	'--size-4-16',
	'--size-4-2',
	'--size-4-3',
	'--size-4-4',
	'--size-4-5',
	'--size-4-6',
	'--size-4-8',
	'--size-4-9',
	'--text-accent',
	'--text-error',
	'--text-faint',
	'--text-muted',
	'--text-normal',
	'--text-on-accent',
	'--text-success',
	'--text-warning',
]);

/** The variables a stylesheet reads, comments left out. */
function variablesRead(css: string): string[] {
	const source = css.replace(/\/\*[\s\S]*?\*\//g, '');
	return [...source.matchAll(/var\(\s*(--[\w-]+)/g)].map((m) => m[1]);
}

describe('src/styles reads only variables Obsidian defines', () => {
	const files = fs.readdirSync(styles).filter((f) => f.endsWith('.css'));

	test.each(files)('%s', (file) => {
		const css = fs.readFileSync(path.join(styles, file), 'utf8');
		const unknown = [...new Set(variablesRead(css))].filter((v) => !OBSIDIAN_VARIABLES.has(v));
		expect(unknown).toEqual([]);
	});

	test('the reader finds a variable and skips comments', () => {
		expect(variablesRead('.a { color: var(--x-y); } /* var(--z) */')).toEqual(['--x-y']);
	});
});
