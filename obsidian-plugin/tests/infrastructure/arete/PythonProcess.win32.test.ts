import '../../test-setup';
import { existsSync } from 'fs';
import { spawn } from 'child_process';
import {
	AreteProcesses,
	resolvePythonCommand,
	splitCommand,
} from '@infrastructure/arete/PythonProcess';
import { createMockChildProcess } from '../../test-setup';

/** How the plugin starts Arete on Windows, with process.platform set to win32 on any host. */
describe('resolvePythonCommand on Windows', () => {
	const settings: any = { python_path: 'python3', project_root: '' };
	let savedPlatform: PropertyDescriptor | undefined;
	let savedEnv: NodeJS.ProcessEnv;

	beforeEach(() => {
		savedPlatform = Object.getOwnPropertyDescriptor(process, 'platform');
		savedEnv = process.env;
		Object.defineProperty(process, 'platform', { value: 'win32' });
		process.env = { Path: 'C:\\Windows\\System32', USERPROFILE: 'C:\\Users\\Jo Doe' };
		(existsSync as jest.Mock).mockReset();
	});
	afterEach(() => {
		if (savedPlatform) Object.defineProperty(process, 'platform', savedPlatform);
		process.env = savedEnv;
	});

	test('the default python3, which Windows does not install, runs python', () => {
		const r = resolvePythonCommand(settings);
		expect([r.cmd, ...r.args]).toEqual(['python', '-m', 'arete']);
	});

	test('the installed arete.exe runs as it is', () => {
		const r = resolvePythonCommand({ ...settings, python_path: 'arete' });
		expect([r.cmd, ...r.args]).toEqual(['arete']);
	});

	test('the py launcher gets -m arete', () => {
		const r = resolvePythonCommand({ ...settings, python_path: 'py -3.12' });
		expect([r.cmd, ...r.args]).toEqual(['py', '-3.12', '-m', 'arete']);
	});

	test('a quoted path with spaces is one word', () => {
		const r = resolvePythonCommand({
			...settings,
			python_path: '"C:\\Program Files\\Python312\\python.exe" -m arete',
		});
		expect([r.cmd, ...r.args]).toEqual([
			'C:\\Program Files\\Python312\\python.exe',
			'-m',
			'arete',
		]);
	});

	test('an unquoted path to an existing file is one word, spaces and all', () => {
		const exe = 'C:\\Users\\Jo Doe\\.local\\bin\\arete.exe';
		(existsSync as jest.Mock).mockImplementation((p) => p === exe);
		const r = resolvePythonCommand({ ...settings, python_path: exe });
		expect([r.cmd, ...r.args]).toEqual([exe]);
	});

	test('a checkout named Arete still runs its interpreter with -m arete', () => {
		const python = 'C:\\src\\Arete\\.venv\\Scripts\\python.exe';
		const r = resolvePythonCommand({ ...settings, python_path: python });
		expect([r.cmd, ...r.args]).toEqual([python, '-m', 'arete']);
	});

	test('PYTHONPATH and PATH use ; and Windows separators, under the existing Path key', () => {
		process.env.PYTHONPATH = 'C:\\lib';
		const r = resolvePythonCommand({ ...settings, project_root: 'C:\\src\\Arete' });
		expect(r.env['PYTHONPATH']).toBe('C:\\lib;C:\\src\\Arete\\src');
		expect(r.env['Path']).toBe('C:\\Windows\\System32;C:\\Users\\Jo Doe\\.local\\bin');
		expect(r.env['PATH']).toBeUndefined();
	});

	test('splitCommand keeps quoted words and drops extra spaces', () => {
		expect(splitCommand('  uv  run "my dir\\python.exe" ')).toEqual([
			'uv',
			'run',
			'my dir\\python.exe',
		]);
	});
});

describe('AreteProcesses on Windows', () => {
	let savedPlatform: PropertyDescriptor | undefined;
	beforeEach(() => {
		jest.clearAllMocks();
		savedPlatform = Object.getOwnPropertyDescriptor(process, 'platform');
		Object.defineProperty(process, 'platform', { value: 'win32' });
	});
	afterEach(() => {
		if (savedPlatform) Object.defineProperty(process, 'platform', savedPlatform);
	});

	test('a .cmd shim is refused with a message, not handed to a shell', () => {
		const processes = new AreteProcesses('C:\\Vault');
		const command = { cmd: 'C:\\tools\\arete.cmd', args: [] as string[], env: {}, cwd: '.' };
		expect(() => processes.spawn(command, ['sync'])).toThrow('is a batch file');
		expect(spawn).not.toHaveBeenCalled();
	});

	test('a vault path with spaces is one argument, with no shell', () => {
		const child = createMockChildProcess();
		(spawn as jest.Mock).mockReturnValue(child);
		const processes = new AreteProcesses('C:\\Users\\Jo Doe\\My Vault');
		processes.spawn({ cmd: 'arete', args: [], env: {}, cwd: '.' }, [
			'sync',
			'C:\\Users\\Jo Doe\\My Vault',
		]);
		const [cmd, args, opts] = (spawn as jest.Mock).mock.calls[0];
		expect([cmd, args, opts.shell]).toEqual([
			'arete',
			['sync', 'C:\\Users\\Jo Doe\\My Vault'],
			undefined,
		]);
		expect(opts.env.ARETE_VAULT_ROOT).toBe('C:\\Users\\Jo Doe\\My Vault');
		expect(child.stdout.setEncoding).toHaveBeenCalledWith('utf8');
		expect(child.stderr.setEncoding).toHaveBeenCalledWith('utf8');
	});
});
