import '../../test-setup';
import { resolvePythonCommand } from '@infrastructure/arete/PythonProcess';
import { onLinux } from '../../test-setup';

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

	it('adds the extra tool directories to PATH on macOS only', () => {
		process.env.HOME = '/Users/t';
		Object.defineProperty(process, 'platform', { value: 'linux' });
		const before = resolvePythonCommand(settings).env['PATH'];
		expect(before).toBe(process.env.PATH);

		Object.defineProperty(process, 'platform', { value: 'darwin' });
		const after = resolvePythonCommand(settings).env['PATH']!;
		expect(
			after.endsWith(
				'/Users/t/.local/bin:/Users/t/.cargo/bin:/opt/homebrew/bin:/usr/local/bin',
			),
		).toBe(true);
		expect(after.startsWith(`${process.env.PATH}:`)).toBe(true);
	});

	it('leaves PATH alone on macOS when HOME is unset', () => {
		delete process.env.HOME;
		Object.defineProperty(process, 'platform', { value: 'darwin' });
		expect(resolvePythonCommand(settings).env['PATH']).toBe(process.env.PATH);
	});
});
