import { App, Notice, requestUrl } from 'obsidian';
import { spawn, ChildProcess } from 'child_process';
import { AretePluginSettings } from '@domain/settings';
import { resolvePythonCommand } from '@infrastructure/arete/PythonProcess';

export class ServerManager {
	app: App;
	settings: AretePluginSettings;
	private serverProcess: ChildProcess | null = null;
	constructor(app: App, settings: AretePluginSettings) {
		this.app = app;
		this.settings = settings;
	}

	private startPromise: Promise<void> | null = null;

	start(forceRestart = false): Promise<void> {
		if (this.settings.execution_mode !== 'server') return Promise.resolve();

		if (this.startPromise && !forceRestart) {
			return this.startPromise;
		}

		this.startPromise = (async () => {
			// 1. Check if already running
			const isRunning = await this.checkHealth();
			if (isRunning) {
				if (!forceRestart) {
					console.log('[Arete] Server already running.');
					// No Notice here to avoid noise
					return;
				}
				console.log('[Arete] Force restarting server...');
				await this.stop();
				await new Promise((r) => setTimeout(r, 1000));
			}

			// 2. Start Server
			new Notice('Starting Arete Server...');
			try {
				this.spawnServer();

				// 3. Wait for startup
				let attempts = 0;
				const maxAttempts = 15;
				while (attempts < maxAttempts) {
					await new Promise((r) => setTimeout(r, 1000));
					if (await this.checkHealth()) {
						console.log('[Arete] Server is ready.');
						new Notice('Arete Server started successfully!');
						return;
					}
					attempts++;
					if (attempts % 5 === 0) {
						console.log(
							`[Arete] Still waiting for server... (${attempts}/${maxAttempts})`,
						);
					}
				}
				console.error('[Arete] Server failed to respond to health check.');
				new Notice('Failed to connect to Arete Server after spawning.');
			} catch (e) {
				console.error(e);
				new Notice('Failed to spawn Arete Server.');
			}
		})();

		return this.startPromise;
	}

	async stop() {
		if (this.settings.execution_mode !== 'server') return;

		// Graceful shutdown via API
		const port = this.settings.server_port || 8777;
		try {
			await requestUrl({
				url: `http://127.0.0.1:${port}/shutdown`,
				method: 'POST',
			});
			console.log('[Arete] Shutdown signal sent.');
		} catch (e) {
			// Server might already be dead
		}

		if (this.serverProcess) {
			this.serverProcess.kill();
			this.serverProcess = null;
		}
	}

	async restart() {
		await this.start(true);
	}

	private async checkHealth(): Promise<boolean> {
		const port = this.settings.server_port || 8777;
		try {
			const res = await requestUrl({
				url: `http://127.0.0.1:${port}/health`,
				throw: false,
			});
			return res.status === 200;
		} catch (e) {
			return false;
		}
	}

	private spawnServer() {
		const vaultConfig = this.app.vault.adapter as any;
		const vaultPath = vaultConfig.getBasePath ? vaultConfig.getBasePath() : process.cwd();

		const resolved = resolvePythonCommand(this.settings, vaultPath);
		const args = [...resolved.args];

		if (this.settings.server_reload) {
			args.push('--reload');
		}

		args.push('server', '--port', (this.settings.server_port || 8777).toString());

		console.log(`[Arete] Spawning server: ${resolved.cmd} ${args.join(' ')}`);

		this.serverProcess = spawn(resolved.cmd, args, {
			cwd: resolved.cwd,
			env: resolved.env,
			stdio: ['ignore', 'pipe', 'pipe'],
		});

		this.serverProcess.stdout?.on('data', (data) => {
			console.log(`[Arete Server] ${data.toString().trim()}`);
		});

		this.serverProcess.stderr?.on('data', (data) => {
			console.error(`[Arete Server Error] ${data.toString().trim()}`);
		});

		this.serverProcess.on('error', (err) => {
			console.error('[Arete] Server spawn error:', err);
		});

		this.serverProcess.unref();
	}
}
