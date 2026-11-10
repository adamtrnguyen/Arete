// Layout rules of src/styles/dashboard.css that jest can pin without a browser.
// tests/test-setup.ts mocks fs, so the real module is taken explicitly (rule Q3).
import * as path from 'path';

const fs = jest.requireActual('fs') as typeof import('fs');

const css = fs
	.readFileSync(path.resolve(__dirname, '../../src/styles/dashboard.css'), 'utf8')
	.replace(/\/\*[\s\S]*?\*\//g, '');

/** The declarations of every rule whose selector list names `selector` exactly. */
function declarations(selector: string): string {
	return [...css.matchAll(/([^{}]+)\{([^}]*)\}/g)]
		.filter((m) =>
			m[1]
				.split(',')
				.map((s) => s.trim())
				.includes(selector),
		)
		.map((m) => m[2])
		.join(';');
}

describe('dashboard.css', () => {
	it.each(['.arete-deck-header:hover', '.arete-leech-table tbody tr:hover'])(
		'%s shows the native hover background (rule N9)',
		(selector) => {
			expect(declarations(selector)).toMatch(
				/background(-color)?:\s*var\(--background-modifier-hover\)/,
			);
		},
	);

	it('keeps a badge on one line, so a two-word label does not wrap beside long text', () => {
		expect(declarations('.arete-badge')).toMatch(/white-space:\s*nowrap/);
	});
});
