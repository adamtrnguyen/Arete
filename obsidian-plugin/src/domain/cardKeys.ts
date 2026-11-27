/**
 * Structural metadata keys on a card entry in frontmatter.
 * Every other scalar key is a card content field (e.g. Front, Back, Text).
 */
export const RESERVED_CARD_KEYS: ReadonlySet<string> = new Set([
	'id',
	'ID',
	'model',
	'Model',
	'deck',
	'Deck',
	'tags',
	'Tags',
	'anki',
	'Anki',
	'deps',
	'Deps',
	'prerequisites',
	'related',
	'markdown',
	'cid',
	'nid',
	'__line__',
]);

export function isReservedCardKey(key: string): boolean {
	return RESERVED_CARD_KEYS.has(key) || key.startsWith('__');
}
