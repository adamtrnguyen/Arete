/** One Anki note prune deletes, or would delete. */
export interface PruneNote {
	nid: number;
	arete_id: string;
	deck: string;
}

/** The prune part of `arete sync --prune --json`: what was, or would be, deleted. */
export interface PruneReport {
	/** clean, deleted, unconfirmed, failed, skipped, refused, ...; any other means not deleted. */
	status: string;
	message: string;
	notes: PruneNote[];
	decks: string[];
	note_count: number;
	deck_count: number;
}

/** What `arete sync --json` prints: one object, on stdout. */
export interface SyncReport {
	ok: boolean;
	/** Cards the run compared with Anki. */
	generated: number;
	/** Cards added or updated in Anki. */
	changed: number;
	errors: number;
	/** Why a run stopped early or failed; null when it did not. */
	message: string | null;
	prune: PruneReport | null;
}

/** The report a sync printed, or null when stdout is not one (an older CLI, a crash). */
export function parseSyncReport(stdout: string): SyncReport | null {
	let value: any;
	try {
		value = JSON.parse(stdout);
	} catch {
		return null;
	}
	if (!value || typeof value !== 'object' || typeof value.ok !== 'boolean') return null;
	const prune = value.prune && typeof value.prune === 'object' ? value.prune : null;
	return {
		ok: value.ok,
		generated: Number(value.generated) || 0,
		changed: Number(value.changed) || 0,
		errors: Number(value.errors) || 0,
		message: typeof value.message === 'string' && value.message ? value.message : null,
		prune: prune && {
			status: String(prune.status ?? ''),
			message: String(prune.message ?? ''),
			notes: Array.isArray(prune.notes) ? prune.notes : [],
			decks: Array.isArray(prune.decks) ? prune.decks : [],
			note_count: Number(prune.note_count) || 0,
			deck_count: Number(prune.deck_count) || 0,
		},
	};
}

/** The prune preview waiting for the user's yes: unconfirmed, and something to delete. */
export function pendingPrune(report: SyncReport | null): PruneReport | null {
	const prune = report?.prune;
	if (!prune || prune.status !== 'unconfirmed') return null;
	return prune.note_count + prune.deck_count > 0 ? prune : null;
}

const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? '' : 's'}`;

/** One sentence on what the sync changed in Anki. */
export function syncSummary(report: SyncReport): string {
	if (report.errors > 0) {
		return `Sync finished with ${plural(report.errors, 'error')}, see the log.`;
	}
	return report.changed > 0
		? `Sync completed, ${plural(report.changed, 'card')} changed in Anki.`
		: 'Sync completed, Anki was already up to date.';
}

/** One sentence on what prune did. Any status other than deleted means nothing was deleted. */
export function pruneSummary(prune: PruneReport): string {
	switch (prune.status) {
		case 'deleted':
			return `Deleted ${plural(prune.note_count, 'note')} and ${plural(prune.deck_count, 'deck')} from Anki.`;
		case 'clean':
			return 'Nothing to prune.';
		case 'unconfirmed':
			return 'Nothing was deleted: the prune was not confirmed.';
		case 'failed':
			return `Prune failed: ${prune.message || 'see the log.'}`;
		default:
			return `Nothing was deleted. ${prune.message}`.trim();
	}
}
