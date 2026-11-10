// N10: manifest.json's minAppVersion must be at or above the highest @since among the
// obsidian.d.ts declarations that src/ references.
// Usage: node scripts/min-app-version.cjs [--list]      (run from obsidian-plugin/)
// Exit 0 when the manifest is high enough, 1 when it is too low, 2 when the script cannot run.
//
// An API is "referenced" when an identifier in src/ resolves to a declaration in obsidian.d.ts that
// carries a @since tag. For a call or `new`, the overload the compiler picked is used, so a newer
// overload that the plugin does not call does not raise the floor.
const fs = require('fs');
const path = require('path');

const dir = path.resolve(__dirname, '..');
const ts = require(path.join(dir, 'node_modules/typescript'));
const list = process.argv.includes('--list');

const cmp = (a, b) => {
	const x = a.split('.').map(Number);
	const y = b.split('.').map(Number);
	for (let i = 0; i < 3; i++) if ((x[i] || 0) !== (y[i] || 0)) return (x[i] || 0) - (y[i] || 0);
	return 0;
};
const isObsidian = (d) =>
	d.getSourceFile().fileName.endsWith('node_modules/obsidian/obsidian.d.ts');
const sinceOf = (d) => {
	const tag = ts.getJSDocTags(d).find((t) => t.tagName.text === 'since');
	if (!tag) return null;
	const text =
		typeof tag.comment === 'string' ? tag.comment : ts.getTextOfJSDocComment(tag.comment);
	return text ? text.trim() : null;
};

const cfgPath = path.join(dir, 'tsconfig.json');
const cfg = ts.parseJsonConfigFileContent(
	ts.readConfigFile(cfgPath, ts.sys.readFile).config,
	ts.sys,
	dir,
);
const srcRoot = path.join(dir, 'src') + path.sep;
const program = ts.createProgram({
	rootNames: cfg.fileNames.filter((f) => f.startsWith(srcRoot)),
	options: { ...cfg.options, noEmit: true },
});
const checker = program.getTypeChecker();

const uses = new Map(); // "name@since" -> first use
const record = (sf, node, name, since) => {
	const key = `${name}@${since}`;
	if (uses.has(key)) return;
	const { line } = sf.getLineAndCharacterOfPosition(node.getStart(sf));
	uses.set(key, `${path.relative(dir, sf.fileName)}:${line + 1}`);
};

for (const sf of program.getSourceFiles()) {
	if (!sf.fileName.startsWith(srcRoot)) continue;
	const handled = new Set(); // callee identifiers settled through their resolved overload
	(function visit(n) {
		if (ts.isCallExpression(n) || ts.isNewExpression(n)) {
			const decl = checker.getResolvedSignature(n)?.declaration;
			const callee = ts.isPropertyAccessExpression(n.expression)
				? n.expression.name
				: n.expression;
			if (decl && isObsidian(decl) && ts.isIdentifier(callee)) {
				handled.add(callee);
				const since = sinceOf(decl);
				if (since) record(sf, callee, callee.text, since);
				// An untagged overload falls back to the symbol's tagged declarations below.
				else handled.delete(callee);
			}
		}
		if (ts.isIdentifier(n) && !handled.has(n)) {
			const decls = checker.getSymbolAtLocation(n)?.declarations?.filter(isObsidian) ?? [];
			for (const d of decls) {
				const since = sinceOf(d);
				if (since) record(sf, n, n.text, since);
			}
		}
		ts.forEachChild(n, visit);
	})(sf);
}

const rows = [...uses.entries()].map(([k, at]) => {
	const i = k.lastIndexOf('@');
	return { name: k.slice(0, i), since: k.slice(i + 1), at };
});
rows.sort((a, b) => cmp(b.since, a.since));
if (rows.length === 0) {
	console.error(
		'min-app-version: no referenced Obsidian API carries @since; the check cannot run.',
	);
	process.exit(2);
}
const manifest = JSON.parse(fs.readFileSync(path.join(dir, 'manifest.json'), 'utf8')).minAppVersion;
const needed = rows[0].since;
console.log(`referenced Obsidian APIs with @since: ${rows.length}`);
for (const r of rows.slice(0, list ? rows.length : 5))
	console.log(`${r.since.padEnd(8)} ${r.name.padEnd(28)} ${r.at}`);
if (cmp(manifest, needed) < 0) {
	console.error(
		`FAIL: manifest.json minAppVersion ${manifest} is below ${needed} (${rows[0].name} at ${rows[0].at}).`,
	);
	process.exit(1);
}
console.log(`ok: manifest.json minAppVersion ${manifest} >= ${needed}`);
