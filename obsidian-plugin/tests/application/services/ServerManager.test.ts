import '../../test-setup';
import { App, Notice, requestUrl } from 'obsidian';
import { spawn } from 'child_process';
import { ServerManager } from '@application/services/ServerManager';
import { createMockChildProcess } from '../../test-setup';

describe('ServerManager', () => {
	let service: ServerManager;
	let app: App;
	let settings: any;
	beforeEach(() => {
		jest.clearAllMocks();
		jest.useFakeTimers();
		app = new App();
		settings = {
			execution_mode: 'server',
			python_path: 'python3',
			server_port: 8777,
			project_root: '/mock/project',
		};
		service = new ServerManager(app, settings);
	});

	afterEach(() => {
		jest.useRealTimers();
	});

	describe('start', () => {
		test('returns early if not in server mode', async () => {
			settings.execution_mode = 'cli';
			await service.start();
			expect(requestUrl).not.toHaveBeenCalled();
		});

		test('returns early if already running and healthy', async () => {
			(requestUrl as jest.Mock).mockResolvedValue({ status: 200 });

			const startPromise = service.start();
			jest.advanceTimersByTime(100);
			await startPromise;

			expect(spawn).not.toHaveBeenCalled();
		});

		test('spawns and polls until healthy', async () => {
			// 1. Initial health check fails
			(requestUrl as jest.Mock)
				.mockResolvedValueOnce({ status: 500 }) // health check 1 (check if running)
				.mockResolvedValueOnce({ status: 500 }) // health check 2 (after spawn)
				.mockResolvedValueOnce({ status: 200 }); // health check 3 (success)

			const mockChild = createMockChildProcess();
			(spawn as jest.Mock).mockReturnValue(mockChild);

			const startPromise = service.start();

			// Needs multiple turns to progress through the while loop
			for (let i = 0; i < 3; i++) {
				await jest.advanceTimersByTimeAsync(1000);
			}

			await startPromise;
			expect(spawn).toHaveBeenCalled();
			expect(Notice).toHaveBeenCalledWith(expect.stringContaining('started successfully'));
		});

		test('force restart stops existing server', async () => {
			const stopSpy = jest.spyOn(service, 'stop').mockResolvedValue(undefined);
			(requestUrl as jest.Mock)
				.mockResolvedValueOnce({ status: 200 }) // initial health (running)
				.mockResolvedValueOnce({ status: 500 }) // health check after stop (not running)
				.mockResolvedValueOnce({ status: 200 }); // health check after spawn (ready)

			const mockChild = createMockChildProcess();
			(spawn as jest.Mock).mockReturnValue(mockChild);

			const startPromise = service.start(true);
			await jest.advanceTimersByTimeAsync(1500); // Wait for stop and first poll
			await jest.advanceTimersByTimeAsync(1500); // Wait for second poll (success)
			await startPromise;

			expect(stopSpy).toHaveBeenCalled();
			expect(spawn).toHaveBeenCalled();
		});

		test('handles spawn failure', async () => {
			(requestUrl as jest.Mock).mockResolvedValue({ status: 500 });
			(spawn as jest.Mock).mockImplementation(() => {
				throw new Error('Spawn failed');
			});

			await service.start();
			expect(Notice).toHaveBeenCalledWith('Failed to spawn Arete Server.');
		});

		test('times out if health check never passes', async () => {
			(requestUrl as jest.Mock).mockResolvedValue({ status: 500 });
			const mockChild = createMockChildProcess();
			(spawn as jest.Mock).mockReturnValue(mockChild);

			const startPromise = service.start();

			for (let i = 0; i < 16; i++) {
				await jest.advanceTimersByTimeAsync(1000);
			}

			await startPromise;
			expect(Notice).toHaveBeenCalledWith(expect.stringContaining('Failed to connect'));
		});

		test('returns same startPromise if already starting', () => {
			(requestUrl as jest.Mock).mockReturnValue(
				new Promise(() => {
					/* never resolves */
				}),
			); // Never resolves
			const p1 = service.start();
			const p2 = service.start();
			expect(p1).toBe(p2);
		});
	});

	describe('restart', () => {
		test('calls start with forceRestart=true', async () => {
			const startSpy = jest.spyOn(service, 'start').mockResolvedValue(undefined);
			await service.restart();
			expect(startSpy).toHaveBeenCalledWith(true);
		});
	});

	describe('stop', () => {
		test('terminates process and sends shutdown signal', async () => {
			const mockChild = createMockChildProcess();
			(spawn as jest.Mock).mockReturnValue(mockChild);

			// Setup a process by starting
			(requestUrl as jest.Mock).mockResolvedValue({ status: 500 });
			const startPromise = service.start();
			await jest.advanceTimersByTimeAsync(100);

			await service.stop();
			expect(requestUrl).toHaveBeenCalledWith(
				expect.objectContaining({ url: expect.stringContaining('/shutdown') }),
			);
			expect(mockChild.kill).toHaveBeenCalled();

			// Cleanup
			(requestUrl as jest.Mock).mockResolvedValue({ status: 200 });
			await jest.advanceTimersByTimeAsync(1500);
			await startPromise;
		});
	});

	describe('spawnServer logic', () => {
		test('handles .py script path', async () => {
			settings.arete_script_path = '/path/to/main.py';
			const mockChild = createMockChildProcess();
			(spawn as jest.Mock).mockReturnValue(mockChild);

			(requestUrl as jest.Mock).mockResolvedValue({ status: 500 });
			const startPromise = service.start();
			await jest.advanceTimersByTimeAsync(100); // Allow first checkHealth to resolve

			expect(spawn).toHaveBeenCalledWith(
				'python3',
				expect.arrayContaining(['-m', 'arete', 'server']),
				expect.any(Object),
			);
			const spawnCall = (spawn as jest.Mock).mock.calls[0];
			const env = spawnCall[2].env;
			expect(env.PYTHONPATH).toContain('/path');
			// Cleanup
			(requestUrl as jest.Mock).mockResolvedValue({ status: 200 });
			await jest.advanceTimersByTimeAsync(1500);
			await startPromise;
		});

		test('injects -m arete if missing from python_path', async () => {
			settings.python_path = '/usr/bin/python3';
			const mockChild = createMockChildProcess();
			(spawn as jest.Mock).mockReturnValue(mockChild);

			(requestUrl as jest.Mock).mockResolvedValue({ status: 500 });
			const startPromise = service.start();
			await jest.advanceTimersByTimeAsync(100);

			expect(spawn).toHaveBeenCalledWith(
				'/usr/bin/python3',
				expect.arrayContaining(['-m', 'arete', 'server']),
				expect.any(Object),
			);
			// Cleanup
			(requestUrl as jest.Mock).mockResolvedValue({ status: 200 });
			await jest.advanceTimersByTimeAsync(1500);
			await startPromise;
		});

		test('logs stdout and stderr from server', async () => {
			const mockChild = createMockChildProcess();
			(spawn as jest.Mock).mockReturnValue(mockChild);
			(requestUrl as jest.Mock).mockResolvedValue({ status: 500 });
			const logSpy = jest.spyOn(console, 'log').mockImplementation();
			const errorSpy = jest.spyOn(console, 'error').mockImplementation();

			const startPromise = service.start();
			await jest.advanceTimersByTimeAsync(100);

			mockChild.stdout.emit('data', Buffer.from('Server Logged info'));
			mockChild.stderr.emit('data', Buffer.from('Server Logged error'));

			expect(logSpy).toHaveBeenCalledWith(expect.stringContaining('Server Logged info'));
			expect(errorSpy).toHaveBeenCalledWith(expect.stringContaining('Server Logged error'));
			// Cleanup
			(requestUrl as jest.Mock).mockResolvedValue({ status: 200 });
			await jest.advanceTimersByTimeAsync(1500);
			await startPromise;
		});

		test('handles process error event', async () => {
			const mockChild = createMockChildProcess();
			(spawn as jest.Mock).mockReturnValue(mockChild);
			(requestUrl as jest.Mock).mockResolvedValue({ status: 500 });
			const errorSpy = jest.spyOn(console, 'error').mockImplementation();

			const startPromise = service.start();
			await jest.advanceTimersByTimeAsync(100);

			mockChild.emit('error', new Error('Spawn error event'));
			expect(errorSpy).toHaveBeenCalledWith(
				expect.stringContaining('Server spawn error'),
				expect.any(Error),
			);
			// Cleanup
			(requestUrl as jest.Mock).mockResolvedValue({ status: 200 });
			await jest.advanceTimersByTimeAsync(1500);
			await startPromise;
		});
	});
});
