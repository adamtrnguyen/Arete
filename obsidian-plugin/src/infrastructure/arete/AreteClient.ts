import { AretePluginSettings } from '@/domain/settings';
import {
	AreteProcesses,
	parseJsonStdout,
	resolvePythonCommand,
} from '@infrastructure/arete/PythonProcess';
import { GraphExport, GraphSource } from '@/domain/graph/types';
import { AnkiModelSource, CardStatsSource, CardSuspender } from '@/domain/ports';
import { debugLog } from '@/domain/debugLog';

/** One card of a study queue, in study order. */
interface StudyQueueItem {
	position: number;
	id: string;
	title: string;
	file: string;
	isPrereq: boolean;
}

export interface StudyQueue {
	deck: string;
	dueCount: number;
	totalWithPrereqs: number;
	queue: StudyQueueItem[];
}

/**
 * The plugin's calls into Python. Each method runs one `arete` command and parses its
 * stdout, which the CLI keeps to a single JSON value.
 */
export class AreteClient implements GraphSource, AnkiModelSource, CardStatsSource, CardSuspender {
	private settings: AretePluginSettings;
	/** The vault's folder on disk; the queue is planned from its notes. */
	private vaultRoot: string;
	private processes = new AreteProcesses();

	constructor(settings: AretePluginSettings, vaultRoot = '') {
		this.settings = settings;
		this.vaultRoot = vaultRoot;
	}

	/** Kill the calls in flight (they reject) and refuse new ones. For plugin unload. */
	dispose(): void {
		this.processes.killAll();
	}

	async getCardStats(nids: number[]): Promise<unknown> {
		return this.runAnki(['stats', '--nids', JSON.stringify(nids)]);
	}

	async suspendCards(cardIds: number[]): Promise<boolean> {
		const res = await this.runAnki(['suspend', '--cids', JSON.stringify(cardIds)]);
		return res?.ok === true;
	}

	async unsuspendCards(cardIds: number[]): Promise<boolean> {
		const res = await this.runAnki(['unsuspend', '--cids', JSON.stringify(cardIds)]);
		return res?.ok === true;
	}

	async browse(query: string): Promise<boolean> {
		const res = await this.runAnki(['browse', '--query', query]);
		return res?.ok === true;
	}

	async modelStyling(modelName: string): Promise<string> {
		const res = await this.runAnki(['model-css', modelName]);
		return res?.css || '';
	}

	async modelTemplates(
		modelName: string,
	): Promise<Record<string, { Front: string; Back: string }>> {
		return (await this.runAnki(['model-templates', modelName])) || {};
	}

	/** The resolved dependency graph; resolution happens in Python only. */
	async fetchGraph(vaultRoot: string): Promise<GraphExport> {
		return this.runCLI(['graph', 'export', vaultRoot]);
	}

	async getDeckNames(): Promise<string[]> {
		try {
			const res = await this.runAnki(['decks']);
			return res?.decks || [];
		} catch (e) {
			console.error('[Arete] getDeckNames failed:', e);
			return [];
		}
	}

	/** Plan a study queue: due cards with their prerequisites first. Creates no deck. */
	async buildStudyQueue(
		deck: string | null,
		depth: number,
		maxCards: number,
	): Promise<StudyQueue> {
		if (!this.vaultRoot) throw new Error('Cannot determine the vault path.');
		const args = ['queue', this.vaultRoot, '--dry-run', '--json'];
		args.push('--depth', String(depth), '--max-cards', String(maxCards));
		if (deck) args.push('--deck', deck);
		const res = await this.runCLI([...args, ...this.backendArgs()]);
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

	/** Send the cards of these Arete ids, in this order, to Anki's queue deck. */
	async createQueueDeck(cardIds: string[]): Promise<boolean> {
		try {
			const res = await this.runAnki(['queue-deck', '--ids', JSON.stringify(cardIds)]);
			return res?.ok === true;
		} catch (e) {
			console.error('[Arete] createQueueDeck failed:', e);
			return false;
		}
	}

	/** `arete anki <args>` with the backend settings. */
	private runAnki(args: string[]): Promise<any> {
		return this.runCLI(['anki', ...args, ...this.backendArgs()]);
	}

	private backendArgs(): string[] {
		const args: string[] = [];
		if (this.settings.backend && this.settings.backend !== 'auto') {
			args.push('--backend', this.settings.backend);
		}
		if (this.settings.anki_connect_url) {
			args.push('--anki-connect-url', this.settings.anki_connect_url);
		}
		return args;
	}

	/** Run `arete <args>`; resolve with the JSON it prints, reject on any other outcome. */
	private runCLI(args: string[]): Promise<any> {
		const command = `arete ${args.slice(0, 2).join(' ')}`;
		return new Promise((resolve, reject) => {
			const resolved = resolvePythonCommand(this.settings);
			const finalArgs = [...resolved.args, ...args];

			debugLog(`[Arete] Spawning: ${resolved.cmd} ${finalArgs.join(' ')}`);
			const child = this.processes.spawn(resolved, finalArgs);
			child.once('error', (error) => {
				reject(
					new Error(
						`Could not start Arete (${resolved.cmd}, project: ${resolved.cwd}): ${error.message}`,
					),
				);
			});

			let stdout = '';
			let stderr = '';
			child.stdout.on('data', (d) => (stdout += d.toString()));
			child.stderr.on('data', (d) => (stderr += d.toString()));

			child.on('close', (code, signal) => {
				if (signal) {
					reject(new Error(`${command} was stopped (${signal}).`));
					return;
				}
				if (code !== 0) {
					reject(new Error(`${command} failed (exit ${code}): ${stderr.trim()}`));
					return;
				}
				try {
					resolve(parseJsonStdout(stdout, command));
				} catch (e) {
					reject(e);
				}
			});
		});
	}
}
