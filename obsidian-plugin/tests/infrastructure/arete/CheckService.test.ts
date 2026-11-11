import '../../test-setup';
import { App, Notice, requestUrl } from 'obsidian';
import { spawn, exec } from 'child_process';
import { createMockChildProcess, onLinux } from '../../test-setup';

onLinux();
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

	test('dispose kills a check in flight', async () => {
		const mockChild = createMockChildProcess();
		(spawn as jest.Mock).mockReturnValue(mockChild);
		jest.spyOn(console, 'error').mockImplementation(() => undefined);

		const promise = service.getCheckResult('test.md');
		service.dispose();
		mockChild.emit('close', null, 'SIGTERM');

		expect(mockChild.kill).toHaveBeenCalledTimes(1);
		await expect(promise).rejects.toThrow(
			'arete vault check printed something other than JSON',
		);
	});

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

	test('testConfig runs the resolved command with --help, without a shell', async () => {
		const child = createMockChildProcess();
		(spawn as jest.Mock).mockReturnValue(child);

		const done = service.testConfig();
		child.emit('close', 0, null);
		await done;

		expect(spawn).toHaveBeenCalledWith('python3', ['-m', 'arete', '--help'], expect.anything());
		expect(exec).not.toHaveBeenCalled();
		expect(Notice).toHaveBeenCalledWith('Arete runs: python3 -m arete');
	});

	test('testConfig with the arete command runs arete --help', async () => {
		service.settings.python_path = 'arete';
		const child = createMockChildProcess();
		(spawn as jest.Mock).mockReturnValue(child);

		const done = service.testConfig();
		child.emit('close', 0, null);
		await done;

		expect(spawn).toHaveBeenCalledWith('arete', ['--help'], expect.anything());
	});

	test('testConfig reports what the command printed on failure', async () => {
		const child = createMockChildProcess();
		(spawn as jest.Mock).mockReturnValue(child);

		const done = service.testConfig();
		child.stderr.emit('data', 'No module named arete');
		child.emit('close', 1, null);
		await done;

		expect(Notice).toHaveBeenCalledWith('Command failed: No module named arete');
	});

	test('testConfig names a command that is not installed', async () => {
		const child = createMockChildProcess();
		(spawn as jest.Mock).mockReturnValue(child);

		const done = service.testConfig();
		child.emit('error', Object.assign(new Error('spawn python3 ENOENT'), { code: 'ENOENT' }));
		await done;

		expect(Notice).toHaveBeenCalledWith('Command failed: python3 was not found.');
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
