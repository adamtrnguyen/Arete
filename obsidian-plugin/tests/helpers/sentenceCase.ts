/** Words that keep their capitals in a sentence-case UI string. */
export const PROPER_NOUNS = new Set([
	'Arete',
	'Anki',
	'AnkiConnect',
	'Obsidian',
	'FSRS',
	'Python',
	'YAML',
	'Debug:',
	'Graph:',
]);

/** True when every word after the first is lowercase or a proper noun. */
export function isSentenceCase(text: string): boolean {
	return text
		.split(/\s+/)
		.slice(1)
		.every((word) => {
			const bare = word.replace(/^[("]+|[)":.,]+$/g, '');
			return !/^[A-Z][a-z]/.test(bare) || PROPER_NOUNS.has(bare);
		});
}
