import {
	parseSyncReport,
	pendingPrune,
	PruneReport,
	pruneSummary,
	SyncReport,
	syncSummary,
} from '@/domain/syncReport';

const prune = (over: Partial<PruneReport> = {}): PruneReport => ({
	status: 'unconfirmed',
	message: 'Prune not confirmed.',
	notes: [{ nid: 1, arete_id: 'arete_A', deck: 'D' }],
	decks: [],
	note_count: 1,
	deck_count: 0,
	...over,
});

describe('parseSyncReport', () => {
	test('reads the object arete sync --json prints', () => {
		const report = parseSyncReport(
			JSON.stringify({
				ok: true,
				generated: 3,
				changed: 2,
				errors: 0,
				message: null,
				prune: { status: 'clean', message: 'No orphans found.', notes: [], decks: [] },
			}),
		);
		expect(report).toEqual({
			ok: true,
			generated: 3,
			changed: 2,
			errors: 0,
			message: null,
			prune: {
				status: 'clean',
				message: 'No orphans found.',
				notes: [],
				decks: [],
				note_count: 0,
				deck_count: 0,
			},
		});
	});

	test('anything else is no report', () => {
		expect(parseSyncReport('')).toBeNull();
		expect(parseSyncReport('INFO: Starting sync')).toBeNull();
		expect(parseSyncReport('{}')).toBeNull();
		expect(parseSyncReport('[1]')).toBeNull();
	});
});

describe('pendingPrune', () => {
	const report = (p: PruneReport | null): SyncReport => ({
		ok: true,
		generated: 0,
		changed: 0,
		errors: 0,
		message: null,
		prune: p,
	});

	test('an unconfirmed preview with something to delete waits for a yes', () => {
		expect(pendingPrune(report(prune()))).toEqual(prune());
		expect(pendingPrune(report(prune({ note_count: 0, deck_count: 1 })))).not.toBeNull();
	});

	test('nothing to delete, another status or no prune asks nothing', () => {
		expect(pendingPrune(report(prune({ note_count: 0, deck_count: 0 })))).toBeNull();
		expect(pendingPrune(report(prune({ status: 'clean' })))).toBeNull();
		expect(pendingPrune(report(prune({ status: 'deleted' })))).toBeNull();
		expect(pendingPrune(report(null))).toBeNull();
		expect(pendingPrune(null)).toBeNull();
	});
});

describe('summaries', () => {
	const base: SyncReport = {
		ok: true,
		generated: 1,
		changed: 0,
		errors: 0,
		message: null,
		prune: null,
	};

	test('the sync summary counts changed cards and errors', () => {
		expect(syncSummary({ ...base, changed: 1 })).toBe(
			'Sync completed, 1 card changed in Anki.',
		);
		expect(syncSummary({ ...base, changed: 4 })).toBe(
			'Sync completed, 4 cards changed in Anki.',
		);
		expect(syncSummary(base)).toBe('Sync completed, Anki was already up to date.');
		expect(syncSummary({ ...base, errors: 2 })).toBe(
			'Sync finished with 2 errors, see the log.',
		);
	});

	test('only deleted says something was deleted; an unknown status shows its message', () => {
		expect(pruneSummary(prune({ status: 'deleted', note_count: 2, deck_count: 1 }))).toBe(
			'Deleted 2 notes and 1 deck from Anki.',
		);
		expect(pruneSummary(prune({ status: 'clean' }))).toBe('Nothing to prune.');
		expect(pruneSummary(prune({ status: 'skipped', message: 'Run on the vault.' }))).toBe(
			'Nothing was deleted. Run on the vault.',
		);
		expect(
			pruneSummary(prune({ status: 'skipped_failed_cards', message: 'Cards failed.' })),
		).toBe('Nothing was deleted. Cards failed.');
		expect(pruneSummary(prune({ status: 'failed', message: 'Anki refused.' }))).toBe(
			'Prune failed: Anki refused.',
		);
	});
});
