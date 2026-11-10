/**
 * One place that knows the shape of a card, a note and a stats row.
 *
 * Arete writes ids nested (`anki: {nid: ...}`) and the backend sends FSRS difficulty on
 * a 0..1 scale. A hand-written fixture can disagree with both and still pass
 * (docs/history/test-factories.md).
 *
 * Build fixtures here so a format change is one edit, and so the types are checked.
 */

import type { AnkiCardStats } from '@/domain/stats';

const TEST_DECK = 'Test::Deck';

/** A card as it appears in note frontmatter. Ids nest under `anki`, as Arete writes them. */
export interface CardFixture {
	id?: string;
	model?: string;
	deck?: string;
	Front: string;
	Back: string;
	anki?: { nid?: string; cid?: string };
	deps?: { requires: string[]; related: string[] };
}

export function card(
	overrides: Partial<CardFixture> & { nid?: string | number } = {},
): CardFixture {
	const { nid, ...rest } = overrides;
	const built: CardFixture = {
		id: 'arete_01TESTCARD0000000000000001',
		model: 'Basic',
		Front: 'What is a port?',
		Back: 'What a caller needs, not how it is done.',
		...rest,
	};
	if (nid !== undefined) {
		built.anki = { nid: String(nid), cid: String(nid), ...(rest.anki ?? {}) };
	}
	return built;
}

/** The same note as text on disk, for anything that parses raw YAML. */
export function noteYaml(
	options: { deck?: string; model?: string; cards?: CardFixture[]; body?: string } = {},
): string {
	const cards = options.cards ?? [card()];
	const lines = [
		'---',
		'arete: true',
		`deck: ${options.deck ?? TEST_DECK}`,
		`model: ${options.model ?? 'Basic'}`,
		'cards:',
	];
	for (const c of cards) {
		lines.push(`  - model: ${c.model ?? 'Basic'}`);
		if (c.id) lines.push(`    id: ${c.id}`);
		if (c.deck) lines.push(`    deck: ${c.deck}`);
		lines.push(`    Front: |-`, `      ${c.Front}`);
		lines.push(`    Back: |-`, `      ${c.Back}`);
		if (c.anki) {
			lines.push('    anki:');
			if (c.anki.nid !== undefined) lines.push(`      nid: '${c.anki.nid}'`);
			if (c.anki.cid !== undefined) lines.push(`      cid: '${c.anki.cid}'`);
		}
	}
	lines.push('---', '', options.body ?? '# Body', '');
	return lines.join('\n');
}

/**
 * A stats row as the backend sends it.
 *
 * `difficulty` is normalized 0..1. Views scale it with `difficultyOutOfTen`.
 */
export function ankiCardStats(overrides: Partial<AnkiCardStats> = {}): AnkiCardStats {
	return {
		cardId: 1,
		noteId: 101,
		lapses: 0,
		ease: 2500,
		difficulty: 0.3,
		deckName: TEST_DECK,
		interval: 10,
		due: 0,
		reps: 5,
		averageTime: 5000,
		...overrides,
	};
}
