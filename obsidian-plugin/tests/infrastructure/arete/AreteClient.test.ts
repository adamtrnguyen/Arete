import '../../test-setup';
import { AreteClient } from '@infrastructure/arete/AreteClient';
import { requestUrl } from 'obsidian';
import { spawn } from 'child_process';
import { createMockChildProcess } from '../../test-setup';

describe('AreteClient', () => {
	let client: AreteClient;
	let settings: any;

	beforeEach(() => {
		jest.clearAllMocks();
		settings = {
			server_port: 8777,
			execution_mode: 'server',
			backend: 'auto',
			anki_connect_url: 'http://localhost:8765',
		};
		client = new AreteClient(settings);
	});

	describe('invokeServer', () => {
		test('successful server invocation', async () => {
			(requestUrl as jest.Mock).mockResolvedValue({
				status: 200,
				json: { result: 'ok' },
			});

			const result = await client.invokeServer('/test', { data: 123 });

			expect(requestUrl).toHaveBeenCalledWith(
				expect.objectContaining({
					url: 'http://127.0.0.1:8777/test',
					method: 'POST',
					body: JSON.stringify({
						data: 123,
						backend: 'auto',
						anki_connect_url: 'http://localhost:8765',
					}),
				}),
			);
			expect(result).toEqual({ result: 'ok' });
		});

		test('successful invoke in server mode', async () => {
			settings.execution_mode = 'server';
			(requestUrl as jest.Mock).mockResolvedValue({
				status: 200,
				json: { result: 'ok' },
			});

			const result = await client.invoke('/test', { data: 456 });

			expect(result).toEqual({ result: 'ok' });
		});

		test('server error handling (status 400)', async () => {
			(requestUrl as jest.Mock).mockResolvedValue({
				status: 400,
				text: 'Bad Request',
				throw: false,
			});

			await expect(client.invokeServer('/test')).rejects.toThrow(
				'Server returned 400: Bad Request',
			);
		});
	});

	describe('invokeCLI', () => {
		beforeEach(() => {
			settings.execution_mode = 'cli';
			settings.python_path = 'python3';
		});

		test('cli invocation for suspendCards', async () => {
			const mockChild = createMockChildProcess();
			(spawn as jest.Mock).mockReturnValue(mockChild);

			const promise = client.suspendCards([1, 2, 3]);

			// Simulate CLI output
			mockChild.stdout.emit('data', JSON.stringify({ ok: true }));
			mockChild.emit('close', 0);

			const result = await promise;
			expect(result).toBe(true);
			expect(spawn).toHaveBeenCalledWith(
				'python3',
				expect.arrayContaining([
					'-m',
					'arete',
					'anki',
					'cards-suspend',
					'--cids',
					'[1,2,3]',
				]),
				expect.any(Object),
			);
		});

		test('cli invocation for unsuspendCards', async () => {
			const mockChild = createMockChildProcess();
			(spawn as jest.Mock).mockReturnValue(mockChild);

			const promise = client.unsuspendCards([4, 5]);
			mockChild.stdout.emit('data', JSON.stringify({ ok: true }));
			mockChild.emit('close', 0);

			const result = await promise;
			expect(result).toBe(true);
			expect(spawn).toHaveBeenCalledWith(
				'python3',
				expect.arrayContaining(['cards-unsuspend', '--cids', '[4,5]']),
				expect.any(Object),
			);
		});

		test('cli invocation for browse', async () => {
			const mockChild = createMockChildProcess();
			(spawn as jest.Mock).mockReturnValue(mockChild);

			const promise = client.browse('deck:Default');
			mockChild.stdout.emit('data', JSON.stringify({ ok: true }));
			mockChild.emit('close', 0);

			const result = await promise;
			expect(result).toBe(true);
			expect(spawn).toHaveBeenCalledWith(
				'python3',
				expect.arrayContaining(['browse', '--query', 'deck:Default']),
				expect.any(Object),
			);
		});

		test('cli invocation for browse failure', async () => {
			const mockChild = createMockChildProcess();
			(spawn as jest.Mock).mockReturnValue(mockChild);

			const promise = client.browse('deck:Default');
			mockChild.stdout.emit('data', JSON.stringify({ ok: false }));
			mockChild.emit('close', 0);

			const result = await promise;
			expect(result).toBe(false);
		});

		test('unsupported endpoint error', async () => {
			await expect(client.invokeCLI('/invalid', {})).rejects.toThrow(
				'CLI Endpoint not supported: /invalid',
			);
		});

		test('cli invocation for stats with JSON fallback', async () => {
			const mockChild = createMockChildProcess();
			(spawn as jest.Mock).mockReturnValue(mockChild);

			const promise = client.invokeCLI('/anki/stats', { nids: [100] });

			// Simulate messy CLI output with logs and JSON
			mockChild.stdout.emit('data', 'DEBUG: some log\n{"result": "success"}\nINFO: done');
			mockChild.emit('close', 0);

			const result = await promise;
			expect(result).toEqual({ result: 'success' });
			expect(spawn).toHaveBeenCalledWith(
				'python3',
				expect.arrayContaining(['stats', '--nids', '[100]']),
				expect.any(Object),
			);
		});

		test('cli invocation with specific backend', async () => {
			settings.backend = 'direct';
			const mockChild = createMockChildProcess();
			(spawn as jest.Mock).mockReturnValue(mockChild);

			const promise = client.invokeCLI('/anki/stats', { nids: [100] });
			mockChild.stdout.emit('data', '{}');
			mockChild.emit('close', 0);

			await promise;
			expect(spawn).toHaveBeenCalledWith(
				'python3',
				expect.arrayContaining(['--backend', 'direct']),
				expect.any(Object),
			);
		});

		test('cli failure handling', async () => {
			const mockChild = createMockChildProcess();
			(spawn as jest.Mock).mockReturnValue(mockChild);

			const promise = client.invokeCLI('/anki/stats', { nids: [100] });

			mockChild.stderr.emit('data', 'Critical Error');
			mockChild.emit('close', 1);

			await expect(promise).rejects.toThrow('CLI Error (1): Critical Error');
		});

		test('cli failure handling in JSON fallback parsing', async () => {
			const mockChild = createMockChildProcess();
			(spawn as jest.Mock).mockReturnValue(mockChild);

			const promise = client.invokeCLI('/anki/stats', { nids: [100] });

			// Brackets exist but content is invalid JSON
			mockChild.stdout.emit('data', 'some logs { invalid json } more logs');
			mockChild.emit('close', 0);

			const result = await promise;
			// Should return the raw output since JSON parsing failed
			expect(result).toEqual({ output: 'some logs { invalid json } more logs' });
		});

		test('cli invocation with .py script path', async () => {
			settings.arete_script_path = '/path/to/main.py';
			const mockChild = createMockChildProcess();
			(spawn as jest.Mock).mockReturnValue(mockChild);

			const promise = client.invokeCLI('/anki/stats', { nids: [100] });
			mockChild.stdout.emit('data', '{}');
			mockChild.emit('close', 0);

			await promise;
			expect(spawn).toHaveBeenCalledWith(
				'python3',
				expect.arrayContaining(['-m', 'arete']),
				expect.objectContaining({
					env: expect.objectContaining({ PYTHONPATH: '/path' }),
				}),
			);
		});

		test('cli endpoint mapping for model templates', async () => {
			const mockChild = createMockChildProcess();
			(spawn as jest.Mock).mockReturnValue(mockChild);

			const promise = client.modelTemplates('Basic');

			mockChild.stdout.emit('data', '{}');
			mockChild.emit('close', 0);

			await promise;
			expect(spawn).toHaveBeenCalledWith(
				'python3',
				expect.arrayContaining(['models-templates', 'Basic']),
				expect.any(Object),
			);
		});
	});

	describe('modelStyling', () => {
		test('server mode styling fetch', async () => {
			settings.execution_mode = 'server';
			(requestUrl as jest.Mock).mockResolvedValue({
				status: 200,
				json: { css: 'body { color: red; }' },
			});

			const css = await client.modelStyling('Basic');
			expect(css).toBe('body { color: red; }');
		});

		test('server mode styling fetch error (status 500)', async () => {
			settings.execution_mode = 'server';
			(requestUrl as jest.Mock).mockResolvedValue({
				status: 500,
				json: null,
			});

			const css = await client.modelStyling('Basic');
			expect(css).toBe('');
		});

		test('cli mode styling fetch', async () => {
			settings.execution_mode = 'cli';
			const mockChild = createMockChildProcess();
			(spawn as jest.Mock).mockReturnValue(mockChild);

			const promise = client.modelStyling('Basic');
			mockChild.stdout.emit('data', JSON.stringify({ css: 'body { color: blue; }' }));
			mockChild.emit('close', 0);

			const css = await promise;
			expect(css).toBe('body { color: blue; }');
		});
	});

	describe('modelTemplates', () => {
		test('server mode templates fetch', async () => {
			settings.execution_mode = 'server';
			(requestUrl as jest.Mock).mockResolvedValue({
				status: 200,
				json: { Basic: { Front: '{{Front}}', Back: '{{Back}}' } },
			});

			const templates = await client.modelTemplates('Basic');
			expect(templates).toEqual({ Basic: { Front: '{{Front}}', Back: '{{Back}}' } });
		});

		test('server mode templates fetch error (status 404)', async () => {
			settings.execution_mode = 'server';
			(requestUrl as jest.Mock).mockResolvedValue({
				status: 404,
				json: null,
			});

			const templates = await client.modelTemplates('Basic');
			expect(templates).toEqual({});
		});

		test('cli mode templates fetch', async () => {
			settings.execution_mode = 'cli';
			const mockChild = createMockChildProcess();
			(spawn as jest.Mock).mockReturnValue(mockChild);

			const promise = client.modelTemplates('Basic');
			mockChild.stdout.emit('data', JSON.stringify({ Basic: { Front: 'F', Back: 'B' } }));
			mockChild.emit('close', 0);

			const templates = await promise;
			expect(templates).toEqual({ Basic: { Front: 'F', Back: 'B' } });
		});
	});

});
