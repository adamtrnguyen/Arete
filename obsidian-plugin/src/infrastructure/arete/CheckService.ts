import { App, Notice, requestUrl } from 'obsidian';
import { exec } from 'child_process';
import { AretePluginSettings } from '@domain/settings';
import {
	AreteProcesses,
	parseJsonStdout,
	resolvePythonCommand,
} from '@infrastructure/arete/PythonProcess';
import { FileChecker } from '@/domain/ports';

export class CheckService implements FileChecker {
	app: App;
	settings: AretePluginSettings;

	private processes = new AreteProcesses();

	constructor(app: App, settings: AretePluginSettings) {
		this.app = app;
		this.settings = settings;
	}

	/** Kill the checks and fixes in flight and refuse new ones. For plugin unload. */
	dispose(): void {
		this.processes.killAll();
	}

	async getCheckResult(filePath: string): Promise<any> {
		const resolved = resolvePythonCommand(this.settings);
		const args = [...resolved.args, 'vault', 'check', filePath, '--json'];

		return new Promise((resolve, reject) => {
			const child = this.processes.spawn(resolved, args);
			let stdout = '';
			let stderr = '';

			child.stdout.on('data', (d) => (stdout += d.toString()));
			child.stderr.on('data', (d) => (stderr += d.toString()));

			// `vault check --json` prints its JSON on a failed check too, so any exit code
			// with JSON on stdout is a result.
			child.on('close', (_code) => {
				try {
					resolve(parseJsonStdout(stdout, 'arete vault check'));
				} catch (e) {
					console.error('[Arete] Check stderr:', stderr);
					reject(e);
				}
			});

			child.on('error', (err) => {
				reject(err);
			});
		});
	}

	async runFix(filePath: string): Promise<void> {
		const resolved = resolvePythonCommand(this.settings);
		const args = [...resolved.args, 'vault', 'fix', filePath];

		return new Promise((resolve) => {
			const child = this.processes.spawn(resolved, args);

			child.on('close', (code) => {
				if (code === 0) {
					new Notice('File auto-fixed.');
				} else {
					new Notice('Fix failed, check the developer console.');
				}
				resolve();
			});
		});
	}

	async testConfig() {
		new Notice('Testing configuration...');

		const rawSettings = this.settings.python_path;
		if (!rawSettings) {
			new Notice('The Python executable setting is empty.');
			return;
		}

		const resolved = resolvePythonCommand(this.settings);
		const cmd = `${rawSettings} -c "import arete; print('Arete module found')"`;

		const child = exec(
			cmd,
			{ cwd: resolved.cwd, env: resolved.env },
			(error: any, stdout: string, stderr: string) => {
				if (error) {
					console.error('[Arete] Test configuration failed:', error);
					const msg = stderr || stdout || error.message;
					new Notice(`Command failed: ${msg.substring(0, 200)}`);
				} else {
					new Notice('Python found and the Arete module is available.');
				}
			},
		);
		if (child) this.processes.track(child);
	}

	/** Ask AnkiConnect for its version and tell the user whether it answered. */
	async testAnkiConnect() {
		try {
			const response = await requestUrl({
				url: this.settings.anki_connect_url,
				method: 'POST',
				body: JSON.stringify({ action: 'version', version: 6 }),
				contentType: 'application/json',
			});
			if (response.json?.result) {
				new Notice(`AnkiConnect v${response.json.result} is connected.`, 3000);
			} else {
				new Notice('AnkiConnect responded, but its version is unknown.', 5000);
			}
		} catch (e) {
			console.error('[Arete] AnkiConnect test failed:', e);
			new Notice('Cannot connect to AnkiConnect, check that Anki is running.', 8000);
		}
	}
}
