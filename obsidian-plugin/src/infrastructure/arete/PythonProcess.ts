import * as os from 'os';
import * as path from 'path';
import { existsSync } from 'fs';
import { ChildProcess, spawn } from 'child_process';
import { App, FileSystemAdapter } from 'obsidian';
import { AretePluginSettings } from '@/domain/settings';

export interface PythonCommand {
	cmd: string;
	args: string[];
	env: Record<string, string | undefined>;
	cwd: string;
}

/**
 * The command a "Python executable" setting names, split into words. A double-quoted
 * part is one word, so `"C:\Program Files\Python312\python.exe" -m arete` works; so does
 * an unquoted path to an existing file, spaces and all (`C:\Users\Jo Doe\...\arete.exe`).
 */
export function splitCommand(setting: string): string[] {
	const trimmed = setting.trim();
	if (!trimmed.includes('"') && trimmed.includes(' ')) {
		const expanded = expandHome(trimmed);
		if (existsSync(expanded)) return [expanded];
	}
	const words: string[] = [];
	for (const m of trimmed.matchAll(/"([^"]*)"|(\S+)/g)) words.push(m[1] ?? m[2]);
	return words;
}

/** The key the environment stores PATH under: Windows spells it `Path`, case-insensitively. */
function pathKey(env: Record<string, string | undefined>): string {
	return Object.keys(env).find((k) => k.toUpperCase() === 'PATH') ?? 'PATH';
}

/** Append directories to a PATH-like value with the platform's separator. */
function appendPaths(current: string | undefined, extra: string[], delimiter: string): string {
	return [...(current ? [current] : []), ...extra].join(delimiter);
}

/** Expand a leading `~` (home boundary only) so settings work before a restart. */
function expandHome(value: string): string {
	if (value === '~' || value.startsWith('~/') || value.startsWith('~\\')) {
		return os.homedir() + value.slice(1);
	}
	return value;
}

/**
 * Resolves the Python executable, arguments, environment, and working directory
 * from plugin settings. Shared across AreteClient, CheckService and SyncService, so one
 * place decides how Arete is started.
 *
 * Paths and separators follow `process.platform` (Windows: `\` and `;`). On Windows a
 * bare `python3` becomes `python`: python.org installs no `python3`, and the
 * `python3` on PATH is the Microsoft Store alias, which runs nothing.
 */
export function resolvePythonCommand(
	settings: AretePluginSettings,
	fallbackCwd?: string,
): PythonCommand {
	const win = process.platform === 'win32';
	const p = win ? path.win32 : path.posix;
	const pythonSetting = (settings.python_path || '').trim() || 'python3';
	const projectRoot = expandHome(settings.project_root || '');

	const parts = splitCommand(pythonSetting);
	const rawCmd = expandHome(parts[0]);
	const cmd = win && rawCmd === 'python3' ? 'python' : rawCmd;
	const args = parts.slice(1);
	const env: Record<string, string | undefined> = Object.assign({}, process.env);

	// Add "-m arete" unless the command already reaches arete.
	// The check must include `cmd`, not only `args`: with python_path set to the
	// installed console script ("arete"), args is empty, and looking at args alone
	// produced "arete -m arete". That is the setting a user needs in order to stop
	// depending on a checked-out repository, so it has to work.
	// The file name only: a checkout at `.../Arete/.venv/bin/python` still needs -m arete.
	const invokesArete = p.basename(cmd).toLowerCase().startsWith('arete');
	const hasArete = args.some((a) => a.toLowerCase() === 'arete');
	const hasModule = args.includes('-m');
	if (!invokesArete && !hasArete && !hasModule) {
		args.push('-m', 'arete');
	}

	// Auto-detect 'src' folder in project root for PYTHONPATH
	const cwd = projectRoot || fallbackCwd || '.';
	if (cwd && cwd !== '.') {
		env['PYTHONPATH'] = appendPaths(env['PYTHONPATH'], [p.join(cwd, 'src')], p.delimiter);
	}

	// GUI apps start with a short PATH: add where `uv tool install` and Homebrew put
	// commands. On Windows uv puts them in %USERPROFILE%\.local\bin.
	const extraPaths: string[] = [];
	if (process.platform === 'darwin' && process.env.HOME) {
		const home = process.env.HOME;
		extraPaths.push(
			p.join(home, '.local', 'bin'),
			p.join(home, '.cargo', 'bin'),
			'/opt/homebrew/bin',
			'/usr/local/bin',
		);
	} else if (process.platform === 'linux' && process.env.HOME) {
		const home = process.env.HOME;
		extraPaths.push(p.join(home, '.local', 'bin'), p.join(home, '.cargo', 'bin'));
	} else if (win && process.env.USERPROFILE) {
		extraPaths.push(p.join(process.env.USERPROFILE, '.local', 'bin'));
	}
	if (extraPaths.length) {
		const key = pathKey(env);
		env[key] = appendPaths(env[key], extraPaths, p.delimiter);
	}

	return { cmd, args, env, cwd };
}

/** The single JSON value an `arete` command prints, or an error that quotes what it printed. */
export function parseJsonStdout(stdout: string, command: string): any {
	try {
		return JSON.parse(stdout);
	} catch {
		const preview = stdout.trim().substring(0, 200);
		throw new Error(`${command} printed something other than JSON: "${preview}"`);
	}
}

/** The vault's absolute folder on disk, or '' when the vault is not on a file system. */
export function vaultBasePath(app: App): string {
	const adapter = app.vault.adapter as Partial<FileSystemAdapter>;
	return adapter.getBasePath ? adapter.getBasePath() : '';
}

/**
 * The processes one service started and has not seen exit. `killAll` ends them when the
 * plugin unloads and refuses new ones after that, so no `arete` outlives the plugin.
 *
 * Every process gets `ARETE_VAULT_ROOT` set to the vault, so a command run on one file
 * names the vault, not the file's folder, as its root (in `_obsidian_source`, in
 * `obsidian://` links and for media lookup).
 */
/** Default kill timeout for short CLI commands (60 s). Pass 0 to disable. */
export const DEFAULT_PROCESS_TIMEOUT_MS = 60_000;

export class AreteProcesses {
	private live = new Set<ChildProcess>();
	private timers = new Map<ChildProcess, number>();
	private timedOut = new WeakSet<ChildProcess>();
	private closed = false;

	constructor(
		private readonly vaultRoot: string,
		private readonly defaultTimeoutMs: number = DEFAULT_PROCESS_TIMEOUT_MS,
	) {}

	/** Start `arete <args>` as `command` resolves it (interpreter, cwd, env). */
	spawn(
		command: PythonCommand,
		args: string[],
		env: Record<string, string | undefined> = command.env,
		timeoutMs: number = this.defaultTimeoutMs,
	): ChildProcess {
		if (this.closed) throw new Error('Arete is unloading; the command was not started.');
		if (process.platform === 'win32' && /\.(cmd|bat)$/i.test(command.cmd)) {
			// Node starts a .cmd or .bat only through a shell, which would re-parse the
			// arguments (note paths, JSON); it refuses them without one.
			throw new Error(
				`${command.cmd} is a batch file. Set the Python executable to an .exe (python.exe, arete.exe).`,
			);
		}
		const withVault = this.vaultRoot ? { ...env, ARETE_VAULT_ROOT: this.vaultRoot } : env;
		const child = spawn(command.cmd, args, { cwd: command.cwd, env: withVault });
		// Decode as text across chunk boundaries: a UTF-8 character split between two
		// chunks would otherwise become two replacement characters. The CLI prints UTF-8.
		child.stdout?.setEncoding('utf8');
		child.stderr?.setEncoding('utf8');
		return this.track(child, timeoutMs);
	}

	/** Watch a process started elsewhere until it exits. */
	track(child: ChildProcess, timeoutMs = 0): ChildProcess {
		this.live.add(child);
		const cleanup = () => {
			this.live.delete(child);
			const timer = this.timers.get(child);
			if (timer !== undefined) {
				window.clearTimeout(timer);
				this.timers.delete(child);
			}
		};
		child.once('close', cleanup);
		child.once('error', cleanup);
		if (timeoutMs > 0) {
			const timer = window.setTimeout(() => {
				this.timers.delete(child);
				if (this.live.has(child)) {
					this.timedOut.add(child);
					child.kill();
				}
			}, timeoutMs);
			(timer as unknown as { unref?: () => void }).unref?.();
			this.timers.set(child, timer);
		}
		return child;
	}

	/** Whether this process was killed because its timeout elapsed. */
	didTimeOut(child: ChildProcess): boolean {
		return this.timedOut.has(child);
	}

	/** Processes still running. */
	get size(): number {
		return this.live.size;
	}

	killAll(): void {
		this.closed = true;
		for (const timer of this.timers.values()) window.clearTimeout(timer);
		this.timers.clear();
		for (const child of this.live) child.kill();
		this.live.clear();
	}
}
