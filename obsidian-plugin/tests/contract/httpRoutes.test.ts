// Every HTTP path the plugin calls exists in the Python server (rule B3).
// tests/test-setup.ts mocks fs, so the real module is taken explicitly (rule Q3).
import * as path from 'path';

const fs = jest.requireActual('fs') as typeof import('fs');

const plugin = path.resolve(__dirname, '../..');
const serverFile = path.join(plugin, '../src/arete/interface/http_server.py');

// `/anki/models/${name}/styling`, `/anki/models/{name}/styling` and `/anki/models/<x>/styling` compare equal.
const norm = (p: string) => p.replace(/\$\{[^}]+\}|\{[A-Za-z_]+\}/g, '{param}');

function walk(dir: string): string[] {
	return fs.readdirSync(dir, { withFileTypes: true }).flatMap((e) => {
		const full = path.join(dir, e.name);
		return e.isDirectory() ? walk(full) : full.endsWith('.ts') ? [full] : [];
	});
}

/** Routes the server declares: `@app.get("/x")`, `@app.post("/x")` and the other verbs. */
function serverRoutes(py: string): Set<string> {
	return new Set(
		[...py.matchAll(/@app\.(?:get|post|put|patch|delete)\(\s*["']([^"']+)["']/g)].map((m) =>
			norm(m[1]),
		),
	);
}

/** Paths the client calls: quoted or template literals that start with a server path segment, the
 *  first argument of `invoke`/`invokeServer`, and `<host>:${port}/path` URLs. */
function clientPaths(text: string, segments: string[]): Set<string> {
	const seg = `(?:${segments.join('|')})`;
	const out = new Set<string>();
	const add = (p: string) => {
		if (!p.endsWith('/')) out.add(norm(p)); // a trailing '/' is a prefix test, not a route
	};
	for (const m of text.matchAll(new RegExp(`'(/${seg}[^'?]*)'`, 'g'))) add(m[1]);
	for (const m of text.matchAll(new RegExp('`(/' + seg + '[^`?]*)`', 'g'))) add(m[1]);
	for (const m of text.matchAll(/\.invoke(?:Server)?\(\s*['"`](\/[^'"`?]*)['"`]/g)) add(m[1]);
	for (const m of text.matchAll(/:\$\{[^}]+\}(\/[^`'"?]*)[`'"]/g)) add(m[1]);
	return out;
}

describe('plugin HTTP calls against http_server.py', () => {
	const py = fs.readFileSync(serverFile, 'utf8');
	const server = serverRoutes(py);
	const segments = [...new Set([...server].map((r) => r.split('/')[1]))];
	const text = walk(path.join(plugin, 'src'))
		.map((f) => fs.readFileSync(f, 'utf8'))
		.join('\n');
	const client = clientPaths(text, segments);

	test('the server declares routes (the regex still reads the file)', () => {
		expect(server.size).toBeGreaterThanOrEqual(10);
	});

	test('the plugin calls routes (the regex still reads the client)', () => {
		expect(client.size).toBeGreaterThanOrEqual(10);
	});

	test('every client path is a server route', () => {
		expect([...client].filter((e) => !server.has(e)).sort()).toEqual([]);
	});

	test('a path that is not a route is caught', () => {
		const fake = clientPaths("this.invoke('/anki/zz-missing', {});", segments);
		expect([...fake].filter((e) => !server.has(e))).toEqual(['/anki/zz-missing']);
	});
});
