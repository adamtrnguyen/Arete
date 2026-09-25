import '../../test-setup';
import { DependencyResolver } from '@application/services/DependencyResolver';
import { GraphExport, GraphSource } from '@/domain/graph/types';

// PL1: the resolver draws the graph Python resolved; it no longer parses deps itself.
const GRAPH: GraphExport = {
	nodes: [
		{ id: 'arete_A', title: 'algebra', file: 'Algebra.md', line: 5 },
		{ id: 'arete_M', title: 'math intro', file: 'math/Intro.md', line: 5 },
		{ id: 'arete_C', title: 'calculus', file: 'Calculus.md', line: 5 },
		{ id: 'arete_R', title: 'related', file: 'Rel.md', line: 5 },
	],
	requires: [
		['arete_A', 'arete_M'], // algebra requires math intro
		['arete_C', 'arete_A'], // calculus requires algebra
	],
	related: [['arete_A', 'arete_R']],
	cycles: [],
	unresolved_refs: {
		arete_A: ['Intro (ambiguous: bio/Intro, math/Intro; use a folder/name ref)'],
	},
	duplicate_ids: {},
	skipped_files: [],
};

function source(graph: GraphExport = GRAPH): GraphSource & { calls: string[] } {
	const calls: string[] = [];
	return {
		calls,
		fetchGraph: jest.fn(async (root: string) => {
			calls.push(root);
			return graph;
		}),
	};
}

describe('DependencyResolver', () => {
	it('asks the source for the vault it was built for', async () => {
		const src = source();
		await new DependencyResolver(src, '/vault').buildGraph();
		expect(src.calls).toEqual(['/vault']);
	});

	it('fetches once until invalidated, then again', async () => {
		const src = source();
		const r = new DependencyResolver(src, '/vault');
		await r.buildGraph();
		await r.buildGraph();
		expect(src.calls).toHaveLength(1);
		r.invalidate();
		await r.buildGraph();
		expect(src.calls).toHaveLength(2);
		await r.buildGraph(true);
		expect(src.calls).toHaveLength(3);
	});

	it('retries after a failed fetch instead of caching the failure', async () => {
		const src = source();
		(src.fetchGraph as jest.Mock).mockRejectedValueOnce(new Error('arete not found'));
		const r = new DependencyResolver(src, '/vault');
		await expect(r.buildGraph()).rejects.toThrow('arete not found');
		await r.buildGraph();
		expect(r.getGlobalGraph().cards).toHaveLength(4);
	});

	it('walks prerequisites, dependents and related from the exported edges', async () => {
		const r = new DependencyResolver(source(), '/vault');
		await r.buildGraph();
		const local = r.getLocalGraph('arete_A', 2)!;
		expect(local.center.filePath).toBe('Algebra.md');
		expect(local.prerequisites.map((n) => n.id)).toEqual(['arete_M']);
		expect(local.dependents.map((n) => n.id)).toEqual(['arete_C']);
		expect(local.related.map((n) => n.id)).toEqual(['arete_R']);
		expect(local.links).toContainEqual({
			type: 'requires',
			fromId: 'arete_A',
			toId: 'arete_M',
		});
		expect(r.getLocalGraph('arete_missing')).toBeNull();
	});

	it('groups cards by file for the global graph', async () => {
		const r = new DependencyResolver(source(), '/vault');
		await r.buildGraph();
		const g = r.getGlobalGraph();
		expect(g.files.find((f) => f.path === 'math/Intro.md')?.basename).toBe('Intro');
		expect(g.requiresEdges).toHaveLength(2);
		expect(g.relatedEdges).toHaveLength(1);
	});

	it('reports only the cycles the centre card is in', async () => {
		const r = new DependencyResolver(
			source({
				...GRAPH,
				cycles: [
					['arete_A', 'arete_M'],
					['arete_X', 'arete_Y'],
				],
			}),
			'/vault',
		);
		await r.buildGraph();
		expect(r.getLocalGraph('arete_A')!.cycles).toEqual([['arete_A', 'arete_M']]);
	});
});
