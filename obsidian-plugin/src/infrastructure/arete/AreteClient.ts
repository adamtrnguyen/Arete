import { requestUrl } from 'obsidian';
import { spawn } from 'child_process';
import { AretePluginSettings } from '@/domain/settings';
import { resolvePythonCommand } from '@infrastructure/arete/PythonProcess';

export class AreteClient {
	private settings: AretePluginSettings;
	private url: string;

	constructor(settings: AretePluginSettings) {
		this.settings = settings;
		this.url = `http://127.0.0.1:${settings.server_port || 8777}`;
	}

	// NOTE: This class previously called AnkiConnect directly.
	// It is now REFRACTORED to act as a client for the Arete Server OR CLI.

	async invoke(endpoint: string, body: any = {}): Promise<any> {
		if (this.settings.execution_mode === 'cli') {
			return this.invokeCLI(endpoint, body);
		}

		return this.invokeServer(endpoint, body);
	}

	async invokeServer(endpoint: string, body: any = {}): Promise<any> {
		const areteServerUrl = `http://127.0.0.1:${this.settings.server_port || 8777}`;

		console.log(`[Arete] Server Invoke: ${endpoint}`, body);

		// Inject Config Overrides
		const payload = {
			...body,
			backend: this.settings.backend,
			anki_connect_url: this.settings.anki_connect_url,
		};

		try {
			const response = await requestUrl({
				url: `${areteServerUrl}${endpoint}`,
				method: 'POST', // Most are POST
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify(payload),
				throw: false,
			});

			if (response.status >= 300) {
				throw new Error(`Server returned ${response.status}: ${response.text}`);
			}
			const result = response.json;
			return result;
		} catch (error) {
			console.error(`Arete Server Error (${endpoint}):`, error);
			throw error;
		}
	}

	async invokeCLI(endpoint: string, body: any): Promise<any> {
		console.log(`[Arete] CLI Invoke: ${endpoint}`, body);

		// Map Endpoint to Arguments
		const args: string[] = ['anki'];

		if (endpoint === '/anki/cards/suspend') {
			args.push('cards-suspend');
			args.push('--cids');
			args.push(JSON.stringify(body.cids || []));
		} else if (endpoint === '/anki/cards/unsuspend') {
			args.push('cards-unsuspend');
			args.push('--cids');
			args.push(JSON.stringify(body.cids || []));
		} else if (endpoint.startsWith('/anki/models/')) {
			const parts = endpoint.split('/');
			if (parts.length >= 5) {
				const modelName = decodeURIComponent(parts[3]);
				const action = parts[4].split('?')[0]; // remove query params
				if (action === 'styling') {
					args.push('models-styling');
					args.push(modelName);
				} else if (action === 'templates') {
					args.push('models-templates');
					args.push(modelName);
				}
			}
		} else if (endpoint === '/anki/stats') {
			args.push('stats');
			args.push('--nids');
			args.push(JSON.stringify(body.nids || []));
		} else if (endpoint === '/anki/browse') {
			args.push('browse');
			args.push('--query');
			args.push(body.query);
		} else {
			throw new Error(`CLI Endpoint not supported: ${endpoint}`);
		}

		// Config Overrides
		if (this.settings.backend && this.settings.backend !== 'auto') {
			args.push('--backend', this.settings.backend);
		}
		if (this.settings.anki_connect_url) {
			args.push('--anki-connect-url', this.settings.anki_connect_url);
		}

		// Spawn Logic
		return new Promise((resolve, reject) => {
			const resolved = resolvePythonCommand(this.settings);
			const finalArgs = [...resolved.args, ...args];

			console.log(`[Arete] Spawning: ${resolved.cmd} ${finalArgs.join(' ')}`);
			const child = spawn(resolved.cmd, finalArgs, { cwd: resolved.cwd, env: resolved.env });

			let stdout = '';
			let stderr = '';

			child.stdout.on('data', (d) => (stdout += d.toString()));
			child.stderr.on('data', (d) => (stderr += d.toString()));

			child.on('close', (code) => {
				if (code === 0) {
					const trimmed = stdout.trim();
					try {
						// 1. Try to parse the entire trimmed stdout
						resolve(JSON.parse(trimmed));
					} catch (e) {
						// 2. Fallback: Find the first/last brackets to extract JSON block
						// This handles cases where warnings or logs are mixed with JSON
						const startIndex = trimmed.search(/[[{]/);
						const endIndex = trimmed.lastIndexOf(trimmed.match(/[\]}]/)?.[0] || '');

						if (startIndex !== -1 && endIndex !== -1 && endIndex >= startIndex) {
							const jsonBlock = trimmed.substring(startIndex, endIndex + 1);
							try {
								resolve(JSON.parse(jsonBlock));
								return; // Success!
							} catch (e2) {
								console.error('[Arete] Failed to parse extracted JSON block:', e2);
							}
						}

						// 3. Final Fallback: Return as output object
						resolve({ output: stdout });
					}
				} else {
					reject(new Error(`CLI Error (${code}): ${stderr}`));
				}
			});
		});
	}

	async modelStyling(modelName: string): Promise<string> {
		// GET /anki/models/{name}/styling
		const endpoint = `/anki/models/${encodeURIComponent(modelName)}/styling`;
		if (this.settings.execution_mode === 'cli') {
			const res = await this.invokeCLI(endpoint, {});
			return res.css || '';
		}

		// Server Mode
		const params = new URLSearchParams({
			backend: this.settings.backend || '',
			anki_connect_url: this.settings.anki_connect_url || '',
		});
		const response = await requestUrl(`${this.url}${endpoint}?${params.toString()}`);
		if (response.status !== 200) return '';
		return response.json?.css || '';
	}

	async modelTemplates(
		modelName: string,
	): Promise<Record<string, { Front: string; Back: string }>> {
		// GET /anki/models/{name}/templates
		const endpoint = `/anki/models/${encodeURIComponent(modelName)}/templates`;
		if (this.settings.execution_mode === 'cli') {
			return this.invokeCLI(endpoint, {});
		}

		const params = new URLSearchParams({
			backend: this.settings.backend || '',
			anki_connect_url: this.settings.anki_connect_url || '',
		});
		const response = await requestUrl(`${this.url}${endpoint}?${params.toString()}`);
		if (response.status !== 200) return {};
		return response.json || {};
	}

	async suspendCards(cardIds: number[]): Promise<boolean> {
		const res = await this.invoke('/anki/cards/suspend', { cids: cardIds });
		return res.ok;
	}

	async unsuspendCards(cardIds: number[]): Promise<boolean> {
		const res = await this.invoke('/anki/cards/unsuspend', { cids: cardIds });
		return res.ok;
	}

	async browse(query: string): Promise<boolean> {
		const res = await this.invoke('/anki/browse', { query });
		return res && res.ok;
	}

	async getDeckNames(): Promise<string[]> {
		try {
			const res = await this.invoke('/anki/decks', {});
			return res.decks || [];
		} catch (e) {
			console.error('[AreteClient] getDeckNames failed:', e);
			return [];
		}
	}

	async buildStudyQueue(
		deck: string | null,
		depth: number,
		maxCards: number,
	): Promise<{
		deck: string;
		dueCount: number;
		totalWithPrereqs: number;
		queue: Array<{
			position: number;
			id: string;
			title: string;
			file: string;
			isPrereq: boolean;
		}>;
	}> {
		const res = await this.invoke('/queue/build', {
			deck: deck || null,
			depth,
			max_cards: maxCards,
		});
		return {
			deck: res.deck || 'All Decks',
			dueCount: res.due_count || 0,
			totalWithPrereqs: res.total_with_prereqs || 0,
			queue: (res.queue || []).map((c: any, idx: number) => ({
				position: c.position || idx + 1,
				id: c.id,
				title: c.title,
				file: c.file,
				isPrereq: c.is_prereq || false,
			})),
		};
	}

	async createQueueDeck(cardIds: string[]): Promise<boolean> {
		try {
			const res = await this.invoke('/queue/create-deck', { card_ids: cardIds });
			return res.ok || false;
		} catch (e) {
			console.error('[AreteClient] createQueueDeck failed:', e);
			return false;
		}
	}
}
