import { dueEpochSeconds } from '@/domain/stats';

describe('dueEpochSeconds', () => {
	it('keeps a (re)learning card due time, which Anki stores as epoch seconds', () => {
		expect(dueEpochSeconds(1762228377)).toBe(1762228377);
	});

	it('refuses a review-card day number or a new-card position ("20721d ago")', () => {
		expect(dueEpochSeconds(4213)).toBeNull(); // days since collection creation
		expect(dueEpochSeconds(3)).toBeNull(); // new-card queue position
		expect(dueEpochSeconds(0)).toBeNull();
		expect(dueEpochSeconds(undefined)).toBeNull();
	});
});
