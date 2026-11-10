import { App, Notice } from 'obsidian';
import { promises as fsp } from 'fs';
import { ChildProcess } from 'child_process';
import * as path from 'path';
import { AretePluginSettings } from '@domain/settings';
import {
	AreteProcesses,
	PythonCommand,
	resolvePythonCommand,
	vaultBasePath,
} from '@infrastructure/arete/PythonProcess';
import { debugLog } from '@domain/debugLog';

/** A log larger than this is cut to its last LOG_KEEP_BYTES when a run starts. */
export const LOG_MAX_BYTES = 1024 * 1024;
export const LOG_KEEP_BYTES = 256 * 1024;
const LOG_FLUSH_MS = 250;

/**
 * The CLI sync log (`arete_plugin.log`), written off the UI thread. Lines are buffered
 * and appended in order at most every LOG_FLUSH_MS; `flush()` writes the rest and
 * resolves once it is on disk. A run first bounds the file's size.
 */
export class SyncLog {
	private buffer: string[] = [];
	private timer: ReturnType<typeof setTimeout> | null = null;
	private writing: Promise<void>;

	constructor(private logPath: string) {
		this.writing = logPath ? this.bound() : Promise.resolve();
	}

	write(msg: string): void {
		this.buffer.push(`[${new Date().toISOString()}] ${msg}\n`);
		if (!this.timer) this.timer = setTimeout(() => void this.flush(), LOG_FLUSH_MS);
	}

	flush(): Promise<void> {
		if (this.timer) clearTimeout(this.timer);
		this.timer = null;
		if (!this.logPath || this.buffer.length === 0) return this.writing;
		const chunk = this.buffer.join('');
		this.buffer = [];
		this.writing = this.writing
			.then(() => fsp.appendFile(this.logPath, chunk))
			.catch((e) => console.error('[Arete] Failed to write to log file', e));
		return this.writing;
	}

	/** Cut an oversized log to its last LOG_KEEP_BYTES, starting at a line boundary. */
	private async bound(): Promise<void> {
		try {
			const { size } = await fsp.stat(this.logPath);
			if (size <= LOG_MAX_BYTES) return;
			const content = await fsp.readFile(this.logPath);
			const tail = content.subarray(content.length - LOG_KEEP_BYTES);
			const lineStart = tail.indexOf(10) + 1; // after the first newline
			await fsp.writeFile(this.logPath, tail.subarray(lineStart));
		} catch {
			// No log yet, or unreadable: appends report their own failures.
		}
	}
}

/** The exit code `arete sync` uses when another sync holds the sync lock. */
export const SYNC_LOCK_EXIT_CODE = 75;
/** The text of the line `arete sync` prints on stderr when another sync holds the lock. */
export const SYNC_LOCK_PATTERN = /another sync is running/i;

/** Whether a sync stopped because another sync, in any process, was running. */
export function isSyncLockHeld(code: number | null, stderr: string): boolean {
	return code === SYNC_LOCK_EXIT_CODE && SYNC_LOCK_PATTERN.test(stderr);
}

export type SyncStatus = 'idle' | 'syncing' | 'error' | 'success';

/** How a sync ended: it ran, it failed, another sync held the lock, or the plugin stopped it. */
export type SyncOutcome = 'success' | 'failed' | 'busy' | 'stopped';

/** What one `arete` process left behind. `spawnError` is set when it never started. */
interface ProcessResult {
	code: number | null;
	signal: NodeJS.Signals | null;
	stderr: string;
	spawnError?: Error;
}

export class SyncService {
	app: App;
	settings: AretePluginSettings;
	pluginManifest: any;

	private processes: AreteProcesses;
	private disposed = false;

	constructor(app: App, settings: AretePluginSettings, manifest: any) {
		this.app = app;
		this.settings = settings;
		this.pluginManifest = manifest;
		this.processes = new AreteProcesses(vaultBasePath(app));
	}

	/**
	 * Kill a sync in flight and refuse new ones. For plugin unload. A sync cut short
	 * leaves Anki partly updated; the next sync completes it (Obsidian is the source).
	 */
	dispose(): void {
		this.disposed = true;
		this.processes.killAll();
	}

	/**
	 * Run `arete sync` on the vault or on one file, and resolve once it has exited.
	 * Progress goes to the log and the status bar; the result goes to a notice.
	 */
	async runSync(
		prune = false,
		targetPath: string | null = null,
		force = false,
		updateStatusBar: (state: SyncStatus, msg?: string) => void,
	): Promise<SyncOutcome> {
		updateStatusBar('syncing');
		new Notice(targetPath ? 'Syncing file...' : 'Starting sync...');

		const vaultPath = vaultBasePath(this.app);
		if (!vaultPath) {
			new Notice('Cannot determine the vault path.');
			updateStatusBar('error', 'No Vault Path');
			return 'failed';
		}

		const pluginDir =
			(this.pluginManifest && this.pluginManifest.dir) || '.obsidian/plugins/arete';
		const syncLog = new SyncLog(path.join(vaultPath, pluginDir, 'arete_plugin.log'));
		const log = (msg: string) => {
			debugLog(`[Arete] ${msg.trim()}`);
			syncLog.write(msg);
		};

		log(`\n\n=== STARTING NEW SYNC RUN ===`);
		log(`Vault: ${vaultPath}`);

		const resolved = resolvePythonCommand(this.settings, vaultPath);
		const args = [...resolved.args, ...this.syncArgs(prune, force), targetPath || vaultPath];
		log(`Spawning: ${resolved.cmd} ${args.join(' ')}`);

		const result = await this.runProcess(resolved, args, log);
		void syncLog.flush();
		return this.report(result, updateStatusBar);
	}

	/** The options after `sync`, from the command and the settings. */
	private syncArgs(prune: boolean, force: boolean): string[] {
		const args: string[] = [];
		if (this.settings.debug_mode) args.push('--verbose');
		args.push('sync');
		if (prune) args.push('--prune');
		// Forcing re-sends every card, so the content cache is cleared with it.
		if (force) args.push('--force', '--clear-cache');
		if (this.settings.backend && this.settings.backend !== 'auto') {
			args.push('--backend', this.settings.backend);
		}
		if (this.settings.workers) args.push('--workers', this.settings.workers.toString());
		if (
			this.settings.anki_connect_url &&
			this.settings.anki_connect_url !== 'http://localhost:8765'
		) {
			args.push('--anki-connect-url', this.settings.anki_connect_url);
		}
		if (this.settings.anki_media_dir) {
			args.push('--anki-media-dir', this.settings.anki_media_dir);
		}
		return args;
	}

	/** Start one `arete` process and resolve once it has exited or failed to start. */
	private runProcess(
		resolved: PythonCommand,
		args: string[],
		log: (msg: string) => void,
	): Promise<ProcessResult> {
		return new Promise((resolve) => {
			let stderr = '';
			let child: ChildProcess;
			try {
				child = this.processes.spawn(resolved, args, resolved.env);
			} catch (e: any) {
				log(`Exception during spawn: ${e.message}`);
				resolve({ code: null, signal: null, stderr, spawnError: e });
				return;
			}

			child.stdout?.on('data', (data) => {
				if (!data) return;
				for (const l of data.toString().split('\n')) if (l) log(`STDOUT: ${l}`);
			});
			child.stderr?.on('data', (data) => {
				if (!data) return;
				const str = data.toString();
				stderr += str;
				for (const l of str.split('\n')) if (l) log(`STDERR: ${l}`);
			});
			// A process that fails to start may emit 'close' after 'error'; the first wins.
			child.once('error', (err) => {
				log(`Process Error: ${err.message}`);
				resolve({ code: null, signal: null, stderr, spawnError: err });
			});
			child.once('close', (code, signal) => {
				log(signal ? `Process stopped (${signal})` : `Process exited with code ${code}`);
				resolve({ code, signal, stderr });
			});
		});
	}

	/** Tell the user how the sync ended. */
	private report(
		result: ProcessResult,
		updateStatusBar: (state: SyncStatus, msg?: string) => void,
	): SyncOutcome {
		// Stopped by `dispose` on unload: the plugin that would show it is gone. `uv run`
		// passes the signal on and exits with a code, so the flag decides, not the signal.
		if (this.disposed || result.signal) return 'stopped';

		if (result.spawnError) {
			const msg = result.spawnError.message;
			new Notice(`Failed to start sync: ${msg}`);
			updateStatusBar('error', msg);
			return 'failed';
		}
		if (isSyncLockHeld(result.code, result.stderr)) {
			new Notice('A sync is already running.');
			updateStatusBar('idle');
			return 'busy';
		}
		if (result.code === 0) {
			new Notice('Sync completed.');
			updateStatusBar('success');
			return 'success';
		}

		updateStatusBar('error');
		const stderr = result.stderr;
		if (stderr.includes('AnkiConnect call failed') || stderr.includes('Connection refused')) {
			new Notice('Anki is not reachable, check that Anki is open with AnkiConnect.');
		} else if (stderr.includes('ModuleNotFoundError')) {
			new Notice('Python dependencies are missing, check the Python executable path.');
		} else if (stderr.includes('No module named')) {
			new Notice('Invalid Python environment or Arete is not installed.');
		} else {
			new Notice(`Sync failed with exit code ${result.code}, see the log.`);
		}
		return 'failed';
	}
}
