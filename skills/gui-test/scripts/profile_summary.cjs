// Summarise a CDP Profiler.stop result: self time by script, and inclusive time of
// functions whose script URL matches a filter (default: the arete plugin).
// usage: node prof.cjs <profile.json> [urlFilter]
const fs = require('fs');
const raw = JSON.parse(fs.readFileSync(process.argv[2], 'utf8'));
const prof = raw.profile || raw.result?.profile || raw;
const filter = process.argv[3] || 'arete';
const nodes = new Map(prof.nodes.map((n) => [n.id, n]));
const parent = new Map();
for (const n of prof.nodes) for (const c of n.children || []) parent.set(c, n.id);
const self = new Map();
for (let i = 0; i < prof.samples.length; i++) {
	const id = prof.samples[i];
	const dt = (prof.timeDeltas[i + 1] ?? prof.timeDeltas[i] ?? 0) / 1000;
	self.set(id, (self.get(id) || 0) + dt);
}
const total = [...self.values()].reduce((a, b) => a + b, 0);
const byUrl = new Map();
const key = (n) => {
	const u = n.callFrame.url || '(native)';
	return u.includes(filter) ? 'PLUGIN ' + filter : u.replace(/^.*\//, '').slice(0, 40) || '(native)';
};
for (const [id, t] of self) {
	const k = nodes.get(id).callFrame.functionName === '(idle)' ? '(idle)' : key(nodes.get(id));
	byUrl.set(k, (byUrl.get(k) || 0) + t);
}
// inclusive time for plugin functions: a sample counts once per distinct plugin frame on its stack
const incl = new Map();
for (const [id, t] of self) {
	const seen = new Set();
	let cur = id;
	while (cur !== undefined) {
		const n = nodes.get(cur);
		if ((n.callFrame.url || '').includes(filter)) {
			const k = `${n.callFrame.functionName || '(anon)'}@${n.callFrame.lineNumber}:${n.callFrame.columnNumber}`;
			if (!seen.has(k)) {
				seen.add(k);
				incl.set(k, (incl.get(k) || 0) + t);
			}
		}
		cur = parent.get(cur);
	}
}
const pluginAny = (() => {
	let s = 0;
	for (const [id, t] of self) {
		let cur = id;
		while (cur !== undefined) {
			if ((nodes.get(cur).callFrame.url || '').includes(filter)) {
				s += t;
				break;
			}
			cur = parent.get(cur);
		}
	}
	return s;
})();
console.log(`total sampled ms: ${total.toFixed(1)}; samples under any plugin frame (inclusive): ${pluginAny.toFixed(1)} ms`);
console.log('self time by script:');
for (const [k, v] of [...byUrl].sort((a, b) => b[1] - a[1]).slice(0, 10)) console.log(`  ${v.toFixed(1).padStart(8)} ms  ${k}`);
console.log('inclusive time, plugin functions:');
for (const [k, v] of [...incl].sort((a, b) => b[1] - a[1]).slice(0, 15)) console.log(`  ${v.toFixed(1).padStart(8)} ms  ${k}`);
