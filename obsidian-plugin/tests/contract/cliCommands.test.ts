// Every `arete` command line the plugin builds names a registered CLI command and only the
// options that command declares (rule B3). The commands are read from the Typer sources;
// the command lines come from the real client and services, with every optional flag on.
// tests/test-setup.ts mocks fs, so the real module is taken explicitly (rule Q3).
import '../test-setup';
import * as path from 'path';
import { spawn } from 'child_process';
import { App } from 'obsidian';
import { createMockChildProcess } from '../test-setup';
import { DEFAULT_SETTINGS } from '@/domain/settings';
import { AreteClient } from '@infrastructure/arete/AreteClient';
import { CheckService } from '@infrastructure/arete/CheckService';
import { SyncService } from '@infrastructure/arete/SyncService';
import { resolvePythonCommand } from '@infrastructure/arete/PythonProcess';

// SyncService's log writes through fs.promises, which the shared fs mock lacks.
jest.mock('fs', () => ({
	promises: {
		appendFile: jest.fn().mockResolvedValue(undefined),
		stat: jest.fn().mockRejectedValue(new Error('ENOENT')),
		readFile: jest.fn(),
		writeFile: jest.fn(),
	},
}));

const fs = jest.requireActual('fs') as typeof import('fs');
const iface = path.resolve(__dirname, '../../../src/arete/interface');

interface Command {
	options: Set<string>;
	/** Options that take no value. */
	flags: Set<string>;
	args: number;
}

interface Cli {
	root: Command;
	commands: Map<string, Command>; // 'sync', 'anki stats', ...
}

/** The parameters of a Typer function: one chunk per `    name: ...` parameter. */
function parseParams(block: string): Command {
	const cmd: Command = { options: new Set(), flags: new Set(), args: 0 };
	const chunks = block.split(/\n(?= {4}\w+:)/).filter((c) => /^\s*\w+:/.test(c));
	for (const chunk of chunks) {
		const name = chunk.trim().split(':')[0];
		if (/typer\.Context/.test(chunk)) continue;
		if (/typer\.Argument\(/.test(chunk)) {
			cmd.args++;
			continue;
		}
		const isBool = /^\s*\w+:\s*(?:Annotated\[\s*)?bool\b/.test(chunk);
		const isFlag = isBool || /count=True/.test(chunk);
		const declared = chunk.match(/typer\.Option\(\s*((?:"-[^"]*"\s*,\s*)*"-[^"]*")/);
		let names: string[];
		if (declared) {
			names = [...declared[1].matchAll(/"(-[^"]*)"/g)].flatMap((m) => m[1].split('/'));
		} else {
			const long = `--${name.replace(/_/g, '-')}`;
			names = isBool ? [long, `--no-${long.slice(2)}`] : [long];
		}
		for (const n of names) {
			cmd.options.add(n);
			if (isFlag) cmd.flags.add(n);
		}
	}
	return cmd;
}

/** The command tree the Typer apps in src/arete/interface register. */
function readCli(): Cli {
	const files = fs.readdirSync(iface).filter((f) => f.endsWith('.py'));
	const sources = files.map((f) => fs.readFileSync(path.join(iface, f), 'utf8'));
	const all = sources.join('\n');

	// Typer variable -> the name it is mounted under at the root ('' for the root app).
	const mount = new Map<string, string>([['app', '']]);
	for (const m of all.matchAll(/app\.add_typer\(\s*(\w+)\s*,\s*name="([\w-]+)"/g)) {
		mount.set(m[1], m[2]);
	}

	const cli: Cli = {
		root: { options: new Set(), flags: new Set(), args: 0 },
		commands: new Map(),
	};
	// The parameters run to the `)` that ends the signature: the one followed by `:` and a
	// newline, so `def mcp():` does not swallow the next function's parameters.
	const decorated =
		/@(\w+)\.(command|callback)\(([^)]*)\)\s*\ndef (\w+)\(((?:[^)]|\)(?!(?:\s*->[^:\n]*)?:\s*\n))*)\)(?:\s*->[^:\n]*)?:\s*\n/g;
	for (const m of all.matchAll(decorated)) {
		const [, owner, kind, decoratorArgs, fn, params] = m;
		const group = mount.get(owner);
		if (group === undefined) continue;
		const parsed = parseParams(params);
		if (kind === 'callback') {
			if (group === '') cli.root = parsed;
			continue;
		}
		const explicit = decoratorArgs.match(/^\s*"([\w-]+)"/);
		const name = explicit ? explicit[1] : fn.replace(/_/g, '-');
		cli.commands.set(group ? `${group} ${name}` : name, parsed);
	}
	return cli;
}

/** What is wrong with `argv` (the arguments after `arete`) against `cli`; empty when nothing. */
function check(cli: Cli, argv: string[]): string[] {
	const errors: string[] = [];
	let i = 0;
	while (i < argv.length && argv[i].startsWith('-')) {
		if (!cli.root.options.has(argv[i])) errors.push(`unknown global option ${argv[i]}`);
		i += cli.root.flags.has(argv[i]) ? 1 : 2;
	}
	let key = argv[i++];
	if (!cli.commands.has(key) && i < argv.length) key = `${key} ${argv[i++]}`;
	const cmd = cli.commands.get(key);
	if (!cmd) return [...errors, `no command "arete ${key}"`];

	let positional = 0;
	while (i < argv.length) {
		const tok = argv[i];
		if (tok.startsWith('--')) {
			if (!cmd.options.has(tok)) errors.push(`"arete ${key}" has no option ${tok}`);
			i += cmd.flags.has(tok) ? 1 : 2;
		} else {
			positional++;
			i++;
		}
	}
	if (positional > cmd.args) {
		errors.push(`"arete ${key}" takes ${cmd.args} arguments, got ${positional}`);
	}
	return errors;
}

/** Every command line the plugin spawns, with every optional setting turned on. */
async function pluginCommandLines(): Promise<{ lines: string[][]; clientMethods: string[] }> {
	const settings = {
		...DEFAULT_SETTINGS,
		python_path: 'python3',
		backend: 'ankiconnect' as const,
		anki_connect_url: 'http://anki:9999',
		anki_media_dir: '/media',
		workers: 4,
		debug_mode: true,
	};
	const children: any[] = [];
	(spawn as jest.Mock).mockImplementation(() => {
		const child = createMockChildProcess();
		children.push(child);
		return child;
	});

	const client = new AreteClient(settings, '/vault');
	const calls: Record<string, () => Promise<unknown>> = {
		getCardStats: () => client.getCardStats([1]),
		suspendCards: () => client.suspendCards([1]),
		unsuspendCards: () => client.unsuspendCards([1]),
		browse: () => client.browse('nid:1'),
		modelStyling: () => client.modelStyling('Basic'),
		modelTemplates: () => client.modelTemplates('Basic'),
		fetchGraph: () => client.fetchGraph('/vault'),
		getDeckNames: () => client.getDeckNames(),
		buildStudyQueue: () => client.buildStudyQueue('Math', 2, 50),
		createQueueDeck: () => client.createQueueDeck(['arete_A']),
	};
	const app = new App();
	(app.vault.adapter as any).getBasePath = jest.fn().mockReturnValue('/vault');
	const check = new CheckService(app, settings);
	const sync = new SyncService(app, settings, { dir: 'plugin' });

	const pending = [
		...Object.values(calls).map((call) => call()),
		check.getCheckResult('/vault/A.md'),
		check.runFix('/vault/A.md'),
		sync.runSync(true, '/vault/A.md', true, jest.fn()),
	];
	for (const child of children) {
		child.stdout.emit('data', '{}');
		child.emit('close', 0);
	}
	await Promise.allSettled(pending);

	const prefix = resolvePythonCommand(settings).args.length;
	const lines = (spawn as jest.Mock).mock.calls.map((c) => (c[1] as string[]).slice(prefix));
	return { lines, clientMethods: Object.keys(calls) };
}

describe('plugin command lines against the registered CLI', () => {
	const cli = readCli();

	beforeEach(() => jest.clearAllMocks());

	test('the Typer sources still parse into commands', () => {
		for (const name of ['sync', 'queue', 'anki stats', 'graph export', 'vault check']) {
			expect(cli.commands.has(name)).toBe(true);
		}
		expect(cli.commands.get('anki stats')!.options).toContain('--anki-connect-url');
		expect(cli.commands.get('vault check')!.flags).toContain('--json');
		expect(cli.root.flags).toContain('--verbose');
	});

	test('every client method that spawns is covered here', async () => {
		const { clientMethods } = await pluginCommandLines();
		const helpers = ['constructor', 'dispose', 'runAnki', 'backendArgs', 'runCLI'];
		const methods = Object.getOwnPropertyNames(AreteClient.prototype).filter(
			(m) => !helpers.includes(m),
		);
		expect(clientMethods.sort()).toEqual(methods.sort());
	});

	test('every command line names a registered command and its own options', async () => {
		jest.spyOn(console, 'error').mockImplementation(() => undefined);
		const { lines } = await pluginCommandLines();
		expect(lines).toHaveLength(13);
		const errors = lines.flatMap((argv) =>
			check(cli, argv).map((e) => `${argv.join(' ')}: ${e}`),
		);
		expect(errors).toEqual([]);
	});

	test('a renamed command, a renamed option and a stray argument are caught', () => {
		expect(check(cli, ['anki', 'model-style', 'Basic'])).toEqual([
			'no command "arete anki model-style"',
		]);
		expect(check(cli, ['anki', 'stats', '--note-ids', '[1]'])).toEqual([
			'"arete anki stats" has no option --note-ids',
		]);
		expect(check(cli, ['anki', 'decks', 'extra'])).toEqual([
			'"arete anki decks" takes 0 arguments, got 1',
		]);
		expect(check(cli, ['--verbose', 'sync', '--prune', '/v'])).toEqual([]);
	});
});
