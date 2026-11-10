import '../../test-setup';
import { App, Notice, requestUrl } from 'obsidian';
import { spawn } from 'child_process';
import * as fs from 'fs';
import { createMockChildProcess } from '../../test-setup';
import {
	LOG_KEEP_BYTES,
	LOG_MAX_BYTES,
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
			execution_mode: 'cli',
			server_port: 8777,
			project_root: '',
			server_reload: false,
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

	describe('runSyncServer', () => {
		beforeEach(() => {
			service.settings.execution_mode = 'server';
		});

		test('successful server sync', async () => {
			(requestUrl as jest.Mock).mockResolvedValue({
				status: 200,
				json: { success: true, total_imported: 5 },
			});

			await service.runSync(false, null, false, updateStatusBar);

			expect(updateStatusBar).toHaveBeenCalledWith('success');
			expect(requestUrl).toHaveBeenCalledWith(
				expect.objectContaining({
					method: 'POST',
					url: 'http://127.0.0.1:8777/sync',
				}),
			);
		});

		test('an unconfirmed prune says what it found and that nothing was deleted', async () => {
			(requestUrl as jest.Mock).mockResolvedValue({
				status: 200,
				json: {
					success: true,
					total_imported: 0,
					prune: { status: 'unconfirmed', note_count: 3, deck_count: 1 },
				},
			});

			await service.runSync(true, null, false, updateStatusBar);

			expect(Notice).toHaveBeenCalledWith(
				expect.stringContaining(
					'3 Arete notes and 1 decks would be deleted. Nothing was deleted',
				),
				15000,
			);
		});

		test('server sync with errors', async () => {
			(requestUrl as jest.Mock).mockResolvedValue({
				status: 200,
				json: { success: false, total_errors: 3 },
			});

			await service.runSync(false, null, false, updateStatusBar);
			expect(updateStatusBar).toHaveBeenCalledWith('error', 'Sync Errors');
		});

		test('server connection refused', async () => {
			(requestUrl as jest.Mock).mockRejectedValue(new Error('Connection refused'));

			await service.runSync(false, null, false, updateStatusBar);
			expect(updateStatusBar).toHaveBeenCalledWith('error', 'Connection Failed');
		});

		test('server non-200 response', async () => {
			(requestUrl as jest.Mock).mockResolvedValue({ status: 500 });
			await service.runSync(false, null, false, updateStatusBar);
			expect(updateStatusBar).toHaveBeenCalledWith('error', 'HTTP 500');
		});
	});

	describe('runSyncCLI - error cases', () => {
		beforeEach(() => {
			service.settings.execution_mode = 'cli';
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

		test('runSyncServer generic error handling', async () => {
			service.settings.execution_mode = 'server';
			(requestUrl as jest.Mock).mockRejectedValue(new Error('Random sync error'));

			await service.runSync(false, null, false, updateStatusBar);
			expect(updateStatusBar).toHaveBeenCalledWith('error', 'Connection Failed');
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
			await service.runSync(false, null, false, updateStatusBar);

			const lines = Array.from({ length: 500 }, (_, i) => `note ${i}`).join('\n');
			mockChild.stdout.emit('data', Buffer.from(lines));
			mockChild.emit('close', 0);
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
			await service.runSync(false, null, false, updateStatusBar);

			mockChild.stderr.emit('data', 'Traceback: boom');
			mockChild.emit('close', 1);
			await settle();

			const written = fsp.appendFile.mock.calls.map((c) => c[1]).join('');
			expect(written).toContain('STDERR: Traceback: boom');
			expect(written).toContain('Process exited with code 1');
		});

		test('a spawn error is written too', async () => {
			const mockChild = createMockChildProcess();
			(spawn as jest.Mock).mockReturnValue(mockChild);
			await service.runSync(false, null, false, updateStatusBar);

			mockChild.emit('error', new Error('ENOENT python3'));
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
