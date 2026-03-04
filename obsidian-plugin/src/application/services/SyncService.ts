import { App, FileSystemAdapter, Notice, requestUrl } from 'obsidian';
import { spawn } from 'child_process';
import * as path from 'path';
import { AretePluginSettings } from '@domain/settings';
import { resolvePythonCommand } from '@infrastructure/arete/PythonProcess';

export class SyncService {
	app: App;
	settings: AretePluginSettings;
	pluginManifest: any;

	constructor(app: App, settings: AretePluginSettings, manifest: any) {
		this.app = app;
		this.settings = settings;
		this.pluginManifest = manifest;
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
		new Notice(targetPath ? 'Syncing file via Server...' : 'Starting sync via Server...');

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
				if (stats.success) {
					new Notice(`Sync Complete! (+${stats.total_imported} notes)`);
					updateStatusBar('success');
				} else {
					new Notice(`Sync finished with ${stats.total_errors} errors.`);
					updateStatusBar('error', 'Sync Errors');
				}
			} else {
				new Notice(`Server Error: ${response.status}`);
				updateStatusBar('error', `HTTP ${response.status}`);
			}
		} catch (e: any) {
			console.error('Server Sync Failed', e);
			if (e.message && e.message.includes('Connection refused')) {
				new Notice('Arete Server is not running! Switch to CLI mode or start server.');
			} else {
				new Notice(`Sync Failed: ${e.message}`);
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
		const action = targetPath ? 'Sycing file...' : 'Starting arete sync...';
		new Notice(action);

		const vaultConfig = this.app.vault.adapter as FileSystemAdapter;
		if (!vaultConfig.getBasePath) {
			new Notice('Error: Cannot determine vault path.');
			updateStatusBar('error', 'No Vault Path');
			return;
		}
		const vaultPath = vaultConfig.getBasePath();

		// Logging Setup
		const pluginDir =
			(this.pluginManifest && this.pluginManifest.dir) || '.obsidian/plugins/arete';
		const logPath = vaultPath ? path.join(vaultPath, pluginDir, 'arete_plugin.log') : '';

		const log = (msg: string) => {
			const timestamp = new Date().toISOString();
			const line = `[${timestamp}] ${msg}\n`;
			console.log(msg);
			try {
				// eslint-disable-next-line @typescript-eslint/no-var-requires
				const fs = require('fs');
				fs.appendFileSync(logPath, line);
			} catch (e) {
				console.error('Failed to write to log file', e);
			}
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
			const child = spawn(cmd, args, {
				cwd: resolved.cwd,
				env: env,
			});

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

			child.on('close', (code) => {
				log(`Process exited with code ${code}`);

				if (code === 0) {
					new Notice('arete sync completed successfully!');
					updateStatusBar('success');
				} else {
					updateStatusBar('error');

					if (
						stderrBuffer.includes('AnkiConnect call failed') ||
						stderrBuffer.includes('Connection refused')
					) {
						new Notice('Error: Anki is not reachable. Is Anki open with AnkiConnect?');
					} else if (stderrBuffer.includes('ModuleNotFoundError')) {
						new Notice(
							'Error: Python Dependencies missing. Check Python Executable path.',
						);
					} else if (stderrBuffer.includes('No module named')) {
						new Notice('Error: Invalid Python environment or arete not installed.');
					} else {
						new Notice(`arete sync failed! (Code ${code}). See log.`);
					}
				}
			});

			child.on('error', (err) => {
				log(`Process Error: ${err.message}`);
				new Notice(`Failed to start: ${err.message}`);
				updateStatusBar('error', err.message);
			});
		} catch (e: any) {
			log(`Exception during spawn: ${e.message}`);
			new Notice(`Exception: ${e.message}`);
			updateStatusBar('error', e.message);
		}
	}
}
