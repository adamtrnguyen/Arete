import '../../test-setup';
import { App, Notice, requestUrl } from 'obsidian';
import { spawn } from 'child_process';
import * as fs from 'fs';
import { createMockChildProcess } from '../../test-setup';
import { SyncService } from '@application/services/SyncService';
import { AretePluginSettings } from '@domain/settings';
import * as path from 'path';

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
			arete_script_path: '',
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
		service.settings.backend = 'apy';
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
				'apy',
				'--anki-connect-url',
				'http://anki:8765',
				'--anki-media-dir',
				'/anki/media',
			]),
			expect.any(Object),
		);
	});

	test('runSync with .py script path', async () => {
		service.settings.arete_script_path = '/path/to/o2a/main.py';
		const mockChild = createMockChildProcess();
		(spawn as jest.Mock).mockReturnValue(mockChild);

		const syncPromise = service.runSync(false, null, false, updateStatusBar);
		mockChild.emit('close', 0);
		await syncPromise;

		expect(spawn).toHaveBeenCalledWith(
			'python3',
			expect.arrayContaining(['-m', 'arete', 'sync', '--workers', '4', '/mock/vault/path']),
			expect.any(Object),
		);
		const spawnCall = (spawn as jest.Mock).mock.calls[0];
		const env = spawnCall[2].env;
		expect(env.PYTHONPATH).toContain('/path/to');
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
				expect.stringContaining('Python Dependencies missing'),
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
			expect(Notice).toHaveBeenCalledWith(expect.stringContaining('arete sync failed'));
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
			// fs is require'd inside the function, so we mock it on the globally available mock if possible,
			// or just let it fail by giving it an invalid path (it's already handled by the catch)
			// Actually, we can use the 'test-setup' to mock fs.
			// fs is imported at top
			jest.spyOn(fs, 'appendFileSync').mockImplementation(() => {
				throw new Error('Disk full');
			});

			const mockChild = createMockChildProcess();
			(spawn as jest.Mock).mockReturnValue(mockChild);
			const promise = service.runSync(false, null, false, updateStatusBar);
			mockChild.emit('close', 0);
			await promise;

			expect(console.error).toHaveBeenCalledWith(
				expect.stringContaining('Failed to write to log file'),
				expect.any(Error),
			);
		});
	});
});
