// A comment in src/**/*.ts or src/styles/**/*.css states the contract and the present reason, and
// carries no date, emoji, history narrative or audit finding id. History belongs in docs/history.
// `prose: ok <reason>` on the same line exempts one deliberate use.
// tests/test-setup.ts mocks fs, and the TypeScript compiler needs the real one at load (rule Q3).
jest.unmock('fs');
import * as fs from 'fs';
import * as path from 'path';
import * as ts from 'typescript';

const src = path.resolve(__dirname, '../../src');
const EXEMPT = 'prose: ok';

// A pointer to docs/history/<file> is a reference, not a record; its path may carry a date.
const HISTORY_POINTER = /docs\/history\/\S+/g;
const DATE = /\b(19|20)\d\d-\d\d(-\d\d)?\b/;
const EMOJI = /[\u{1F300}-\u{1FAFF}⚠⛔✅❌]/u;
const NARRATIVE =
	/\b(used to|previously|formerly|originally|until recently|turned out|incident|post-?mortem|hotfix|regression|the fix for|bug hunt)\b|\b(NEW|FIX):/i;
// A letter and digits in parentheses, or as a leading label before a colon, or a work-batch label.
const FINDING =
	/\([A-Z]{1,2}\d{1,2}[a-z]?(?:[/,-] ?[A-Z]{1,2}\d{1,2}[a-z]?)*(?:\)| in\b)|^\s*(?:\/\/|\/\*+|\*)?\s*[A-Z]{1,2}\d{1,2}[a-z]?(?:[/-][A-Z]{1,2}\d{1,2}[a-z]?)*:|\b(?:batch|wave) \d+\b/;

type Line = { file: string; line: number; text: string };

function walk(dir: string, ext: string): string[] {
	return fs
		.readdirSync(dir, { withFileTypes: true })
		.flatMap((e) =>
			e.isDirectory()
				? walk(path.join(dir, e.name), ext)
				: e.name.endsWith(ext)
					? [path.join(dir, e.name)]
					: [],
		);
}

/** One entry per source line of every comment in a TypeScript file; strings are never comments. */
function tsCommentLines(source: string): { line: number; text: string }[] {
	const sf = ts.createSourceFile('x.ts', source, ts.ScriptTarget.Latest, true);
	const seen = new Set<number>();
	const out: { line: number; text: string }[] = [];
	const take = (ranges: ts.CommentRange[] | undefined) => {
		for (const r of ranges ?? []) {
			if (seen.has(r.pos)) continue;
			seen.add(r.pos);
			const first = sf.getLineAndCharacterOfPosition(r.pos).line;
			source
				.slice(r.pos, r.end)
				.split('\n')
				.forEach((text, i) => out.push({ line: first + i + 1, text }));
		}
	};
	const visit = (node: ts.Node) => {
		take(ts.getLeadingCommentRanges(source, node.getFullStart()));
		take(ts.getTrailingCommentRanges(source, node.getEnd()));
		ts.forEachChild(node, visit);
	};
	visit(sf);
	take(ts.getLeadingCommentRanges(source, sf.endOfFileToken.getFullStart()));
	return out;
}

/** One entry per source line of every block comment in a stylesheet. */
function cssCommentLines(css: string): { line: number; text: string }[] {
	const out: { line: number; text: string }[] = [];
	for (const m of css.matchAll(/\/\*[\s\S]*?\*\//g)) {
		const first = css.slice(0, m.index).split('\n').length;
		m[0].split('\n').forEach((text, i) => out.push({ line: first + i, text }));
	}
	return out;
}

function comments(): Line[] {
	const lines: Line[] = [];
	for (const file of walk(src, '.ts')) {
		for (const c of tsCommentLines(fs.readFileSync(file, 'utf8'))) {
			lines.push({ file: path.relative(src, file), ...c });
		}
	}
	for (const file of walk(src, '.css')) {
		for (const c of cssCommentLines(fs.readFileSync(file, 'utf8'))) {
			lines.push({ file: path.relative(src, file), ...c });
		}
	}
	return lines;
}

const offenders = (re: RegExp, lines: Line[]) =>
	lines
		.filter((l) => re.test(l.text.replace(HISTORY_POINTER, '')) && !l.text.includes(EXEMPT))
		.map((l) => `${l.file}:${l.line}`);

describe('comments in src carry no record', () => {
	const lines = comments();

	test('the scan reads comments, not strings', () => {
		const found = tsCommentLines(
			['const a = "// not a comment 2026-01-01";', '// real', '/** doc', ' * more */'].join(
				'\n',
			),
		);
		expect(found.map((c) => c.text.trim())).toEqual(['// real', '/** doc', '* more */']);
		expect(cssCommentLines('a{}\n/* x\n y */').map((c) => c.line)).toEqual([2, 3]);
		expect(lines.length).toBeGreaterThan(100);
	});

	test('the patterns see a violation and honour the exemption', () => {
		const bad = (text: string): Line[] => [{ file: 'f', line: 1, text }];
		expect(offenders(DATE, bad('// fixed 2026-09-08'))).toHaveLength(1);
		expect(offenders(EMOJI, bad('// ⚠ careful'))).toHaveLength(1);
		expect(offenders(NARRATIVE, bad('// this used to crash'))).toHaveLength(1);
		expect(offenders(FINDING, bad('// guard the edge (G5)'))).toHaveLength(1);
		expect(offenders(NARRATIVE, bad('// this used to crash prose: ok quoting'))).toHaveLength(
			0,
		);
		expect(offenders(DATE, bad('// see docs/history/2026-09-08-x.md'))).toHaveLength(0);
	});

	test('no comment carries a date', () => {
		expect(offenders(DATE, lines)).toEqual([]);
	});

	test('no comment carries an emoji', () => {
		expect(offenders(EMOJI, lines)).toEqual([]);
	});

	test('no comment tells a history', () => {
		expect(offenders(NARRATIVE, lines)).toEqual([]);
	});

	test('no comment cites a finding id', () => {
		expect(offenders(FINDING, lines)).toEqual([]);
	});
});
