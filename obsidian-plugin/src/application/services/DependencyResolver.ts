/**
 * DependencyResolver serves the graph views from the graph the Python side resolves.
 *
 * It used to parse frontmatter and resolve `deps` references itself: a second
 * implementation that missed the ambiguous-basename and scalar-ref fixes (G5/G6). Now it
 * only loads `GraphSource` output, caches it until `invalidate()`, and walks it.
 */

import {
	CardNode,
	DependencyEdge,
	DependencyGraphBuilder,
	FileNode,
	GlobalGraphResult,
	GraphExport,
	GraphSource,
	LocalGraphResult,
} from '@/domain/graph/types';

export class DependencyResolver {
	private graphBuilder = new DependencyGraphBuilder();
	private cycles: string[][] = [];
	private loaded: Promise<void> | null = null;

	constructor(
		private source: GraphSource,
		private vaultRoot: string,
	) {}

	/** Drop the cached graph; the next buildGraph() fetches it again. */
	invalidate(): void {
		this.loaded = null;
	}

	/** Load the graph once; later calls reuse it until invalidate() (or force). */
	async buildGraph(force = false): Promise<void> {
		if (force) this.invalidate();
		if (!this.loaded) {
			this.loaded = this.source.fetchGraph(this.vaultRoot).then((g) => this.load(g));
			this.loaded.catch(() => {
				this.loaded = null; // a failed fetch is retried
			});
		}
		return this.loaded;
	}

	private load(g: GraphExport): void {
		const builder = new DependencyGraphBuilder();
		for (const n of g.nodes) {
			builder.addNode({ id: n.id, title: n.title, filePath: n.file, lineNumber: n.line });
		}
		for (const [from, to] of g.requires) builder.addRequires(from, to);
		for (const [from, to] of g.related) builder.addRelated(from, to);
		this.graphBuilder = builder;
		this.cycles = g.cycles;
	}

	/**
	 * Get the full vault-wide dependency graph for global visualization.
	 */
	getGlobalGraph(): GlobalGraphResult {
		const allNodes = this.graphBuilder.getAllNodes();

		// Build file index
		const fileMap = new Map<string, FileNode>();
		for (const node of allNodes) {
			if (!fileMap.has(node.filePath)) {
				const basename =
					node.filePath.replace(/\.md$/, '').split('/').pop() || node.filePath;
				fileMap.set(node.filePath, {
					path: node.filePath,
					basename,
					cardCount: 0,
					cardIds: [],
				});
			}
			const file = fileMap.get(node.filePath)!;
			file.cardCount++;
			file.cardIds.push(node.id);
		}

		// Collect ALL edges
		const requiresEdges: DependencyEdge[] = [];
		const relatedEdges: DependencyEdge[] = [];
		for (const node of allNodes) {
			for (const prereqId of this.graphBuilder.getPrerequisites(node.id)) {
				requiresEdges.push({ type: 'requires', fromId: node.id, toId: prereqId });
			}
			for (const relId of this.graphBuilder.getRelated(node.id)) {
				relatedEdges.push({ type: 'related', fromId: node.id, toId: relId });
			}
		}

		return {
			files: Array.from(fileMap.values()),
			cards: allNodes,
			requiresEdges,
			relatedEdges,
		};
	}

	/**
	 * Get local subgraph centered on a card.
	 */
	getLocalGraph(cardId: string, depth = 2): LocalGraphResult | null {
		if (!this.graphBuilder.hasNode(cardId)) {
			return null;
		}

		const center = this.graphBuilder.getNode(cardId)!;
		const prereqIds = new Set<string>();
		const dependentIds = new Set<string>();
		const relatedIds = new Set<string>();

		// Walk prerequisites backward
		this.walkPrereqs(cardId, depth, prereqIds);

		// Walk dependents forward
		this.walkDependents(cardId, depth, dependentIds);

		// Get direct related
		for (const relId of this.graphBuilder.getRelated(cardId)) {
			if (this.graphBuilder.hasNode(relId)) {
				relatedIds.add(relId);
			}
		}

		// Convert to CardNode arrays
		const prerequisites: CardNode[] = [];
		for (const id of prereqIds) {
			const node = this.graphBuilder.getNode(id);
			if (node) prerequisites.push(node);
		}

		const dependents: CardNode[] = [];
		for (const id of dependentIds) {
			const node = this.graphBuilder.getNode(id);
			if (node) dependents.push(node);
		}

		const related: CardNode[] = [];
		for (const id of relatedIds) {
			const node = this.graphBuilder.getNode(id);
			if (node) related.push(node);
		}

		// Collect all edges within the subgraph
		const subgraphNodes = new Set([cardId, ...prereqIds, ...dependentIds, ...relatedIds]);
		const links: DependencyEdge[] = [];

		for (const sourceId of subgraphNodes) {
			// Check requires (outbound edges from sourceId)
			// requires map in GraphBuilder is Source -> Targets
			const targets = this.graphBuilder.getPrerequisites(sourceId); // what sourceId requires

			for (const targetId of targets) {
				if (subgraphNodes.has(targetId)) {
					links.push({ type: 'requires', fromId: sourceId, toId: targetId });
				}
			}

			// Check related
			const rels = this.graphBuilder.getRelated(sourceId);
			for (const targetId of rels) {
				if (subgraphNodes.has(targetId)) {
					links.push({ type: 'related', fromId: sourceId, toId: targetId });
				}
			}
		}

		// Detect cycles (simplified)
		const cycles = this.detectCyclesForCard(cardId);

		return {
			center,
			prerequisites,
			dependents,
			related,
			links,
			cycles,
		};
	}

	// --- Private helpers ---

	/**
	 * BFS traversal to collect prerequisites up to set depth.
	 */
	private walkPrereqs(startCardId: string, maxDepth: number, collected: Set<string>): void {
		if (maxDepth <= 0) return;

		// Queue: { id, depth } (depth is distance from start)
		const queue: Array<{ id: string; distance: number }> = [{ id: startCardId, distance: 0 }];
		const visited = new Set<string>([startCardId]);

		while (queue.length > 0) {
			const { id, distance } = queue.shift()!;

			if (distance >= maxDepth) continue;

			// Get prereqs (outgoing/upstream)
			const neighbors = this.graphBuilder.getPrerequisites(id);
			for (const nid of neighbors) {
				if (this.graphBuilder.hasNode(nid)) {
					// We collect it
					collected.add(nid);

					if (!visited.has(nid)) {
						visited.add(nid);
						queue.push({ id: nid, distance: distance + 1 });
					}
				}
			}
		}
	}

	/**
	 * BFS traversal to collect dependents up to set depth.
	 */
	private walkDependents(startCardId: string, maxDepth: number, collected: Set<string>): void {
		if (maxDepth <= 0) return;

		const queue: Array<{ id: string; distance: number }> = [{ id: startCardId, distance: 0 }];
		const visited = new Set<string>([startCardId]);

		while (queue.length > 0) {
			const { id, distance } = queue.shift()!;

			if (distance >= maxDepth) continue;

			// Get dependents (incoming/downstream)
			const neighbors = this.graphBuilder.getDependents(id);
			for (const nid of neighbors) {
				if (this.graphBuilder.hasNode(nid)) {
					collected.add(nid);

					if (!visited.has(nid)) {
						visited.add(nid);
						queue.push({ id: nid, distance: distance + 1 });
					}
				}
			}
		}
	}

	private detectCyclesForCard(cardId: string): string[][] {
		return this.cycles.filter((c) => c.includes(cardId));
	}
}
