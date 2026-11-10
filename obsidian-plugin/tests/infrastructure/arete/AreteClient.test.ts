import '../../test-setup';
import { spawn } from 'child_process';
import { AreteClient } from '@infrastructure/arete/AreteClient';
import { createMockChildProcess } from '../../test-setup';

describe('AreteClient: one `arete` process per call, JSON on stdout', () => {
	let settings: any;
	let client: AreteClient;

	beforeEach(() => {
		jest.clearAllMocks();
		settings = {
			backend: 'ankiconnect',
			anki_connect_url: 'http://anki:8765',
			python_path: 'python3',
			project_root: '/project',
		};
		client = new AreteClient(settings, '/v');
	});

	/** The next spawned process; `reply` makes it print `stdout` and exit with `code`. */
	const cli = () => {
		const child = createMockChildProcess();
		(spawn as jest.Mock).mockReturnValueOnce(child);
		return child;
	};
	const reply = (child: any, stdout: string, code = 0) => {
		child.stdout.emit('data', stdout);
		child.emit('close', code);
	};
	/** The arguments after the interpreter's `-m arete`. */
	const areteArgs = (call = 0) => {
		const args = (spawn as jest.Mock).mock.calls[call][1] as string[];
		return args.slice(args.indexOf('arete') + 1);
	};
	const BACKEND = ['--backend', 'ankiconnect', '--anki-connect-url', 'http://anki:8765'];

	describe('arguments', () => {
		it('getCardStats passes the note ids as JSON', async () => {
			const child = cli();
			const promise = client.getCardStats([1, 2]);
			reply(child, '[{"card_id": 1}]');
			await expect(promise).resolves.toEqual([{ card_id: 1 }]);
			expect(areteArgs()).toEqual(['anki', 'stats', '--nids', '[1,2]', ...BACKEND]);
		});

		it('suspend and unsuspend pass the card ids and return the verdict', async () => {
			let child = cli();
			let promise = client.suspendCards([4, 5]);
			reply(child, '{"ok": true}');
			await expect(promise).resolves.toBe(true);
			expect(areteArgs(0)).toEqual(['anki', 'suspend', '--cids', '[4,5]', ...BACKEND]);

			child = cli();
			promise = client.unsuspendCards([6]);
			reply(child, '{"ok": false}');
			await expect(promise).resolves.toBe(false);
			expect(areteArgs(1)).toEqual(['anki', 'unsuspend', '--cids', '[6]', ...BACKEND]);
		});

		it('browse passes the query', async () => {
			const child = cli();
			const promise = client.browse('nid:123');
			reply(child, '{"ok": true}');
			await expect(promise).resolves.toBe(true);
			expect(areteArgs()).toEqual(['anki', 'browse', '--query', 'nid:123', ...BACKEND]);
		});

		it('model styling returns the css, or an empty string when there is none', async () => {
			let child = cli();
			let promise = client.modelStyling('Basic (and reversed)');
			reply(child, '{"css": ".c{}"}');
			await expect(promise).resolves.toBe('.c{}');
			expect(areteArgs()).toEqual(['anki', 'model-css', 'Basic (and reversed)', ...BACKEND]);

			child = cli();
			promise = client.modelStyling('Basic');
			reply(child, '{}');
			await expect(promise).resolves.toBe('');
		});

		it('model templates returns what the CLI printed', async () => {
			const child = cli();
			const promise = client.modelTemplates('Basic');
			reply(child, '{"Card 1": {"Front": "F", "Back": "B"}}');
			await expect(promise).resolves.toEqual({ 'Card 1': { Front: 'F', Back: 'B' } });
			expect(areteArgs()).toEqual(['anki', 'model-templates', 'Basic', ...BACKEND]);
		});

		it('fetchGraph runs `graph export <vault>` with no backend flags', async () => {
			const child = cli();
			const promise = client.fetchGraph('/my vault');
			reply(child, '{"nodes": []}');
			await expect(promise).resolves.toEqual({ nodes: [] });
			expect(areteArgs()).toEqual(['graph', 'export', '/my vault']);
		});

		it('getDeckNames returns the decks, or none when the call fails', async () => {
			let child = cli();
			let promise = client.getDeckNames();
			reply(child, '{"decks": ["Default", "Math"]}');
			await expect(promise).resolves.toEqual(['Default', 'Math']);
			expect(areteArgs()).toEqual(['anki', 'decks', ...BACKEND]);

			jest.spyOn(console, 'error').mockImplementation(() => undefined);
			child = cli();
			promise = client.getDeckNames();
			reply(child, '', 1);
			await expect(promise).resolves.toEqual([]);
		});

		it('buildStudyQueue plans a queue for the vault and maps the reply', async () => {
			const child = cli();
			const promise = client.buildStudyQueue('Math', 3, 25);
			reply(
				child,
				JSON.stringify({
					deck: 'Math',
					due_count: 4,
					total_with_prereqs: 6,
					queue: [
						{ position: 1, id: 'a', title: 'A', file: 'a.md', is_prereq: true },
						{ position: 2, id: 'b', title: 'B', file: 'b.md', is_prereq: false },
					],
				}),
			);
			await expect(promise).resolves.toEqual({
				deck: 'Math',
				dueCount: 4,
				totalWithPrereqs: 6,
				queue: [
					{ position: 1, id: 'a', title: 'A', file: 'a.md', isPrereq: true },
					{ position: 2, id: 'b', title: 'B', file: 'b.md', isPrereq: false },
				],
			});
			expect(areteArgs()).toEqual([
				'queue',
				'/v',
				'--dry-run',
				'--json',
				'--depth',
				'3',
				'--max-cards',
				'25',
				'--deck',
				'Math',
				...BACKEND,
			]);
		});

		it('buildStudyQueue sends no deck for all decks and fills defaults for an empty reply', async () => {
			const child = cli();
			const promise = client.buildStudyQueue(null, 2, 50);
			reply(child, '{}');
			await expect(promise).resolves.toEqual({
				deck: 'All Decks',
				dueCount: 0,
				totalWithPrereqs: 0,
				queue: [],
			});
			expect(areteArgs()).not.toContain('--deck');
		});

		it('buildStudyQueue rejects when the CLI fails', async () => {
			const child = cli();
			const promise = client.buildStudyQueue(null, 2, 50);
			child.stderr.emit('data', 'No vault root configured.');
			reply(child, '', 1);
			await expect(promise).rejects.toThrow(
				'arete queue /v failed (exit 1): No vault root configured.',
			);
		});

		it('buildStudyQueue without a vault folder spawns nothing', async () => {
			await expect(new AreteClient(settings).buildStudyQueue(null, 2, 50)).rejects.toThrow(
				'Cannot determine the vault path.',
			);
			expect(spawn).not.toHaveBeenCalled();
		});

		it('createQueueDeck passes the ids in order and reports a refusal as false', async () => {
			let child = cli();
			let promise = client.createQueueDeck(['arete_B', 'arete_A']);
			reply(child, '{"ok": true, "message": "Created"}');
			await expect(promise).resolves.toBe(true);
			expect(areteArgs()).toEqual([
				'anki',
				'queue-deck',
				'--ids',
				'["arete_B","arete_A"]',
				...BACKEND,
			]);

			child = cli();
			promise = client.createQueueDeck(['arete_B']);
			reply(child, '{"ok": false, "message": "No matching Anki cards found."}');
			await expect(promise).resolves.toBe(false);
		});

		it('omits --backend for auto and --anki-connect-url when it is empty', async () => {
			settings.backend = 'auto';
			settings.anki_connect_url = '';
			const child = cli();
			const promise = client.getCardStats([]);
			reply(child, '[]');
			await promise;
			expect(areteArgs()).toEqual(['anki', 'stats', '--nids', '[]']);
		});

		it('spawns in the project root with the interpreter from the settings', async () => {
			const child = cli();
			const promise = client.fetchGraph('/v');
			reply(child, '{}');
			await promise;
			const [cmd, args, opts] = (spawn as jest.Mock).mock.calls[0];
			expect(cmd).toBe('python3');
			expect(args.slice(0, 2)).toEqual(['-m', 'arete']);
			expect(opts.cwd).toBe('/project');
		});
	});

	describe('dispose (plugin unload)', () => {
		it('kills a call in flight, which rejects, and spawns nothing after', async () => {
			const child = cli();
			const promise = client.getCardStats([1]);
			client.dispose();
			expect(child.kill).toHaveBeenCalledTimes(1);
			child.emit('close', null, 'SIGTERM');
			await expect(promise).rejects.toThrow('arete anki stats was stopped (SIGTERM).');

			await expect(client.fetchGraph('/v')).rejects.toThrow('Arete is unloading');
			expect(spawn).toHaveBeenCalledTimes(1);
		});

		it('a finished call is not killed', async () => {
			const child = cli();
			const promise = client.getCardStats([1]);
			reply(child, '[]');
			await promise;
			client.dispose();
			expect(child.kill).not.toHaveBeenCalled();
		});
	});

	describe('stdout is JSON only', () => {
		it('output with anything around the JSON is an error that quotes it', async () => {
			const child = cli();
			const promise = client.fetchGraph('/v');
			reply(child, 'warning: slow\n{"ok": true}\n');
			await expect(promise).rejects.toThrow(
				'arete graph export printed something other than JSON: "warning: slow',
			);
		});

		it('empty output is an error', async () => {
			const child = cli();
			const promise = client.getCardStats([1]);
			reply(child, '');
			await expect(promise).rejects.toThrow(
				'arete anki stats printed something other than JSON',
			);
		});

		it('a non-zero exit rejects with the command, the code and stderr', async () => {
			const child = cli();
			const promise = client.suspendCards([1]);
			child.stderr.emit('data', 'boom\n');
			reply(child, '', 3);
			await expect(promise).rejects.toThrow('arete anki suspend failed (exit 3): boom');
		});

		it('a process that cannot start rejects with the interpreter and project', async () => {
			const child = cli();
			const promise = client.fetchGraph('/v');
			child.emit('error', new Error('spawn python3 ENOENT'));
			child.emit('close', -2);
			await expect(promise).rejects.toThrow(
				'Could not start Arete (python3, project: /project): spawn python3 ENOENT',
			);
		});
	});
});
