import '../../test-setup';
import { App, Notice } from 'obsidian';
import { spawn, exec } from 'child_process';
import { createMockChildProcess } from '../../test-setup';
import { CheckService } from '@application/services/CheckService';

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
				arete_script_path: '',
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

		test('extracts JSON from messy output (fallback)', async () => {
			const mockChild = createMockChildProcess();
			(spawn as jest.Mock).mockReturnValue(mockChild);

			const promise = service.getCheckResult('test.md');
			mockChild.stdout.emit('data', 'DEBUG: noise\n{"issues": ["err"]}\nINFO: noise');
			mockChild.emit('close', 0);

			const result = await promise;
			expect(result).toEqual({ issues: ['err'] });
		});

		test('rejects on invalid JSON', async () => {
			const mockChild = createMockChildProcess();
			(spawn as jest.Mock).mockReturnValue(mockChild);

			const promise = service.getCheckResult('test.md');
			mockChild.stdout.emit('data', 'Invalid output');
			mockChild.emit('close', 0);

			await expect(promise).rejects.toThrow('Parse Error');
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

	test('runFix spawns fix-file command (success)', async () => {
		const mockChild = createMockChildProcess();
		(spawn as jest.Mock).mockReturnValue(mockChild);

		const promise = service.runFix('test.md');
		mockChild.emit('close', 0);
		await promise;

		expect(spawn).toHaveBeenCalledWith(
			'python3',
			expect.arrayContaining(['fix-file', 'test.md']),
			expect.any(Object),
		);
		expect(Notice).toHaveBeenCalledWith('✨ File auto-fixed!');
	});

	test('runFix spawns fix-file command (failure)', async () => {
		const mockChild = createMockChildProcess();
		(spawn as jest.Mock).mockReturnValue(mockChild);

		const promise = service.runFix('test.md');
		mockChild.emit('close', 1);
		await promise;

		expect(Notice).toHaveBeenCalledWith('❌ Fix failed (check console)');
	});

	test('runFix with .py script path', async () => {
		service.settings.arete_script_path = '/path/to/o2a/main.py';
		const mockChild = createMockChildProcess();
		(spawn as jest.Mock).mockReturnValue(mockChild);

		const promise = service.runFix('test.md');
		mockChild.emit('close', 0);
		await promise;

		const spawnCall = (spawn as jest.Mock).mock.calls[0];
		const env = spawnCall[2].env;
		expect(env.PYTHONPATH).toContain('/path/to');
	});

	test('testConfig calls exec (success)', async () => {
		(exec as unknown as jest.Mock).mockImplementation((cmd, opts, cb) => {
			cb(null, 'Arete module found', '');
		});

		await service.testConfig();
		expect(Notice).toHaveBeenCalledWith(expect.stringContaining('Success'));
	});

	test('testConfig calls exec (failure)', async () => {
		(exec as unknown as jest.Mock).mockImplementation((cmd, opts, cb) => {
			cb(new Error('Exec failed'), '', 'Stderr noise');
		});

		await service.testConfig();
		expect(Notice).toHaveBeenCalledWith(
			expect.stringContaining('Error: Command failed. Stderr noise'),
		);
	});

	test('testConfig with empty python path', async () => {
		service.settings.python_path = '';
		await service.testConfig();
		expect(Notice).toHaveBeenCalledWith(
			expect.stringContaining('Error: Python Executable setting is empty'),
		);
	});

	test('checkVaultIntegrity detects missing frontmatter', async () => {
		const file = { path: 'fail.md' };
		(app.vault.getMarkdownFiles as jest.Mock).mockReturnValue([file]);
		(app.vault.read as jest.Mock).mockResolvedValue('---\ntest: true\n---\n');
		(app.metadataCache.getFileCache as jest.Mock).mockReturnValue({}); // No frontmatter

		await service.checkVaultIntegrity();
		expect(Notice).toHaveBeenCalledWith(expect.stringContaining('Found 1 files'));
	});

	test('checkVaultIntegrity passed', async () => {
		const file = { path: 'pass.md' };
		(app.vault.getMarkdownFiles as jest.Mock).mockReturnValue([file]);
		(app.vault.read as jest.Mock).mockResolvedValue('---\ntest: true\n---\n');
		(app.metadataCache.getFileCache as jest.Mock).mockReturnValue({
			frontmatter: { test: true },
		});

		await service.checkVaultIntegrity();
		expect(Notice).toHaveBeenCalledWith(expect.stringContaining('Integrity Check Passed'));
	});
});
