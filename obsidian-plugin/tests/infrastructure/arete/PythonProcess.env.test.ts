import '../../test-setup';
import { spawn } from 'child_process';
import { AreteProcesses, resolvePythonCommand } from '@infrastructure/arete/PythonProcess';
import { createMockChildProcess, onLinux } from '../../test-setup';

onLinux();

describe('resolvePythonCommand: environment', () => {
	const settings: any = { python_path: 'python3', project_root: '/proj' };
	let savedPythonPath: string | undefined;
	let savedPlatform: PropertyDescriptor | undefined;
	let savedHome: string | undefined;

	beforeEach(() => {
		savedPythonPath = process.env.PYTHONPATH;
		savedHome = process.env.HOME;
		savedPlatform = Object.getOwnPropertyDescriptor(process, 'platform');
	});
	afterEach(() => {
		if (savedPythonPath === undefined) delete process.env.PYTHONPATH;
		else process.env.PYTHONPATH = savedPythonPath;
		if (savedHome === undefined) delete process.env.HOME;
		else process.env.HOME = savedHome;
		if (savedPlatform) Object.defineProperty(process, 'platform', savedPlatform);
	});

	it('appends the project src to an existing PYTHONPATH rather than replacing it', () => {
		process.env.PYTHONPATH = '/already/there';
		expect(resolvePythonCommand(settings).env['PYTHONPATH']).toBe('/already/there:/proj/src');
	});

	it('uses the project src alone when PYTHONPATH is unset', () => {
		delete process.env.PYTHONPATH;
		expect(resolvePythonCommand(settings).env['PYTHONPATH']).toBe('/proj/src');
	});

	it('leaves PYTHONPATH alone when there is no project and no fallback directory', () => {
		process.env.PYTHONPATH = '/already/there';
		const result = resolvePythonCommand({ ...settings, project_root: '' });
		expect(result.cwd).toBe('.');
		expect(result.env['PYTHONPATH']).toBe('/already/there');
	});

	it('does not touch the caller process environment', () => {
		delete process.env.PYTHONPATH;
		resolvePythonCommand(settings);
		expect(process.env.PYTHONPATH).toBeUndefined();
	});

	it('adds tool directories to PATH on Linux and macOS', () => {
		process.env.HOME = '/Users/t';
		Object.defineProperty(process, 'platform', { value: 'linux' });
		const linuxPath = resolvePythonCommand(settings).env['PATH']!;
		expect(linuxPath.endsWith('/Users/t/.local/bin:/Users/t/.cargo/bin')).toBe(true);
		expect(linuxPath.startsWith(`${process.env.PATH}:`)).toBe(true);

		Object.defineProperty(process, 'platform', { value: 'darwin' });
		const macPath = resolvePythonCommand(settings).env['PATH']!;
		expect(
			macPath.endsWith(
				'/Users/t/.local/bin:/Users/t/.cargo/bin:/opt/homebrew/bin:/usr/local/bin',
			),
		).toBe(true);
		expect(macPath.startsWith(`${process.env.PATH}:`)).toBe(true);
	});

	it('trims whitespace-only python_path back to python3', () => {
		const result = resolvePythonCommand({ ...settings, python_path: '   \t  ' });
		expect(result.cmd).toBe('python3');
		expect(result.args).toEqual(['-m', 'arete']);
	});

	it('kills a spawned process when its timeout expires', () => {
		jest.useFakeTimers();
		try {
			const child = createMockChildProcess();
			(spawn as jest.Mock).mockReturnValueOnce(child);
			const procs = new AreteProcesses('/vault', 5000);
			procs.spawn(resolvePythonCommand(settings), ['-m', 'arete']);
			jest.advanceTimersByTime(4999);
			expect(child.kill).not.toHaveBeenCalled();
			jest.advanceTimersByTime(1);
			expect(child.kill).toHaveBeenCalledTimes(1);
		} finally {
			jest.useRealTimers();
		}
	});

	it('leaves PATH alone on macOS and Linux when HOME is unset', () => {
		delete process.env.HOME;
		Object.defineProperty(process, 'platform', { value: 'darwin' });
		expect(resolvePythonCommand(settings).env['PATH']).toBe(process.env.PATH);
		Object.defineProperty(process, 'platform', { value: 'linux' });
		expect(resolvePythonCommand(settings).env['PATH']).toBe(process.env.PATH);
	});
});
