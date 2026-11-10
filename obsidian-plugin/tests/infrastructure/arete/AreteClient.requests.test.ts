import '../../test-setup';
import { AreteClient } from '@infrastructure/arete/AreteClient';
import { requestUrl } from 'obsidian';
import { spawn } from 'child_process';
import { createMockChildProcess } from '../../test-setup';

describe('AreteClient: requests and arguments for graph, stats, queue and cards', () => {
	let settings: any;
	let client: AreteClient;

	beforeEach(() => {
		jest.clearAllMocks();
		settings = {
			server_port: 8123,
			execution_mode: 'server',
			backend: 'ankiconnect',
			anki_connect_url: 'http://anki:8765',
			python_path: 'python3',
			project_root: '',
		};
		client = new AreteClient(settings);
	});

	const lastRequest = () => {
		const req = (requestUrl as jest.Mock).mock.calls.pop()![0];
		return { ...req, payload: JSON.parse(req.body) };
	};

	const cli = () => {
		const child = createMockChildProcess();
		(spawn as jest.Mock).mockReturnValue(child);
		return child;
	};
	const spawnedArgs = () => (spawn as jest.Mock).mock.calls[0][1] as string[];

	describe('server mode', () => {
		beforeEach(() => {
			(requestUrl as jest.Mock).mockResolvedValue({ status: 200, json: { ok: true } });
		});

		it('fetchGraph posts the vault root, with the configured backend and AnkiConnect URL', async () => {
			const graph: { nodes: number[] } = { nodes: [] };
			(requestUrl as jest.Mock).mockResolvedValue({ status: 200, json: graph });

			await expect(client.fetchGraph('/vault')).resolves.toBe(graph);

			const req = lastRequest();
			expect(req.url).toBe('http://127.0.0.1:8123/graph');
			expect(req.method).toBe('POST');
			expect(req.headers).toEqual({ 'Content-Type': 'application/json' });
			expect(req.payload).toEqual({
				vault_root: '/vault',
				backend: 'ankiconnect',
				anki_connect_url: 'http://anki:8765',
			});
		});

		it('getCardStats posts the note ids', async () => {
			await client.getCardStats([1, 2]);
			const req = lastRequest();
			expect(req.url).toBe('http://127.0.0.1:8123/anki/stats');
			expect(req.payload.nids).toEqual([1, 2]);
		});

		it('suspend and unsuspend post the card ids and return the server verdict', async () => {
			(requestUrl as jest.Mock).mockResolvedValue({ status: 200, json: { ok: true } });
			await expect(client.suspendCards([5])).resolves.toBe(true);
			expect(lastRequest().url).toBe('http://127.0.0.1:8123/anki/cards/suspend');

			(requestUrl as jest.Mock).mockResolvedValue({ status: 200, json: { ok: false } });
			await expect(client.unsuspendCards([5])).resolves.toBe(false);
			const req = lastRequest();
			expect(req.url).toBe('http://127.0.0.1:8123/anki/cards/unsuspend');
			expect(req.payload.cids).toEqual([5]);
		});

		it('browse posts the query', async () => {
			await expect(client.browse('nid:9')).resolves.toBe(true);
			expect(lastRequest().payload.query).toBe('nid:9');
		});

		it('buildStudyQueue sends the deck, depth and cap, and maps the reply', async () => {
			(requestUrl as jest.Mock).mockResolvedValue({
				status: 200,
				json: {
					deck: 'Math',
					due_count: 3,
					total_with_prereqs: 5,
					queue: [
						{ position: 7, id: 'a', title: 'A', file: 'A.md', is_prereq: true },
						{ id: 'b', title: 'B', file: 'B.md' },
					],
				},
			});

			const res = await client.buildStudyQueue('Math', 2, 40);

			expect(lastRequest().payload).toMatchObject({
				deck: 'Math',
				depth: 2,
				max_cards: 40,
			});
			expect(res).toEqual({
				deck: 'Math',
				dueCount: 3,
				totalWithPrereqs: 5,
				queue: [
					{ position: 7, id: 'a', title: 'A', file: 'A.md', isPrereq: true },
					{ position: 2, id: 'b', title: 'B', file: 'B.md', isPrereq: false },
				],
			});
		});

		it('buildStudyQueue fills defaults for an empty reply and sends a null deck for all decks', async () => {
			(requestUrl as jest.Mock).mockResolvedValue({ status: 200, json: {} });

			const res = await client.buildStudyQueue(null, 1, 10);

			expect(lastRequest().payload.deck).toBeNull();
			expect(res).toEqual({ deck: 'All Decks', dueCount: 0, totalWithPrereqs: 0, queue: [] });
		});

		it('createQueueDeck posts the ids and reports a refusal as false', async () => {
			(requestUrl as jest.Mock).mockResolvedValue({ status: 200, json: {} });
			await expect(client.createQueueDeck(['a', 'b'])).resolves.toBe(false);
			expect(lastRequest().payload.card_ids).toEqual(['a', 'b']);
		});

		it('getDeckNames returns an empty list when the reply has none', async () => {
			(requestUrl as jest.Mock).mockResolvedValue({ status: 200, json: {} });
			await expect(client.getDeckNames()).resolves.toEqual([]);
		});

		it('styling and templates requests carry the backend and AnkiConnect URL as query parameters', async () => {
			(requestUrl as jest.Mock).mockResolvedValue({
				status: 200,
				json: { css: '.card{}' },
			});
			await client.modelStyling('Basic (and reversed)');

			const url = (requestUrl as jest.Mock).mock.calls[0][0] as string;
			expect(
				url.startsWith(
					'http://127.0.0.1:8123/anki/models/Basic%20(and%20reversed)/styling?',
				),
			).toBe(true);
			const params = new URLSearchParams(url.split('?')[1]);
			expect(params.get('backend')).toBe('ankiconnect');
			expect(params.get('anki_connect_url')).toBe('http://anki:8765');
		});

		it('the port setting falls back to 8777 when unset', async () => {
			settings.server_port = 0;
			(requestUrl as jest.Mock).mockResolvedValue({ status: 200, json: {} });
			await client.invokeServer('/x');
			expect(lastRequest().url).toBe('http://127.0.0.1:8777/x');
		});
	});

	describe('CLI mode', () => {
		beforeEach(() => {
			settings.execution_mode = 'cli';
		});

		it('fetchGraph runs `graph export <vault>` and parses the JSON it prints', async () => {
			const child = cli();
			const promise = client.fetchGraph('/my vault');
			child.stdout.emit('data', '{"nodes": []}');
			child.emit('close', 0);

			await expect(promise).resolves.toEqual({ nodes: [] });
			expect(spawnedArgs().slice(-3)).toEqual(['graph', 'export', '/my vault']);
			// no `anki` prefix and no backend flags: graph export reads the vault only
			expect(spawnedArgs()).not.toContain('anki');
			expect(spawnedArgs()).not.toContain('--backend');
		});

		it('a non-zero exit rejects with the code and stderr', async () => {
			const child = cli();
			const promise = client.fetchGraph('/v');
			child.stderr.emit('data', 'boom');
			child.emit('close', 3);
			await expect(promise).rejects.toThrow('CLI Error (3): boom');
		});

		it('extracts a flat JSON object when warnings surround it', async () => {
			const child = cli();
			const promise = client.fetchGraph('/v');
			child.stdout.emit('data', 'warning: slow\n{"ok": true}\ndone\n');
			child.emit('close', 0);
			await expect(promise).resolves.toEqual({ ok: true });
		});

		it('extracts a JSON object that contains an array when warnings surround it', async () => {
			const child = cli();
			const promise = client.fetchGraph('/v');
			child.stdout.emit('data', 'warning: slow\n{"nodes": [1]}\ndone\n');
			child.emit('close', 0);
			await expect(promise).resolves.toEqual({ nodes: [1] });
		});

		it('extracts a JSON array when warnings surround it', async () => {
			const child = cli();
			const promise = client.fetchGraph('/v');
			child.stdout.emit('data', 'warning: slow\n[{"id": 1}, {"id": 2}]\ndone\n');
			child.emit('close', 0);
			await expect(promise).resolves.toEqual([{ id: 1 }, { id: 2 }]);
		});

		it('suspend passes the card ids as JSON and the AnkiConnect URL, and returns ok', async () => {
			const child = cli();
			const promise = client.suspendCards([4, 5]);
			child.stdout.emit('data', '{"ok": true}');
			child.emit('close', 0);

			await expect(promise).resolves.toBe(true);
			const args = spawnedArgs();
			expect(args.slice(args.indexOf('anki'))).toEqual([
				'anki',
				'suspend',
				'--cids',
				'[4,5]',
				'--backend',
				'ankiconnect',
				'--anki-connect-url',
				'http://anki:8765',
			]);
		});

		it('omits --backend for auto and --anki-connect-url when it is empty', async () => {
			settings.backend = 'auto';
			settings.anki_connect_url = '';
			const child = cli();
			const promise = client.getCardStats([]);
			child.stdout.emit('data', '{}');
			child.emit('close', 0);
			await promise;

			const args = spawnedArgs();
			expect(args.slice(args.indexOf('anki'))).toEqual(['anki', 'stats', '--nids', '[]']);
		});

		it('an endpoint the CLI does not implement is refused before anything is spawned', async () => {
			await expect(client.getDeckNames()).resolves.toEqual([]);
			await expect(client.buildStudyQueue(null, 1, 1)).rejects.toThrow(
				'CLI Endpoint not supported: /queue/build',
			);
			expect(spawn).not.toHaveBeenCalled();
		});

		it('output that is not JSON is returned as raw output', async () => {
			const child = cli();
			const promise = client.invokeCLI('/anki/stats', { nids: [] });
			child.stdout.emit('data', 'no json here');
			child.emit('close', 0);
			await expect(promise).resolves.toEqual({ output: 'no json here' });
		});

		it('model styling returns the css, or an empty string when there is none', async () => {
			let child = cli();
			let promise = client.modelStyling('Basic');
			child.stdout.emit('data', '{"css": ".c{}"}');
			child.emit('close', 0);
			await expect(promise).resolves.toBe('.c{}');

			child = cli();
			promise = client.modelStyling('Basic');
			child.stdout.emit('data', '{}');
			child.emit('close', 0);
			await expect(promise).resolves.toBe('');
		});
	});
});
