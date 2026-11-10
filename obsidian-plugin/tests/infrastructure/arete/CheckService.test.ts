import '../../test-setup';
import { App, Notice, requestUrl } from 'obsidian';
import { spawn, exec } from 'child_process';
import { createMockChildProcess } from '../../test-setup';
import { CheckService } from '@infrastructure/arete/CheckService';

describe('CheckService', () => {
	let service: CheckService;
	let app: App;
	let plugin: any;

	beforeEach(() => {
		jest.clearAllMocks();
		app = new App();
		plugin = {
			settings: {
				python_path: 'python3',
				project_root: '/mock/project',
			},
		};
		service = new CheckService(app, plugin.settings);
	});

	describe('getCheckResult', () => {
		test('successfully parses JSON output', async () => {
			const mockChild = createMockChildProcess();
			(spawn as jest.Mock).mockReturnValue(mockChild);

			const promise = service.getCheckResult('test.md');
			mockChild.stdout.emit('data', JSON.stringify({ issues: [] }));
			mockChild.emit('close', 0);

			const result = await promise;
			expect(result).toEqual({ issues: [] });
		});

		test('a failed check exits 1 and its JSON is still the result', async () => {
			const mockChild = createMockChildProcess();
			(spawn as jest.Mock).mockReturnValue(mockChild);

			const promise = service.getCheckResult('missing.md');
			mockChild.stdout.emit('data', '{"ok": false, "errors": [{"line": 0}]}');
			mockChild.emit('close', 1);

			await expect(promise).resolves.toEqual({ ok: false, errors: [{ line: 0 }] });
		});

		test('rejects output with anything around the JSON, quoting it', async () => {
			jest.spyOn(console, 'error').mockImplementation(() => undefined);
			const mockChild = createMockChildProcess();
			(spawn as jest.Mock).mockReturnValue(mockChild);

			const promise = service.getCheckResult('test.md');
			mockChild.stdout.emit('data', 'DEBUG: noise\n{"issues": ["err"]}\nINFO: noise');
			mockChild.emit('close', 0);

			await expect(promise).rejects.toThrow(
				'arete vault check printed something other than JSON: "DEBUG: noise',
			);
		});

		test('rejects on spawn error', async () => {
			const mockChild = createMockChildProcess();
			(spawn as jest.Mock).mockReturnValue(mockChild);

			const promise = service.getCheckResult('test.md');
			mockChild.emit('error', new Error('Spawn failed'));

			await expect(promise).rejects.toThrow('Spawn failed');
		});
	});

	// runCheck() moved to main.ts (DDD refactor)

	test('runFix spawns vault fix command (success)', async () => {
		const mockChild = createMockChildProcess();
		(spawn as jest.Mock).mockReturnValue(mockChild);

		const promise = service.runFix('test.md');
		mockChild.emit('close', 0);
		await promise;

		expect(spawn).toHaveBeenCalledWith(
			'python3',
			expect.arrayContaining(['vault', 'fix', 'test.md']),
			expect.any(Object),
		);
		expect(Notice).toHaveBeenCalledWith('File auto-fixed.');
	});

	test('runFix spawns vault fix command (failure)', async () => {
		const mockChild = createMockChildProcess();
		(spawn as jest.Mock).mockReturnValue(mockChild);

		const promise = service.runFix('test.md');
		mockChild.emit('close', 1);
		await promise;

		expect(Notice).toHaveBeenCalledWith('Fix failed, check the developer console.');
	});

	test('testConfig calls exec (success)', async () => {
		(exec as unknown as jest.Mock).mockImplementation((cmd, opts, cb) => {
			cb(null, 'Arete module found', '');
		});

		await service.testConfig();
		expect(Notice).toHaveBeenCalledWith(expect.stringContaining('Python found'));
	});

	test('testConfig calls exec (failure)', async () => {
		(exec as unknown as jest.Mock).mockImplementation((cmd, opts, cb) => {
			cb(new Error('Exec failed'), '', 'Stderr noise');
		});

		await service.testConfig();
		expect(Notice).toHaveBeenCalledWith(
			expect.stringContaining('Command failed: Stderr noise'),
		);
	});

	test('testConfig with empty python path', async () => {
		service.settings.python_path = '';
		await service.testConfig();
		expect(Notice).toHaveBeenCalledWith(
			expect.stringContaining('The Python executable setting is empty'),
		);
	});

	describe('testAnkiConnect', () => {
		beforeEach(() => {
			service.settings.anki_connect_url = 'http://localhost:8765';
		});

		test('reports the version AnkiConnect answers with', async () => {
			(requestUrl as jest.Mock).mockResolvedValue({ json: { result: 6 } });
			await service.testAnkiConnect();
			expect(requestUrl).toHaveBeenCalledWith(
				expect.objectContaining({ url: 'http://localhost:8765', method: 'POST' }),
			);
			expect(Notice).toHaveBeenCalledWith('AnkiConnect v6 is connected.', 3000);
		});

		test('says so when the answer carries no version', async () => {
			(requestUrl as jest.Mock).mockResolvedValue({ json: {} });
			await service.testAnkiConnect();
			expect(Notice).toHaveBeenCalledWith(
				'AnkiConnect responded, but its version is unknown.',
				5000,
			);
		});

		test('says so when nothing answers, without an emoji prefix', async () => {
			jest.spyOn(console, 'error').mockImplementation(() => undefined);
			(requestUrl as jest.Mock).mockRejectedValue(new Error('refused'));
			await service.testAnkiConnect();
			expect(Notice).toHaveBeenCalledWith(
				'Cannot connect to AnkiConnect, check that Anki is running.',
				8000,
			);
		});
	});
});
