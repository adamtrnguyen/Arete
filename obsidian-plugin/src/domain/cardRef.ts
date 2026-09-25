/**
 * Which card a reference names: an Arete id, or a 1-based position.
 *
 * Positions matter because a card drafted by an agent has no id until its first sync.
 * Returns null when nothing matches, so a caller can say so instead of guessing.
 */
export function resolveCardIndex(cards: unknown[], ref?: string): number | null {
	if (!ref) return cards.length > 0 ? 0 : null;
	const byId = cards.findIndex((c) => (c as { id?: unknown } | null)?.id === ref);
	if (byId >= 0) return byId;
	const position = Number(ref);
	if (Number.isInteger(position) && position >= 1 && position <= cards.length) {
		return position - 1;
	}
	return null;
}
