import { parseYaml } from 'obsidian';

interface CardRange {
	index: number;
	startLine: number;
	endLine: number;
	nid: number | null;
	cid: number | null;
}

export interface ParseResult {
	ranges: CardRange[];
	frontmatterEndLine: number | null;
	hasCards: boolean;
}

export class CardParserService {
	/**
	 * The result `parseCards` returns for a note that has no `cards:` key where
	 * `parseCards` looks for one, or null when the note has such a key and needs
	 * `parseCards`. Takes the note's lines (as `text.split('\n')` yields them) lazily,
	 * so it allocates no copy of the text and stops at the closing `---` of the
	 * frontmatter.
	 *
	 * `parseCards` finds a card only after a line whose trimmed text starts with
	 * `cards:`, at line 1 or later and before the closing `---` when the note opens
	 * with `---`. With no such line its result is fixed by the frontmatter end alone.
	 */
	static noCardsResult(lines: Iterable<string>): ParseResult | null {
		let i = 0;
		let opensFrontmatter = false;
		for (const line of lines) {
			if (i === 0) {
				opensFrontmatter = line === '---';
			} else if (opensFrontmatter && line === '---') {
				return { ranges: [], frontmatterEndLine: i, hasCards: false };
			} else if (line.trim().startsWith('cards:')) {
				return null;
			}
			i++;
		}
		return { ranges: [], frontmatterEndLine: null, hasCards: false };
	}

	/**
	 * Parses a document string to find card ranges and their IDs.
	 * This is the primary logic for identifying cards in a note.
	 */
	static parseCards(docText: string): ParseResult {
		const docLines = docText.split('\n');
		const ranges: CardRange[] = [];
		let frontmatterEndLine: number | null = null;
		let inCards = false;
		let cardsBaseIndent = -1;

		// Find frontmatter end
		if (docLines[0] === '---') {
			for (let i = 1; i < docLines.length; i++) {
				if (docLines[i] === '---') {
					frontmatterEndLine = i;
					break;
				}
			}
		}

		for (let i = 1; i < docLines.length; i++) {
			const line = docLines[i];
			const trimmed = line.trim();

			// Stop at frontmatter end if we are in it
			if (line === '---' && frontmatterEndLine !== null && i === frontmatterEndLine) {
				// We reached the end of frontmatter
				break;
			}
			if (!trimmed) continue;

			if (!inCards) {
				if (trimmed === 'cards:' || trimmed.startsWith('cards:')) {
					inCards = true;
				}
				continue;
			}

			const indent = line.search(/\S/);
			if (trimmed.startsWith('- ')) {
				if (cardsBaseIndent === -1) cardsBaseIndent = indent;
				if (indent === cardsBaseIndent) {
					const startLine = i;
					let endLine = i;

					// Find end of this card block
					for (let j = i + 1; j < docLines.length; j++) {
						const nl = docLines[j];
						const nt = nl.trim();
						if (nl === '---' && frontmatterEndLine !== null) {
							endLine = j - 1;
							break;
						}
						if (!nt) {
							endLine = j;
							continue;
						}
						const ni = nl.search(/\S/);
						// New card item or decreased indent means block ended
						if (ni === cardsBaseIndent && nt.startsWith('- ')) {
							endLine = j - 1;
							break;
						}
						if (ni < cardsBaseIndent) {
							endLine = j - 1;
							break;
						}
						endLine = j;
					}

					const block = docLines.slice(startLine, endLine + 1).join('\n');
					const { nid, cid } = this.ankiIds(block);

					ranges.push({
						index: ranges.length,
						startLine,
						endLine: this.findLastContentLine(docLines, startLine, endLine),
						nid,
						cid,
					});
					i = endLine;
				}
			} else if (indent < cardsBaseIndent && trimmed.length > 0) {
				inCards = false;
			}
		}

		return {
			ranges,
			frontmatterEndLine,
			hasCards: ranges.length > 0,
		};
	}

	/**
	 * The Anki note id and card id of one card block, read from its `anki:` mapping
	 * with the YAML loader. A block that is not valid YAML, or has no `anki` mapping,
	 * has no ids: the card shows no stats until its YAML parses.
	 */
	private static ankiIds(block: string): { nid: number | null; cid: number | null } {
		let anki: unknown;
		try {
			// The list dash becomes a space of the same width, so every key keeps its
			// column and the block is one mapping.
			const data: unknown = parseYaml(block.replace(/^(\s*)-/, '$1 '));
			anki = data && typeof data === 'object' ? (data as { anki?: unknown }).anki : null;
		} catch {
			return { nid: null, cid: null };
		}
		if (!anki || typeof anki !== 'object' || Array.isArray(anki)) {
			return { nid: null, cid: null };
		}
		const ids = anki as { nid?: unknown; cid?: unknown };
		return { nid: this.ankiId(ids.nid), cid: this.ankiId(ids.cid) };
	}

	/** An id as YAML holds it: a number, or a string of digits when it was quoted. */
	private static ankiId(raw: unknown): number | null {
		if (typeof raw === 'number') return Number.isSafeInteger(raw) ? raw : null;
		if (typeof raw === 'string' && /^\d+$/.test(raw.trim())) return Number(raw.trim());
		return null;
	}

	/**
	 * Helper: Find last non-blank line in a range
	 */
	private static findLastContentLine(
		lines: string[],
		startLine: number,
		endLine: number,
	): number {
		for (let i = endLine; i >= startLine; i--) {
			if (lines[i].trim().length > 0) {
				return i;
			}
		}
		return startLine;
	}
}
