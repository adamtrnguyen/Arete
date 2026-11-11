import '../../test-setup';
import { AreteProcesses, resolvePythonCommand } from '@infrastructure/arete/PythonProcess';
import * as path from 'path';
import { spawn } from 'child_process';
import { createMockChildProcess, onLinux } from '../../test-setup';

onLinux();

describe('resolvePythonCommand', () => {
	const baseSettings: any = {
		python_path: 'python3',
		project_root: '',
	};

	test('default settings produce python3 -m arete', () => {
		const result = resolvePythonCommand(baseSettings);
		expect(result.cmd).toBe('python3');
		expect(result.args).toContain('-m');
		expect(result.args).toContain('arete');
		expect(result.cwd).toBe('.');
	});

	test('the installed console script is used as-is, not as a module', () => {
		// Setting python_path to "arete" is how a user stops depending on a checked-out
		// repository. Appending "-m arete" to it produced "arete -m arete", which fails.
		const result = resolvePythonCommand({ ...baseSettings, python_path: 'arete' });
		expect(result.cmd).toBe('arete');
		expect(result.args).toEqual([]);
	});

	test('multi-word python_path is split correctly', () => {
		const settings = { ...baseSettings, python_path: 'uv run python' };
		const result = resolvePythonCommand(settings);
		expect(result.cmd).toBe('uv');
		expect(result.args[0]).toBe('run');
		expect(result.args[1]).toBe('python');
		expect(result.args).toContain('-m');
		expect(result.args).toContain('arete');
	});

	test('-m arete is not duplicated when already present in python_path', () => {
		const settings = { ...baseSettings, python_path: 'python3 -m arete' };
		const result = resolvePythonCommand(settings);
		const mCount = result.args.filter((a) => a === '-m').length;
		const areteCount = result.args.filter((a) => a === 'arete').length;
		// Should have exactly one -m and one arete from the original split
		expect(mCount).toBe(1);
		expect(areteCount).toBe(1);
	});

	test('dedup when args contain arete (case-insensitive)', () => {
		const settings = { ...baseSettings, python_path: 'python3 Arete' };
		const result = resolvePythonCommand(settings);
		// Should not add another -m arete
		const mCount = result.args.filter((a) => a === '-m').length;
		expect(mCount).toBe(0);
	});

	test('project_root sets cwd', () => {
		const settings = { ...baseSettings, project_root: '/home/user/project' };
		const result = resolvePythonCommand(settings);
		expect(result.cwd).toBe('/home/user/project');
	});

	test('fallbackCwd is used when project_root is empty', () => {
		const result = resolvePythonCommand(baseSettings, '/vault/path');
		expect(result.cwd).toBe('/vault/path');
	});

	test('project_root takes precedence over fallbackCwd', () => {
		const settings = { ...baseSettings, project_root: '/project' };
		const result = resolvePythonCommand(settings, '/vault');
		expect(result.cwd).toBe('/project');
	});

	test('src PYTHONPATH auto-detection when cwd is set', () => {
		const settings = { ...baseSettings, project_root: '/home/user/project' };
		const result = resolvePythonCommand(settings);
		const pythonPath = result.env['PYTHONPATH'] || '';
		expect(pythonPath).toContain('/home/user/project/src');
	});

	test('macOS PATH augmentation on darwin', () => {
		const originalPlatform = process.platform;
		const originalHome = process.env.HOME;

		Object.defineProperty(process, 'platform', { value: 'darwin' });
		process.env.HOME = '/Users/testuser';

		try {
			const result = resolvePythonCommand(baseSettings);
			const pathEnv = result.env['PATH'] || '';
			expect(pathEnv).toContain('/opt/homebrew/bin');
			expect(pathEnv).toContain('.local/bin');
			expect(pathEnv).toContain('.cargo/bin');
			expect(pathEnv).toContain('/usr/local/bin');
		} finally {
			Object.defineProperty(process, 'platform', { value: originalPlatform });
			process.env.HOME = originalHome;
		}
	});

	test('empty string parts in python_path are filtered out', () => {
		const settings = { ...baseSettings, python_path: 'python3  ' };
		const result = resolvePythonCommand(settings);
		expect(result.cmd).toBe('python3');
		// No empty string args
		expect(result.args.every((a) => a.trim() !== '')).toBe(true);
	});
});

describe('AreteProcesses', () => {
	const command = { cmd: 'python3', args: ['-m', 'arete'], env: { A: '1' }, cwd: '/project' };

	beforeEach(() => jest.clearAllMocks());

	test('spawns in the command cwd and env, and forgets a process once it exits', () => {
		const child = createMockChildProcess();
		(spawn as jest.Mock).mockReturnValueOnce(child);
		const processes = new AreteProcesses('/vault');

		processes.spawn(command, ['-m', 'arete', 'anki', 'decks']);
		expect(spawn).toHaveBeenCalledWith('python3', ['-m', 'arete', 'anki', 'decks'], {
			cwd: '/project',
			env: { A: '1', ARETE_VAULT_ROOT: '/vault' },
		});
		expect(processes.size).toBe(1);
		child.emit('close', 0, null);
		expect(processes.size).toBe(0);
	});

	test('the vault replaces an inherited ARETE_VAULT_ROOT and keeps the rest of the env', () => {
		(spawn as jest.Mock).mockReturnValue(createMockChildProcess());
		const processes = new AreteProcesses('/vault');

		processes.spawn(command, [], { A: '1', ARETE_VAULT_ROOT: '/elsewhere' });

		expect((spawn as jest.Mock).mock.calls[0][2].env).toEqual({
			A: '1',
			ARETE_VAULT_ROOT: '/vault',
		});
		expect(command.env).toEqual({ A: '1' });
	});

	test('without a vault folder the env is passed as it is', () => {
		(spawn as jest.Mock).mockReturnValue(createMockChildProcess());
		new AreteProcesses('').spawn(command, []);
		expect((spawn as jest.Mock).mock.calls[0][2].env).toEqual({ A: '1' });
	});

	test('killAll kills what is running and refuses to start more', () => {
		const running = createMockChildProcess();
		const exited = createMockChildProcess();
		(spawn as jest.Mock).mockReturnValueOnce(running).mockReturnValueOnce(exited);
		const processes = new AreteProcesses('/vault');
		processes.spawn(command, []);
		processes.spawn(command, []);
		exited.emit('close', 0, null);

		processes.killAll();

		expect(running.kill).toHaveBeenCalledTimes(1);
		expect(exited.kill).not.toHaveBeenCalled();
		expect(processes.size).toBe(0);
		expect(() => processes.spawn(command, [])).toThrow('Arete is unloading');
		expect(spawn).toHaveBeenCalledTimes(2);
	});
});
