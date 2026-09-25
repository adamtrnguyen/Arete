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

export function renderCloze(text: string, ordinal: number, side: 'Front' | 'Back'): string {
	return text.replace(CLOZE, (_match, n: string, answer: string, hint?: string) => {
		if (Number(n) !== ordinal) return answer;
		const shown = side === 'Front' ? `[${hint ?? '...'}]` : answer;
		return `<span class="cloze">${shown}</span>`;
	});
}
