/**
 * Anki cloze deletions, {{c1::answer}} or {{c1::answer::hint}}, rendered the way Anki
 * shows one card: the active deletion hidden on the front and revealed on the back,
 * every other deletion shown as plain text.
 *
 * Like Anki, a deletion ends at the first `}}`; math that needs `}}` inside a cloze
 * must be spaced (`} }`) in Anki too.
 */
const CLOZE = /\{\{c(\d+)::([\s\S]*?)(?:::([\s\S]*?))?\}\}/g;

/** Cloze numbers used in a field, ascending and unique. */
export function clozeOrdinals(text: string): number[] {
	const found = new Set<number>();
	for (const m of text.matchAll(CLOZE)) found.add(Number(m[1]));
	return [...found].sort((a, b) => a - b);
}

/** `$$...$$` and `$...$` spans, the delimiters the markdown renderer hands to MathJax. */
const MATH = /\$\$[\s\S]+?\$\$|\$[^$\n]+?\$/g;

export function renderCloze(text: string, ordinal: number, side: 'Front' | 'Back'): string {
	const math = [...text.matchAll(MATH)].map((m) => [m.index, m.index + m[0].length]);
	const inMath = (at: number) => math.some(([from, to]) => at > from && at < to);
	return text.replace(
		CLOZE,
		(_match, n: string, answer: string, hint: string | undefined, at: number) => {
			if (Number(n) !== ordinal) return answer;
			const shown = side === 'Front' ? `[${hint ?? '...'}]` : answer;
			// Inside math the text becomes TeX, where a tag would print as characters.
			return inMath(at) ? shown : `<span class="cloze">${shown}</span>`;
		},
	);
}
