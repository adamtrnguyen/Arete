// CSS that departs from Obsidian's variables may shrink, never grow (rules N7, N9).
// Lower a baseline in the commit that lowers its count. Target for every row: 0.
// tests/test-setup.ts mocks fs, so the real module is taken explicitly (rule Q3).
import * as path from 'path';

const fs = jest.requireActual('fs') as typeof import('fs');

const styles = path.resolve(__dirname, '../../src/styles');

type Row = { re: RegExp; max: number; use: string };

const BASELINE: Record<string, Row> = {
	pxValue: {
		re: /\b\d+(\.\d+)?px\b/g,
		max: 17,
		use: 'var(--size-*) (spacing), var(--border-width) (borders)',
	},
	rgbVariable: {
		re: /var\(--[\w-]*rgb[\w-]*\)/g,
		max: 0,
		use: 'color-mix(in oklch, var(--color-x) N%, transparent)',
	},
	cursorPointer: {
		re: /cursor:\s*pointer/g,
		max: 0,
		use: 'var(--cursor) for controls, var(--cursor-link) for links',
	},
	zIndexLiteral: { re: /z-index:\s*-?\d+/g, max: 2, use: 'var(--layer-*)' },
	transitionAll: { re: /transition:\s*all\b/g, max: 0, use: 'a list of named properties' },
	important: { re: /!important/g, max: 7, use: 'raise specificity instead' },
};

/** Source of a stylesheet without its comments, so prose in a comment is not counted. */
const strip = (css: string) => css.replace(/\/\*[\s\S]*?\*\//g, '');

function count(re: RegExp, css: string): number {
	return (strip(css).match(re) || []).length;
}

describe('src/styles stays within the recorded baseline', () => {
	const files = fs.readdirSync(styles).filter((f) => f.endsWith('.css') && f !== 'index.css');
	const sources = files.map((f) => ({ f, css: fs.readFileSync(path.join(styles, f), 'utf8') }));

	test.each(Object.entries(BASELINE))('%s', (key, row) => {
		const n = sources.reduce((sum, { css }) => sum + count(row.re, css), 0);
		if (n > row.max) throw new Error(`${key}: ${n} > baseline ${row.max}. Use ${row.use}.`);
	});

	test('the counter sees a violation', () => {
		expect(count(BASELINE.pxValue.re, '.a { margin: 4px; }')).toBe(1);
		expect(count(BASELINE.cursorPointer.re, '.a { cursor: pointer; }')).toBe(1);
		expect(count(BASELINE.important.re, '.a { color: red /* 3px !important */; }')).toBe(0);
	});
});
