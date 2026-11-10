import { App, Notice } from 'obsidian';
import { spawn, exec } from 'child_process';
import { AretePluginSettings } from '@domain/settings';
import { resolvePythonCommand } from '@infrastructure/arete/PythonProcess';
import { FileChecker } from '@/domain/ports';

export class CheckService implements FileChecker {
	app: App;
	settings: AretePluginSettings;

	constructor(app: App, settings: AretePluginSettings) {
		this.app = app;
		this.settings = settings;
	}

	async getCheckResult(filePath: string): Promise<any> {
		const resolved = resolvePythonCommand(this.settings);
		const args = [...resolved.args, 'vault', 'check', filePath, '--json'];

		return new Promise((resolve, reject) => {
			const child = spawn(resolved.cmd, args, { env: resolved.env, cwd: resolved.cwd });
			let stdout = '';
			let stderr = '';

			child.stdout.on('data', (d) => (stdout += d.toString()));
			child.stderr.on('data', (d) => (stderr += d.toString()));

			child.on('close', (_code) => {
				try {
					const res = JSON.parse(stdout);
					resolve(res);
				} catch (e) {
					console.error('Failed to parse check output', stdout);
					console.error('Stderr:', stderr);

					// Fallback: Try to extract JSON from stdout (in case of log spew)
					try {
						const jsonMatch = stdout.match(/\{[\s\S]*\}/);
						if (jsonMatch) {
							const res = JSON.parse(jsonMatch[0]);
							resolve(res);
							return;
						}
					} catch (e2) {
						// Fallback failed
					}

					const preview = stdout.trim().substring(0, 200);
					const errPreview = stderr.trim().substring(0, 200);
					reject(new Error(`Parse Error. Stdout: "${preview}" Stderr: "${errPreview}"`));
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
			const child = spawn(resolved.cmd, args, { env: resolved.env, cwd: resolved.cwd });

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

		exec(
			cmd,
			{ cwd: resolved.cwd, env: resolved.env },
			(error: any, stdout: string, stderr: string) => {
				if (error) {
					console.error('Test Config Failed:', error);
					const msg = stderr || stdout || error.message;
					new Notice(`Command failed: ${msg.substring(0, 200)}`);
				} else {
					new Notice('Python found and the Arete module is available.');
				}
			},
		);
	}
}
