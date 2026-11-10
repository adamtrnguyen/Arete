import * as path from 'path';
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
 * Resolves the Python executable, arguments, environment, and working directory
 * from plugin settings. Shared across AreteClient, CheckService and SyncService, so one
 * place decides how Arete is started.
 */
export function resolvePythonCommand(
	settings: AretePluginSettings,
	fallbackCwd?: string,
): PythonCommand {
	const pythonSetting = settings.python_path || 'python3';
	const projectRoot = settings.project_root || '';

	const parts = pythonSetting.split(' ').filter((p) => p.trim() !== '');
	const cmd = parts[0];
	const args = parts.slice(1);
	const env: Record<string, string | undefined> = Object.assign({}, process.env);

	// Add "-m arete" unless the command already reaches arete.
	// The check must include `cmd`, not only `args`: with python_path set to the
	// installed console script ("arete"), args is empty, and looking at args alone
	// produced "arete -m arete". That is the setting a user needs in order to stop
	// depending on a checked-out repository, so it has to work.
	const invokesArete = cmd.toLowerCase().includes('arete');
	const hasArete = args.some((a) => a.toLowerCase().includes('arete'));
	const hasModule = args.includes('-m');
	if (!invokesArete && !hasArete && !hasModule) {
		args.push('-m', 'arete');
	}

	// Auto-detect 'src' folder in project root for PYTHONPATH
	const cwd = projectRoot || fallbackCwd || '.';
	if (cwd && cwd !== '.') {
		const srcPath = path.join(cwd, 'src');
		const currentPath = env['PYTHONPATH'] || '';
		env['PYTHONPATH'] = currentPath ? `${currentPath}:${srcPath}` : srcPath;
	}

	// Fix PATH on macOS for GUI apps (Obsidian often lacks .local/bin)
	if (process.platform === 'darwin' && process.env.HOME) {
		const home = process.env.HOME;
		const extraPaths = [
			path.join(home, '.local', 'bin'),
			path.join(home, '.cargo', 'bin'),
			'/opt/homebrew/bin',
			'/usr/local/bin',
		];
		const currentPath = env['PATH'] || '';
		env['PATH'] = `${currentPath}:${extraPaths.join(':')}`;
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
export class AreteProcesses {
	private live = new Set<ChildProcess>();
	private closed = false;

	constructor(private readonly vaultRoot: string) {}

	/** Start `arete <args>` as `command` resolves it (interpreter, cwd, env). */
	spawn(
		command: PythonCommand,
		args: string[],
		env: Record<string, string | undefined> = command.env,
	): ChildProcess {
		if (this.closed) throw new Error('Arete is unloading; the command was not started.');
		const withVault = this.vaultRoot ? { ...env, ARETE_VAULT_ROOT: this.vaultRoot } : env;
		return this.track(spawn(command.cmd, args, { cwd: command.cwd, env: withVault }));
	}

	/** Watch a process started elsewhere until it exits. */
	track(child: ChildProcess): ChildProcess {
		this.live.add(child);
		child.once('close', () => this.live.delete(child));
		child.once('error', () => this.live.delete(child));
		return child;
	}

	/** Processes still running. */
	get size(): number {
		return this.live.size;
	}

	killAll(): void {
		this.closed = true;
		for (const child of this.live) child.kill();
		this.live.clear();
	}
}
