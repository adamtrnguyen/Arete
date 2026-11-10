import { App, FileSystemAdapter, Notice, requestUrl } from 'obsidian';
import { promises as fsp } from 'fs';
import * as path from 'path';
import { AretePluginSettings } from '@domain/settings';
import { AreteProcesses, resolvePythonCommand } from '@infrastructure/arete/PythonProcess';
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

export class SyncService {
	app: App;
	settings: AretePluginSettings;
	pluginManifest: any;

	private processes = new AreteProcesses();

	constructor(app: App, settings: AretePluginSettings, manifest: any) {
		this.app = app;
		this.settings = settings;
		this.pluginManifest = manifest;
	}

	/**
	 * Kill a sync in flight and refuse new ones. For plugin unload. A sync cut short
	 * leaves Anki partly updated; the next sync completes it (Obsidian is the source).
	 */
	dispose(): void {
		this.processes.killAll();
	}

	async runSync(
		prune = false,
		targetPath: string | null = null,
		force = false,
		updateStatusBar: (state: 'idle' | 'syncing' | 'error' | 'success', msg?: string) => void,
	) {
		if (this.settings.execution_mode === 'server') {
			await this.runSyncServer(prune, targetPath, force, updateStatusBar);
		} else {
			await this.runSyncCLI(prune, targetPath, force, updateStatusBar);
		}
	}

	// ─────────────────────────────────────────────────────────────
	// STRATEGY 1: SERVER MODE (HTTP)
	// ─────────────────────────────────────────────────────────────
	async runSyncServer(
		prune: boolean,
		targetPath: string | null,
		force: boolean,
		updateStatusBar: (state: 'idle' | 'syncing' | 'error' | 'success', msg?: string) => void,
	) {
		updateStatusBar('syncing');
		new Notice(targetPath ? 'Syncing file via server...' : 'Starting sync via server...');

		const vaultConfig = this.app.vault.adapter as FileSystemAdapter;
		const vaultPath = vaultConfig.getBasePath ? vaultConfig.getBasePath() : '';

		const port = this.settings.server_port || 8777;
		const url = `http://127.0.0.1:${port}/sync`;

		// Build Payload
		const payload = {
			vault_root: vaultPath,
			file_path: targetPath, // If null, server treats as whole vault sync if root_input is not set?
			// Wait, server logic: overrides['root_input'] = req.file_path.
			// If file_path is null, root_input is None -> resolved to CWD or vault_root.
			// So we should pass vaultPath as vault_root, and file_path only if specific.
			backend: this.settings.backend === 'auto' ? null : this.settings.backend,
			force: force ? true : null,
			prune: prune ? true : null,
			clear_cache: force ? true : null, // Force implies clear cache often
			anki_connect_url: this.settings.anki_connect_url,
			workers: this.settings.workers,
		};

		try {
			const response = await requestUrl({
				url: url,
				method: 'POST',
				body: JSON.stringify(payload),
				contentType: 'application/json',
			});

			if (response.status === 200) {
				const stats = response.json;
				const pr = stats.prune;
				if (pr && pr.status === 'unconfirmed') {
					// The server never deletes without confirm_prune; say what it found.
					new Notice(
						`Prune preview: ${pr.note_count} Arete notes and ${pr.deck_count} decks ` +
							'would be deleted. Nothing was deleted. Run "arete sync --prune" in a ' +
							'terminal to review and confirm.',
						15000,
					);
				}
				if (stats.success) {
					new Notice(`Sync complete, ${stats.total_imported} notes imported.`);
					updateStatusBar('success');
				} else {
					new Notice(`Sync finished with ${stats.total_errors} errors.`);
					updateStatusBar('error', 'Sync Errors');
				}
			} else {
				new Notice(`Server error: HTTP ${response.status}.`);
				updateStatusBar('error', `HTTP ${response.status}`);
			}
		} catch (e: any) {
			console.error('[Arete] Server sync failed', e);
			if (e.message && e.message.includes('Connection refused')) {
				new Notice(
					'The Arete server is not running, switch to CLI mode or start the server.',
				);
			} else {
				new Notice(`Sync failed: ${e.message}`);
			}
			updateStatusBar('error', 'Connection Failed');
		}
	}

	// ─────────────────────────────────────────────────────────────
	// STRATEGY 2: CLI MODE (SUBPROCESS)
	// ─────────────────────────────────────────────────────────────
	async runSyncCLI(
		prune: boolean,
		targetPath: string | null,
		force: boolean,
		updateStatusBar: (state: 'idle' | 'syncing' | 'error' | 'success', msg?: string) => void,
	) {
		updateStatusBar('syncing');
		const action = targetPath ? 'Syncing file...' : 'Starting sync...';
		new Notice(action);

		const vaultConfig = this.app.vault.adapter as FileSystemAdapter;
		if (!vaultConfig.getBasePath) {
			new Notice('Cannot determine the vault path.');
			updateStatusBar('error', 'No Vault Path');
			return;
		}
		const vaultPath = vaultConfig.getBasePath();

		// Logging Setup
		const pluginDir =
			(this.pluginManifest && this.pluginManifest.dir) || '.obsidian/plugins/arete';
		const logPath = vaultPath ? path.join(vaultPath, pluginDir, 'arete_plugin.log') : '';

		const syncLog = new SyncLog(logPath);
		const log = (msg: string) => {
			debugLog(`[Arete] ${msg.trim()}`);
			syncLog.write(msg);
		};

		log(`\n\n=== STARTING NEW SYNC RUN ===`);
		log(`Vault: ${vaultPath}`);

		const resolved = resolvePythonCommand(this.settings, vaultPath);
		const cmd = resolved.cmd;
		const args = [...resolved.args];
		const env = resolved.env;

		if (this.settings.debug_mode) {
			args.push('--verbose');
		}

		args.push('sync');

		if (prune) {
			args.push('--prune');
		}

		if (force) {
			args.push('--force');
			// If forcing, we also typically want to ignore/clear cache to ensure fresh state
			args.push('--clear-cache');
		}

		if (this.settings.backend && this.settings.backend !== 'auto') {
			args.push('--backend');
			args.push(this.settings.backend);
		}

		if (this.settings.workers) {
			args.push('--workers');
			args.push(this.settings.workers.toString());
		}

		if (
			this.settings.anki_connect_url &&
			this.settings.anki_connect_url !== 'http://localhost:8765'
		) {
			args.push('--anki-connect-url');
			args.push(this.settings.anki_connect_url);
		}

		if (this.settings.anki_media_dir) {
			args.push('--anki-media-dir');
			args.push(this.settings.anki_media_dir);
		}

		args.push(targetPath || vaultPath);

		log(`Spawning: ${cmd} ${args.join(' ')}`);

		try {
			const child = this.processes.spawn(resolved, args, env);

			let stderrBuffer = '';

			child.stdout.on('data', (data) => {
				if (!data) return;
				const lines = data.toString().split('\n');
				lines.forEach((l: string) => {
					if (l) log(`STDOUT: ${l}`);
				});
			});

			child.stderr.on('data', (data) => {
				if (!data) return;
				const str = data.toString();
				stderrBuffer += str;
				const lines = str.split('\n');
				lines.forEach((l: string) => {
					if (l) log(`STDERR: ${l}`);
				});
			});

			child.on('close', (code, signal) => {
				log(signal ? `Process stopped (${signal})` : `Process exited with code ${code}`);
				void syncLog.flush();

				if (signal) {
					// Stopped by `dispose` on unload: the plugin that would show it is gone.
					return;
				}
				if (code === 0) {
					new Notice('Sync completed.');
					updateStatusBar('success');
				} else {
					updateStatusBar('error');

					if (
						stderrBuffer.includes('AnkiConnect call failed') ||
						stderrBuffer.includes('Connection refused')
					) {
						new Notice(
							'Anki is not reachable, check that Anki is open with AnkiConnect.',
						);
					} else if (stderrBuffer.includes('ModuleNotFoundError')) {
						new Notice(
							'Python dependencies are missing, check the Python executable path.',
						);
					} else if (stderrBuffer.includes('No module named')) {
						new Notice('Invalid Python environment or Arete is not installed.');
					} else {
						new Notice(`Sync failed with exit code ${code}, see the log.`);
					}
				}
			});

			child.on('error', (err) => {
				log(`Process Error: ${err.message}`);
				void syncLog.flush();
				new Notice(`Failed to start sync: ${err.message}`);
				updateStatusBar('error', err.message);
			});
		} catch (e: any) {
			log(`Exception during spawn: ${e.message}`);
			void syncLog.flush();
			new Notice(`Sync failed: ${e.message}`);
			updateStatusBar('error', e.message);
		}
	}
}
