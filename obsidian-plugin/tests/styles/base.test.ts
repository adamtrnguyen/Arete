// Layout rules of src/styles/base.css that jest can pin without a browser.
// tests/test-setup.ts mocks fs, so the real module is taken explicitly (rule Q3).
import * as path from 'path';

const fs = jest.requireActual('fs') as typeof import('fs');

const css = fs
	.readFileSync(path.resolve(__dirname, '../../src/styles/base.css'), 'utf8')
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

describe('base.css', () => {
	it('spaces the status-bar icon from its label', () => {
		// Obsidian's own status-bar items with an icon carry no text, so it adds no gap.
		expect(declarations('.arete-status-bar')).toMatch(/gap:\s*var\(--size-[\d-]+\)/);
	});
});
