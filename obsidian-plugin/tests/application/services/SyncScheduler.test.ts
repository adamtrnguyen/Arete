import { SyncRequest, SyncScheduler } from '@application/services/SyncScheduler';

/** A host whose syncs finish only when the test says so. */
function makeHost() {
	const started: SyncRequest[] = [];
	const finishers: (() => void)[] = [];
	const host = {
		run: jest.fn((request: SyncRequest) => {
			started.push(request);
			return new Promise<void>((resolve) => finishers.push(resolve));
		}),
		busy: jest.fn(),
		queueChanged: jest.fn(),
	};
	/** Finish the oldest running sync and let the scheduler react. */
	const finish = async () => {
		finishers.shift()!();
		await new Promise((r) => setImmediate(r));
	};
	return { host, started, finish };
}

const vault: SyncRequest = { prune: false, targetPath: null, force: false };

describe('SyncScheduler', () => {
	test('a manual sync while one runs starts nothing and says a sync is running', async () => {
		const { host, started, finish } = makeHost();
		const scheduler = new SyncScheduler(host);

		const first = scheduler.requestManual(vault);
		const second = await scheduler.requestManual({ ...vault, prune: true });

		expect(second).toBe(false);
		expect(host.busy).toHaveBeenCalledTimes(1);
		expect(started).toEqual([vault]);
		await finish();
		expect(await first).toBe(true);
		expect(scheduler.isRunning).toBe(false);
		expect(started).toHaveLength(1);
	});

	test('any number of saves of one file during a sync become one follow-up for it', async () => {
		const { host, started, finish } = makeHost();
		const scheduler = new SyncScheduler(host);

		void scheduler.requestManual(vault);
		for (let i = 0; i < 5; i++) void scheduler.requestSave('/v/A.md');
		expect(scheduler.isQueued).toBe(true);
		expect(started).toHaveLength(1);

		await finish();
		expect(started).toEqual([vault, { prune: false, targetPath: '/v/A.md', force: false }]);
		expect(scheduler.isQueued).toBe(false);
		await finish();
		expect(started).toHaveLength(2);
		expect(scheduler.isRunning).toBe(false);
	});

	test('saves of different files during a sync become one vault sync, so none is dropped', async () => {
		const { host, started, finish } = makeHost();
		const scheduler = new SyncScheduler(host);

		void scheduler.requestSave('/v/A.md');
		void scheduler.requestSave('/v/B.md');
		void scheduler.requestSave('/v/C.md');
		await finish();

		expect(started).toEqual([
			{ prune: false, targetPath: '/v/A.md', force: false },
			{ prune: false, targetPath: null, force: false },
		]);
	});

	test('a save while idle syncs that file at once', async () => {
		const { host, started, finish } = makeHost();
		const scheduler = new SyncScheduler(host);
		const done = scheduler.requestSave('/v/A.md');
		expect(started).toEqual([{ prune: false, targetPath: '/v/A.md', force: false }]);
		await finish();
		await done;
		expect(host.busy).not.toHaveBeenCalled();
	});

	test('the queue change is reported when a follow-up is queued and when it starts', async () => {
		const { host, finish } = makeHost();
		const scheduler = new SyncScheduler(host);
		void scheduler.requestManual(vault);
		void scheduler.requestSave('/v/A.md');
		expect(host.queueChanged).toHaveBeenCalledTimes(1);
		await finish();
		expect(host.queueChanged).toHaveBeenCalledTimes(2);
	});

	test('a sync that throws still frees the scheduler and runs the follow-up', async () => {
		jest.spyOn(console, 'error').mockImplementation(() => undefined);
		const runs: string[] = [];
		const scheduler = new SyncScheduler({
			run: jest.fn(async (r: SyncRequest) => {
				runs.push(r.targetPath ?? 'vault');
				if (runs.length === 1) throw new Error('boom');
			}),
			busy: jest.fn(),
			queueChanged: jest.fn(),
		});
		const first = scheduler.requestManual(vault);
		void scheduler.requestSave('/v/A.md');
		await first;
		expect(runs).toEqual(['vault', '/v/A.md']);
		expect(scheduler.isRunning).toBe(false);
	});

	test('dispose drops the follow-up and refuses new requests', async () => {
		const { host, started, finish } = makeHost();
		const scheduler = new SyncScheduler(host);
		void scheduler.requestManual(vault);
		void scheduler.requestSave('/v/A.md');

		scheduler.dispose();
		await finish();

		expect(started).toHaveLength(1);
		expect(await scheduler.requestManual(vault)).toBe(false);
		await scheduler.requestSave('/v/B.md');
		expect(started).toHaveLength(1);
		expect(host.busy).not.toHaveBeenCalled();
	});
});
