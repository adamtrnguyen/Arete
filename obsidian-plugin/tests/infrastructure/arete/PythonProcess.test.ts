import '../../test-setup';
import { resolvePythonCommand } from '@infrastructure/arete/PythonProcess';
import * as path from 'path';

describe('resolvePythonCommand', () => {
	const baseSettings: any = {
		python_path: 'python3',
		arete_script_path: '',
		project_root: '',
	};

	test('default settings produce python3 -m arete', () => {
		const result = resolvePythonCommand(baseSettings);
		expect(result.cmd).toBe('python3');
		expect(result.args).toContain('-m');
		expect(result.args).toContain('arete');
		expect(result.cwd).toBe('.');
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

	test('.py script path sets PYTHONPATH and adds -m arete', () => {
		const settings = {
			...baseSettings,
			arete_script_path: '/home/user/arete/src/arete/main.py',
		};
		const result = resolvePythonCommand(settings);
		// PYTHONPATH should be the grandparent of the script
		expect(result.env['PYTHONPATH']).toContain('/home/user/arete/src');
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
		expect(pythonPath).toContain(path.join('/home/user/project', 'src'));
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
