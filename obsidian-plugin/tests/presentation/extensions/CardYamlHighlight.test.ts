/**
 * The card YAML highlight and the card gutter run on every keystroke in every
 * markdown editor. These tests pin how often the note is parsed per transaction,
 * and that reusing the previous decorations gives exactly what a rebuild gives.
 */
jest.unmock('@codemirror/state');
// The real package, loaded after giving the shared document stub the `body.style`
// that @codemirror/view reads at load time.
jest.mock('@codemirror/view', () => {
	const body = (global as any).document.body;
	body.style = body.style ?? {};
	return jest.requireActual('@codemirror/view');
});
jest.mock('obsidian', () => ({
	Notice: jest.fn(),
	// parseYaml is unavailable under jest; CardParserService falls back to its regex.
	parseYaml: () => {
		throw new Error('parseYaml unavailable');
	},
}));

import { EditorState, RangeSet, TransactionSpec } from '@codemirror/state';
import type { DecorationSet } from '@codemirror/view';
import { CardParserService } from '@/application/services/CardParserService';
import { createCardGutter } from '@/presentation/extensions/CardGutterExtension';
import { cardYamlHighlight } from '@/presentation/extensions/CardYamlHighlight';
import { card, noteYaml } from '../../factories';

type FakeView = { state: EditorState };
type Highlight = { decorations: DecorationSet; update(u: unknown): void };

/** An editor with both extensions, as main.ts registers them, driven like CodeMirror does. */
function editor(
	doc: string,
	extensions: unknown[] = [cardYamlHighlight, createCardGutter(jest.fn())],
) {
	const view: FakeView = { state: EditorState.create({ doc, extensions: extensions as any }) };
	const highlight: Highlight = (cardYamlHighlight as any).create(view);
	const apply = (spec: TransactionSpec) => {
		const tr = view.state.update(spec);
		const startState = view.state;
		view.state = tr.state;
		highlight.update({
			view,
			state: tr.state,
			startState,
			changes: tr.changes,
			docChanged: tr.docChanged,
			transactions: [tr],
		});
	};
	/** What a highlight built from scratch on the current state shows. */
	const fresh = () => ((cardYamlHighlight as any).create(view) as Highlight).decorations;
	return { view, highlight, apply, fresh };
}

const cardNote = () =>
	noteYaml({
		cards: [
			card({ Front: 'What is {{c1::a port}}?', Back: 'An interface $x^2$', nid: 1001 }),
			card({ Front: 'Second question', Back: 'Second answer', nid: 1002 }),
		],
		body: '# Body\n\nSome text.',
	});
const posOf = (s: EditorState, text: string) => s.doc.toString().indexOf(text);

describe('parses per transaction', () => {
	let parse: jest.SpyInstance;
	beforeEach(() => {
		parse = jest.spyOn(CardParserService, 'parseCards');
	});
	afterEach(() => parse.mockRestore());

	it('parses a card note once when it opens, shared by both extensions', () => {
		editor(cardNote());
		expect(parse).toHaveBeenCalledTimes(1);
	});

	it('parses once for an edit inside the frontmatter', () => {
		const e = editor(cardNote());
		parse.mockClear();
		e.apply({ changes: { from: posOf(e.view.state, 'Second question'), insert: 'x' } });
		expect(parse).toHaveBeenCalledTimes(1);
	});

	it('does not parse for an edit after the frontmatter', () => {
		const e = editor(cardNote());
		parse.mockClear();
		e.apply({ changes: { from: e.view.state.doc.length, insert: 'more body' } });
		e.apply({ changes: { from: posOf(e.view.state, 'Some text'), insert: '\n\n' } });
		expect(parse).not.toHaveBeenCalled();
	});

	it('does not parse for a selection change', () => {
		const e = editor(cardNote());
		parse.mockClear();
		e.apply({ selection: { anchor: 3 } });
		expect(parse).not.toHaveBeenCalled();
	});

	it('never parses a note without a cards key', () => {
		const e = editor('---\ntitle: x\ntags: [a]\n---\n# Plain note\n');
		e.apply({ changes: { from: posOf(e.view.state, 'title'), insert: 'sub' } });
		e.apply({ changes: { from: e.view.state.doc.length, insert: 'typing' } });
		const noFrontmatter = editor('# Plain\n\nno frontmatter here\n');
		noFrontmatter.apply({ changes: { from: 0, insert: 'x' } });
		expect(parse).not.toHaveBeenCalled();
	});

	it('parses a note without frontmatter whose body has a cards key', () => {
		const e = editor('# Title\ncards:\n  - Front: q\n    Back: a\n');
		parse.mockClear();
		e.apply({ changes: { from: e.view.state.doc.length, insert: 'x' } });
		expect(parse).toHaveBeenCalledTimes(1);
	});

	it('parses once when only the highlight is registered', () => {
		editor(cardNote(), [cardYamlHighlight]);
		expect(parse).toHaveBeenCalledTimes(1);
	});
});

describe('highlight decorations', () => {
	const edits: [string, (s: EditorState) => TransactionSpec][] = [
		['typing in the body', (s) => ({ changes: { from: s.doc.length, insert: 'abc' } })],
		['a new line in the body', (s) => ({ changes: { from: posOf(s, 'Some'), insert: '\n' } })],
		[
			'typing in a card',
			(s) => ({ changes: { from: posOf(s, 'Second answer'), insert: '$y$ ' } }),
		],
		[
			'a new line in a card',
			(s) => ({ changes: { from: posOf(s, 'Second answer'), insert: '\n      ' } }),
		],
		[
			'a cloze in a card',
			(s) => ({ changes: { from: posOf(s, 'Second question'), insert: '{{c2::z}} ' } }),
		],
		[
			'editing a key above cards',
			(s) => ({ changes: { from: posOf(s, 'deck:'), insert: 'x' } }),
		],
		[
			'typing before the closing ---',
			(s) => ({ changes: { from: posOf(s, '---\n\n#'), insert: '-' } }),
		],
		[
			'typing after the closing ---',
			(s) => ({ changes: { from: posOf(s, '---\n\n#') + 3, insert: '-' } }),
		],
		[
			'deleting the last card',
			(s) => {
				const text = s.doc.toString();
				return {
					changes: { from: text.lastIndexOf('  - model:'), to: posOf(s, '---\n\n#') },
				};
			},
		],
	];

	it.each(edits)('after %s, equal a fresh build', (_label, edit) => {
		const e = editor(cardNote());
		expect(RangeSet.eq([e.highlight.decorations], [e.fresh()])).toBe(true);
		e.apply(edit(e.view.state));
		expect(e.view.state.doc.toString()).not.toBe(cardNote());
		expect(RangeSet.eq([e.highlight.decorations], [e.fresh()])).toBe(true);
		// An empty fresh build would make the comparison vacuous.
		expect(e.fresh().size).toBeGreaterThan(0);
	});

	it('keeps the decorations when the edit is after the frontmatter', () => {
		const e = editor(cardNote());
		const before = e.highlight.decorations;
		e.apply({ changes: { from: e.view.state.doc.length, insert: 'abc' } });
		expect(e.highlight.decorations.size).toBe(before.size);
		expect(e.highlight.decorations.size).toBeGreaterThan(0);
	});
});
