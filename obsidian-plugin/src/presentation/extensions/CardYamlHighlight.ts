/**
 * Organized highlighting for the `cards:` block of an Arete note in Source mode.
 *
 * Obsidian colors frontmatter as generic YAML: every key one color, every value
 * plain text. This marks what a card author scans for: where each card starts,
 * question vs answer, prerequisites, and the machine-written bookkeeping to skip.
 */
import { Decoration, DecorationSet, EditorView, ViewPlugin, ViewUpdate } from '@codemirror/view';
import { Range } from '@codemirror/state';
import { CardParserService } from '@/application/services/CardParserService';

type Role = 'q' | 'a' | 'deps' | 'meta' | 'field';

const ROLE: Record<string, Role> = {
	front: 'q',
	text: 'q',
	back: 'a',
	'back extra': 'a',
	extra: 'a',
	deps: 'deps',
	requires: 'deps',
	related: 'deps',
	id: 'meta',
	anki: 'meta',
	nid: 'meta',
	cid: 'meta',
	model: 'meta',
	deck: 'meta',
	tags: 'meta',
};

// `  - Front: …`, `  Back Extra: …`, `"Back Extra": …`
const KEY = /^(\s*(?:-\s+)?)(["']?)([A-Za-z][\w ]*?)\2:(?=\s|$)/;
const CLOZE = /\{\{c(\d+)::[\s\S]*?\}\}/g;
const MATH = /\$\$[^$]+\$\$|\$[^$\n]+\$/g;

const band = [
	Decoration.line({ class: 'arete-yaml-card arete-yaml-card-even' }),
	Decoration.line({ class: 'arete-yaml-card arete-yaml-card-odd' }),
];
const cardStart = Decoration.line({ class: 'arete-yaml-card-start' });
const keyMark = (role: Role) => Decoration.mark({ class: `arete-yk arete-yk-${role}` });
const valueMark = (role: Role) => Decoration.mark({ class: `arete-yv arete-yv-${role}` });
const clozeMark = (n: number) =>
	Decoration.mark({ class: `arete-yaml-cloze arete-yaml-cloze-${((n - 1) % 4) + 1}` });
const mathMark = Decoration.mark({ class: 'arete-yaml-math' });

function build(view: EditorView): DecorationSet {
	const doc = view.state.doc;
	const { ranges, hasCards } = CardParserService.parseCards(doc.toString());
	if (!hasCards) return Decoration.none;

	const decos: Range<Decoration>[] = [];
	for (const card of ranges) {
		let role: Role = 'field';
		// Inside a `key: |-` block scalar every more-indented line is content, even one
		// that looks like `Word: text`; this holds the owning key's column, or -1.
		let blockKeyColumn = -1;
		for (let i = card.startLine; i <= card.endLine && i < doc.lines; i++) {
			const line = doc.line(i + 1); // ranges are 0-based, CodeMirror lines 1-based
			decos.push(band[card.index % 2].range(line.from));
			if (i === card.startLine) decos.push(cardStart.range(line.from));
			if (!line.text.trim()) continue;

			let valueFrom: number;
			const indent = line.text.search(/\S/);
			if (blockKeyColumn >= 0 && indent <= blockKeyColumn) blockKeyColumn = -1;
			const m = blockKeyColumn >= 0 ? null : KEY.exec(line.text);
			if (m) {
				role = ROLE[m[3].toLowerCase()] ?? 'field';
				const keyFrom = line.from + m[1].length;
				const keyTo = keyFrom + m[2].length * 2 + m[3].length + 1; // incl. quotes and ':'
				decos.push(keyMark(role).range(keyFrom, keyTo));
				valueFrom = keyTo;
				if (/^\s*[|>][-+]?\s*$/.test(line.text.slice(keyTo - line.from))) {
					blockKeyColumn = m[1].length;
				}
			} else {
				// continuation of a |- block, or a list item under requires/related
				valueFrom = line.from + line.text.search(/\S/);
			}
			while (valueFrom < line.to && doc.sliceString(valueFrom, valueFrom + 1) === ' ') {
				valueFrom++;
			}
			if (valueFrom >= line.to) continue;
			decos.push(valueMark(role).range(valueFrom, line.to));

			const value = doc.sliceString(valueFrom, line.to);
			for (const c of value.matchAll(CLOZE)) {
				const from = valueFrom + (c.index ?? 0);
				decos.push(clozeMark(Number(c[1])).range(from, from + c[0].length));
			}
			for (const x of value.matchAll(MATH)) {
				const from = valueFrom + (x.index ?? 0);
				decos.push(mathMark.range(from, from + x[0].length));
			}
		}
	}
	return Decoration.set(decos, true);
}

export const cardYamlHighlight = ViewPlugin.fromClass(
	class {
		decorations: DecorationSet;
		constructor(view: EditorView) {
			this.decorations = build(view);
		}
		update(update: ViewUpdate) {
			if (update.docChanged) this.decorations = build(update.view);
		}
	},
	{ decorations: (v) => v.decorations },
);
