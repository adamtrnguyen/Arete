import { DependencyGraphBuilder } from '@/domain/graph/types';

/** The scan `getDependents` replaced: every `requires` entry, in key insertion order. */
function scanDependents(builder: DependencyGraphBuilder, cardId: string): string[] {
	const dependents: string[] = [];
	for (const [id, prereqs] of (builder as any).requires as Map<string, string[]>) {
		if (prereqs.includes(cardId)) dependents.push(id);
	}
	return dependents;
}

/** Deterministic PRNG so a failure reproduces. */
function rng(seed: number): () => number {
	let s = seed >>> 0;
	return () => {
		s = (s * 1664525 + 1013904223) >>> 0;
		return s / 2 ** 32;
	};
}

describe('DependencyGraphBuilder.getDependents', () => {
	it('lists dependents in requires-key order, not edge order', () => {
		const b = new DependencyGraphBuilder();
		b.addNode({ id: 'A', title: 'a', filePath: 'a.md', lineNumber: 1 });
		b.addNode({ id: 'B', title: 'b', filePath: 'b.md', lineNumber: 1 });
		b.addRequires('B', 'X');
		b.addRequires('A', 'X');
		b.addRequires('Z', 'X'); // a source never added as a node
		expect(b.getDependents('X')).toEqual(['A', 'B', 'Z']);
		expect(b.getDependents('X')).toEqual(scanDependents(b, 'X'));
		expect(b.getDependents('missing')).toEqual([]);
	});

	it('matches the full scan on random graphs, across interleaved edits and queries', () => {
		for (let seed = 1; seed <= 25; seed++) {
			const rand = rng(seed);
			const b = new DependencyGraphBuilder();
			const ids = Array.from({ length: 30 }, (_, i) => `arete_${i}`);
			for (let step = 0; step < 200; step++) {
				const r = rand();
				const pick = () => ids[Math.floor(rand() * ids.length)];
				if (r < 0.2) {
					const id = pick();
					b.addNode({ id, title: id, filePath: `${id}.md`, lineNumber: 1 });
				} else if (r < 0.8) {
					b.addRequires(pick(), pick());
				} else {
					const id = pick();
					expect(b.getDependents(id)).toEqual(scanDependents(b, id));
				}
			}
			for (const id of ids) expect(b.getDependents(id)).toEqual(scanDependents(b, id));
		}
	});

	it('returns a fresh array, so a caller cannot corrupt the index', () => {
		const b = new DependencyGraphBuilder();
		b.addRequires('A', 'X');
		b.getDependents('X').push('junk');
		expect(b.getDependents('X')).toEqual(['A']);
	});

	it('scans the requires map once per change, not once per query', () => {
		const b = new DependencyGraphBuilder();
		for (let i = 0; i < 50; i++) b.addRequires(`c${i}`, `c${i + 1}`);
		let scans = 0;
		class CountingMap<K, V> extends Map<K, V> {
			[Symbol.iterator]() {
				scans++;
				return super[Symbol.iterator]();
			}
		}
		(b as any).requires = new CountingMap((b as any).requires);
		(b as any).dependents = null;
		for (let i = 0; i < 50; i++) b.getDependents(`c${i}`);
		expect(scans).toBe(1);
		b.addRequires('c0', 'c7');
		expect(b.getDependents('c7')).toEqual(['c0', 'c6']);
		expect(scans).toBe(2);
	});
});
