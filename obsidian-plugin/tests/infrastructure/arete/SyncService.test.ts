import '../../test-setup';
import { App, Notice } from 'obsidian';
import { spawn } from 'child_process';
import * as fs from 'fs';
import { createMockChildProcess } from '../../test-setup';
import {
	isSyncLockHeld,
	LOG_KEEP_BYTES,
	LOG_MAX_BYTES,
	SYNC_LOCK_EXIT_CODE,
	SyncLog,
	SyncService,
} from '@infrastructure/arete/SyncService';
import { AretePluginSettings } from '@domain/settings';

// The shared setup mocks fs with sync functions only; the sync log uses fs.promises.
jest.mock('fs', () => ({
	appendFileSync: jest.fn(),
	readFileSync: jest.fn(),
	writeFileSync: jest.fn(),
	existsSync: jest.fn(),
	promises: {
		appendFile: jest.fn(),
		stat: jest.fn(),
		readFile: jest.fn(),
		writeFile: jest.fn(),
	},
}));
const fsp = fs.promises as unknown as Record<string, jest.Mock>;

/** Let pending promise callbacks (the log's async writes) run. */
const settle = () => new Promise((r) => setImmediate(r));

describe('SyncService', () => {
	let service: SyncService;
	let app: App;
	let settings: AretePluginSettings;
	let updateStatusBar: jest.Mock;

	beforeEach(() => {
		jest.clearAllMocks();
		app = new App();
		(app.vault.adapter as any).getBasePath = jest.fn().mockReturnValue('/mock/vault/path');

		settings = {
			python_path: 'python3',
			debug_mode: false,
			backend: 'auto',
			workers: 4,
			anki_connect_url: 'http://localhost:8765',
			anki_media_dir: '',
			renderer_mode: 'obsidian',
			stats_algorithm: 'sm2',
			stats_lapse_threshold: 3,
			stats_ease_threshold: 2100,
			stats_difficulty_threshold: 0.9,
			graph_coloring_enabled: false,
			graph_tag_prefix: 'arete/retention',
			sync_on_save: false,
			sync_on_save_delay: 2000,
			ui_expanded_decks: [],
			ui_expanded_concepts: [],
			last_sync_time: null,
			project_root: '',
		};

		updateStatusBar = jest.fn();
		fsp.appendFile.mockResolvedValue(undefined);
		fsp.writeFile.mockResolvedValue(undefined);
		fsp.stat.mockRejectedValue(Object.assign(new Error('ENOENT'), { code: 'ENOENT' }));

		service = new SyncService(app, settings, { dir: 'test-plugin-dir' });
	});

	test('runSync for a specific file', async () => {
		const mockChild = createMockChildProcess();
		(spawn as jest.Mock).mockReturnValue(mockChild);

		const syncPromise = service.runSync(false, '/mock/path/file.md', true, updateStatusBar);
		mockChild.emit('close', 0);
		await syncPromise;

		expect(spawn).toHaveBeenCalledWith(
			'python3',
			expect.arrayContaining(['sync', '--force', '--clear-cache', '/mock/path/file.md']),
			expect.any(Object),
		);
		expect(updateStatusBar).toHaveBeenCalledWith('syncing');
		expect(updateStatusBar).toHaveBeenCalledWith('success');
	});

	test('runSync with all flags and custom settings', async () => {
		service.settings.anki_connect_url = 'http://anki:8765';
		service.settings.anki_media_dir = '/anki/media';
		service.settings.backend = 'direct';
		const mockChild = createMockChildProcess();
		(spawn as jest.Mock).mockReturnValue(mockChild);

		const syncPromise = service.runSync(true, null, true, updateStatusBar);
		mockChild.emit('close', 0);
		await syncPromise;

		expect(spawn).toHaveBeenCalledWith(
			'python3',
			expect.arrayContaining([
				'--prune',
				'--force',
				'--backend',
				'direct',
				'--anki-connect-url',
				'http://anki:8765',
				'--anki-media-dir',
				'/anki/media',
			]),
			expect.any(Object),
		);
	});

	test('dispose kills a sync in flight; the stopped sync shows no notice', async () => {
		const mockChild = createMockChildProcess();
		(spawn as jest.Mock).mockReturnValue(mockChild);

		const syncPromise = service.runSync(false, null, false, updateStatusBar);
		(Notice as unknown as jest.Mock).mockClear();
		updateStatusBar.mockClear();
		service.dispose();
		mockChild.emit('close', null, 'SIGTERM');
		await syncPromise;

		expect(mockChild.kill).toHaveBeenCalledTimes(1);
		expect(Notice).not.toHaveBeenCalled();
		expect(updateStatusBar).not.toHaveBeenCalled();
	});

	test('runSync resolves once the process has exited, with how it ended', async () => {
		const mockChild = createMockChildProcess();
		(spawn as jest.Mock).mockReturnValue(mockChild);
		let outcome: string | undefined;

		const run = service.runSync(false, null, false, updateStatusBar).then((o) => (outcome = o));
		await settle();
		expect(outcome).toBeUndefined();
		mockChild.emit('close', 0);
		await run;

		expect(outcome).toBe('success');
	});

	test('a sync stopped by dispose shows nothing, also when uv exits with a code', async () => {
		const mockChild = createMockChildProcess();
		(spawn as jest.Mock).mockReturnValue(mockChild);
		const run = service.runSync(false, null, false, updateStatusBar);
		(Notice as unknown as jest.Mock).mockClear();
		updateStatusBar.mockClear();

		service.dispose();
		mockChild.emit('close', 143, null);

		expect(await run).toBe('stopped');
		expect(Notice).not.toHaveBeenCalled();
		expect(updateStatusBar).not.toHaveBeenCalled();
	});

	describe('the JSON report and prune', () => {
		const report = (prune: unknown = null, over: Record<string, unknown> = {}) =>
			JSON.stringify({
				ok: true,
				generated: 3,
				changed: 2,
				errors: 0,
				message: null,
				prune,
				...over,
			});
		const preview = {
			status: 'unconfirmed',
			message: 'Prune not confirmed, so nothing was deleted.',
			notes: [{ nid: 7, arete_id: 'arete_X', deck: 'Old' }],
			decks: ['Old'],
			note_count: 1,
			deck_count: 1,
		};
		const deleted = { ...preview, status: 'deleted', message: 'Deleted 1 notes and 1 decks.' };

		/** Each spawn prints the next stdout and exits with the next code. */
		function scriptedRuns(...runs: [string, number][]) {
			(spawn as jest.Mock).mockImplementation(() => {
				const child = createMockChildProcess();
				const [stdout, code] = runs.shift()!;
				setImmediate(() => {
					child.stdout.emit('data', Buffer.from(stdout));
					child.emit('close', code);
				});
				return child;
			});
		}
		const argvs = () => (spawn as jest.Mock).mock.calls.map((c) => c[1] as string[]);
		const notices = () => (Notice as unknown as jest.Mock).mock.calls.map((c) => c[0]);
		const lastNotice = () => notices()[notices().length - 1];

		test('every sync asks for --json and reports cards changed from it', async () => {
			scriptedRuns([report(), 0]);
			expect(await service.runSync(false, null, false, updateStatusBar)).toBe('success');
			expect(argvs()[0]).toEqual(expect.arrayContaining(['sync', '--json']));
			expect(notices()).toContain('Sync completed, 2 cards changed in Anki.');
		});

		test('prune runs without --yes, shows the preview, and deletes only after a yes', async () => {
			scriptedRuns([report(preview), 0], [report(deleted, { changed: 0 }), 0]);
			const confirm = jest.fn().mockResolvedValue(true);

			const outcome = await service.runSync(true, null, false, updateStatusBar, confirm);

			expect(outcome).toBe('success');
			expect(confirm).toHaveBeenCalledWith(expect.objectContaining({ note_count: 1 }));
			expect(argvs()).toHaveLength(2);
			expect(argvs()[0]).toContain('--prune');
			expect(argvs()[0]).not.toContain('--yes');
			expect(argvs()[1]).toEqual(expect.arrayContaining(['--prune', '--yes', '--json']));
			expect(lastNotice()).toBe(
				'Sync completed, Anki was already up to date. Deleted 1 note and 1 deck from Anki.',
			);
		});

		test('cancelling the preview deletes nothing and says so', async () => {
			scriptedRuns([report(preview), 0]);
			const confirm = jest.fn().mockResolvedValue(false);

			await service.runSync(true, null, false, updateStatusBar, confirm);

			expect(argvs()).toHaveLength(1);
			expect(lastNotice()).toBe('Prune cancelled, nothing was deleted.');
		});

		test('without a way to ask, prune deletes nothing', async () => {
			scriptedRuns([report(preview), 0]);
			await service.runSync(true, null, false, updateStatusBar);
			expect(argvs()).toHaveLength(1);
			expect(argvs()[0]).not.toContain('--yes');
		});

		test('nothing to prune asks nothing and says so', async () => {
			scriptedRuns([report({ ...preview, status: 'clean', notes: [], decks: [] }), 0]);
			const confirm = jest.fn();
			await service.runSync(true, null, false, updateStatusBar, confirm);
			expect(confirm).not.toHaveBeenCalled();
			expect(lastNotice()).toBe('Sync completed, 2 cards changed in Anki. Nothing to prune.');
		});

		test('a sync that failed is not offered for deletion', async () => {
			scriptedRuns([report(preview, { ok: false, errors: 2, message: '2 errors' }), 1]);
			const confirm = jest.fn();
			expect(await service.runSync(true, null, false, updateStatusBar, confirm)).toBe(
				'failed',
			);
			expect(confirm).not.toHaveBeenCalled();
			expect(lastNotice()).toMatch(/Nothing was deleted, because the sync did not complete/);
		});

		test('a prune status the plugin does not know is shown as not deleted', async () => {
			const odd = { ...preview, status: 'held_back', message: 'Some cards failed.' };
			scriptedRuns([report(odd), 0]);
			await service.runSync(true, null, false, updateStatusBar, jest.fn());
			expect(lastNotice()).toMatch(/Nothing was deleted\. Some cards failed\.$/);
		});

		test('stdout that is not JSON falls back to the exit code', async () => {
			scriptedRuns(['INFO: done', 0]);
			expect(await service.runSync(false, null, false, updateStatusBar)).toBe('success');
			expect(notices()).toContain('Sync completed.');
		});
	});

	describe('another sync holds the lock', () => {
		test('the lock exit is reported as a running sync, not as a failure', async () => {
			const mockChild = createMockChildProcess();
			(spawn as jest.Mock).mockReturnValue(mockChild);
			const run = service.runSync(false, null, false, updateStatusBar);

			mockChild.stderr.emit('data', 'Another sync is running (pid 123); try again later.\n');
			mockChild.emit('close', SYNC_LOCK_EXIT_CODE);

			expect(await run).toBe('busy');
			expect(Notice).toHaveBeenLastCalledWith('A sync is already running.');
			expect(updateStatusBar).not.toHaveBeenCalledWith('error');
			expect(updateStatusBar).toHaveBeenLastCalledWith('idle');
		});

		test('isSyncLockHeld needs both the exit code and the message', () => {
			expect(isSyncLockHeld(75, 'ERROR: another sync is running')).toBe(true);
			expect(isSyncLockHeld(75, 'something else')).toBe(false);
			expect(isSyncLockHeld(1, 'another sync is running')).toBe(false);
		});
	});

	describe('runSync - error cases', () => {
		beforeEach(() => {
			jest.spyOn(console, 'error').mockImplementation(() => {
				/* ignore console errors in tests */
			});
		});

		afterEach(() => {
			(console.error as jest.Mock).mockRestore();
		});

		test('spawn error', async () => {
			const mockChild = createMockChildProcess();
			(spawn as jest.Mock).mockReturnValue(mockChild);

			const syncPromise = service.runSync(false, null, false, updateStatusBar);

			mockChild.emit('error', new Error('Failed to start'));
			await syncPromise;

			expect(updateStatusBar).toHaveBeenCalledWith('error', 'Failed to start');
		});

		test('exit with error code and specific message (Anki unreachable)', async () => {
			const mockChild = createMockChildProcess();
			(spawn as jest.Mock).mockReturnValue(mockChild);

			const syncPromise = service.runSync(false, null, false, updateStatusBar);

			mockChild.stderr.emit('data', 'AnkiConnect call failed');
			mockChild.emit('close', 1);
			await syncPromise;

			expect(updateStatusBar).toHaveBeenCalledWith('error');
			expect(Notice).toHaveBeenCalledWith(expect.stringContaining('Anki is not reachable'));
		});

		test('handling ModuleNotFoundError in CLI', async () => {
			const mockChild = createMockChildProcess();
			(spawn as jest.Mock).mockReturnValue(mockChild);

			const syncPromise = service.runSync(false, null, false, updateStatusBar);

			mockChild.stderr.emit('data', 'ModuleNotFoundError: No module named arete');
			mockChild.emit('close', 1);
			await syncPromise;

			expect(updateStatusBar).toHaveBeenCalledWith('error');
			expect(Notice).toHaveBeenCalledWith(
				expect.stringContaining('Python dependencies are missing'),
			);
		});

		test('handling No module named arete in CLI', async () => {
			const mockChild = createMockChildProcess();
			(spawn as jest.Mock).mockReturnValue(mockChild);

			const syncPromise = service.runSync(false, null, false, updateStatusBar);

			mockChild.stderr.emit('data', 'No module named arete');
			mockChild.emit('close', 1);
			await syncPromise;

			expect(updateStatusBar).toHaveBeenCalledWith('error');
			expect(Notice).toHaveBeenCalledWith(
				expect.stringContaining('Invalid Python environment'),
			);
		});

		test('general sync failure with log instruction', async () => {
			const mockChild = createMockChildProcess();
			(spawn as jest.Mock).mockReturnValue(mockChild);

			const syncPromise = service.runSync(false, null, false, updateStatusBar);

			mockChild.stderr.emit('data', 'Unknown error occurred');
			mockChild.emit('close', 1);
			await syncPromise;

			expect(updateStatusBar).toHaveBeenCalledWith('error');
			expect(Notice).toHaveBeenCalledWith(
				expect.stringContaining('Sync failed with exit code'),
			);
		});

		test('handles stdout data logging', async () => {
			const mockChild = createMockChildProcess();
			(spawn as jest.Mock).mockReturnValue(mockChild);
			const syncPromise = service.runSync(false, null, false, updateStatusBar);

			mockChild.stdout.emit('data', Buffer.from('Syncing note 1\nSyncing note 2'));
			mockChild.emit('close', 0);
			await syncPromise;
			// Coverage for stdout handler (implicitly through execution)
		});

		test('missing vault path (getBasePath undefined)', async () => {
			(app.vault.adapter as any).getBasePath = undefined;
			await service.runSync(false, null, false, updateStatusBar);
			expect(updateStatusBar).toHaveBeenCalledWith('error', 'No Vault Path');
		});

		test('exception during spawn catch block', async () => {
			(spawn as jest.Mock).mockImplementation(() => {
				throw new Error('Spawn failed immediately');
			});
			await service.runSync(false, null, false, updateStatusBar);
			expect(updateStatusBar).toHaveBeenCalledWith('error', 'Spawn failed immediately');
		});

		test('runSync with debug mode (verbose)', async () => {
			service.settings.debug_mode = true;
			const mockChild = createMockChildProcess();
			(spawn as jest.Mock).mockReturnValue(mockChild);

			const promise = service.runSync(false, null, false, updateStatusBar);
			mockChild.emit('close', 0);
			await promise;

			expect(spawn).toHaveBeenCalledWith(
				'python3',
				expect.arrayContaining(['--verbose']),
				expect.any(Object),
			);
		});

		test('log writes failure (catch block)', async () => {
			fsp.appendFile.mockRejectedValue(new Error('Disk full'));

			const mockChild = createMockChildProcess();
			(spawn as jest.Mock).mockReturnValue(mockChild);
			const promise = service.runSync(false, null, false, updateStatusBar);
			mockChild.emit('close', 0);
			await promise;
			await settle();

			expect(console.error).toHaveBeenCalledWith(
				expect.stringContaining('Failed to write to log file'),
				expect.any(Error),
			);
		});
	});

	describe('CLI sync log', () => {
		const logPath = '/mock/vault/path/test-plugin-dir/arete_plugin.log';

		test('many output lines become one async append, never a sync write', async () => {
			jest.spyOn(console, 'log').mockImplementation(() => undefined);
			const mockChild = createMockChildProcess();
			(spawn as jest.Mock).mockReturnValue(mockChild);
			const run = service.runSync(false, null, false, updateStatusBar);

			const lines = Array.from({ length: 500 }, (_, i) => `note ${i}`).join('\n');
			mockChild.stdout.emit('data', Buffer.from(lines));
			mockChild.emit('close', 0);
			await run;
			await settle();

			expect(fs.appendFileSync).not.toHaveBeenCalled();
			expect(fsp.appendFile).toHaveBeenCalledTimes(1);
			const [pathArg, chunk] = fsp.appendFile.mock.calls[0];
			expect(pathArg).toBe(logPath);
			expect(chunk).toContain('STDOUT: note 0\n');
			expect(chunk).toContain('STDOUT: note 499\n');
		});

		test('the tail of a failed run is on disk once the process closes', async () => {
			const mockChild = createMockChildProcess();
			(spawn as jest.Mock).mockReturnValue(mockChild);
			jest.spyOn(console, 'error').mockImplementation(() => undefined);
			const run = service.runSync(false, null, false, updateStatusBar);

			mockChild.stderr.emit('data', 'Traceback: boom');
			mockChild.emit('close', 1);
			await run;
			await settle();

			const written = fsp.appendFile.mock.calls.map((c) => c[1]).join('');
			expect(written).toContain('STDERR: Traceback: boom');
			expect(written).toContain('Process exited with code 1');
		});

		test('a spawn error is written too', async () => {
			const mockChild = createMockChildProcess();
			(spawn as jest.Mock).mockReturnValue(mockChild);
			const run = service.runSync(false, null, false, updateStatusBar);

			mockChild.emit('error', new Error('ENOENT python3'));
			await run;
			await settle();

			const written = fsp.appendFile.mock.calls.map((c) => c[1]).join('');
			expect(written).toContain('Process Error: ENOENT python3');
		});

		test('an oversized log is cut to its tail, at a line start, before the run appends', async () => {
			const lineCount = Math.ceil(LOG_MAX_BYTES / 20) + 1;
			const old = Buffer.from(
				Array.from({ length: lineCount }, (_, i) => `old line ${i}`.padEnd(19) + '\n').join(
					'',
				),
			);
			fsp.stat.mockResolvedValue({ size: old.length });
			fsp.readFile.mockResolvedValue(old);

			const log = new SyncLog(logPath);
			log.write('new run');
			await log.flush();

			const kept: Buffer = fsp.writeFile.mock.calls[0][1];
			expect(kept.length).toBeLessThanOrEqual(LOG_KEEP_BYTES);
			expect(kept.length).toBeGreaterThan(LOG_KEEP_BYTES - 20);
			expect(kept.toString()).toMatch(/^old line \d+ +\n/);
			expect(kept.toString().endsWith(`old line ${lineCount - 1}`.padEnd(19) + '\n')).toBe(
				true,
			);
			expect(fsp.writeFile.mock.invocationCallOrder[0]).toBeLessThan(
				fsp.appendFile.mock.invocationCallOrder[0],
			);
		});

		test('a log under the limit is left alone', async () => {
			fsp.stat.mockResolvedValue({ size: 10 });
			const log = new SyncLog(logPath);
			log.write('line');
			await log.flush();
			expect(fsp.readFile).not.toHaveBeenCalled();
			expect(fsp.writeFile).not.toHaveBeenCalled();
		});
	});
});
