/**
 * DependencyResolver parses vault files to build a dependency graph cache.
 *
 * This is the Obsidian-side resolver that reads YAML frontmatter directly
 * and builds a local graph for UI rendering.
 */

import { App } from 'obsidian';
import { AretePluginSettings } from '@/domain/settings';
import { CardNode, DependencyEdge, DependencyGraphBuilder, FileNode, GlobalGraphResult, LocalGraphResult } from '@/domain/graph/types';

export class DependencyResolver {
	private app: App;
	private settings: AretePluginSettings;
	private graphBuilder: DependencyGraphBuilder;
	private fileIndex: Map<string, string[]> = new Map(); // basename → card IDs

	constructor(app: App, settings: AretePluginSettings) {
		this.app = app;
		this.settings = settings;
		this.graphBuilder = new DependencyGraphBuilder();
	}

	/**
	 * Build/rebuild the graph from all vault files.
	 * Two-pass approach:
	 * 1. Collect all cards and build file index (basename → card IDs)
	 * 2. Resolve dependency references using the index
	 */
	async buildGraph(): Promise<void> {
		this.graphBuilder = new DependencyGraphBuilder();
		this.fileIndex = new Map();
		const files = this.app.vault.getMarkdownFiles();

		// Pending deps to resolve in second pass
		const pendingDeps: Array<{
			cardId: string;
			requires: string[];
			related: string[];
		}> = [];

		// First pass: collect all cards
		for (const file of files) {
			try {
				const cache = this.app.metadataCache.getFileCache(file);
				const frontmatter = cache?.frontmatter;

				if (!frontmatter || !frontmatter.cards) continue;

				const cards = frontmatter.cards;
				if (!Array.isArray(cards)) continue;

				// Get file basename for index
				const basename = file.basename; // "algebra.md" -> "algebra"
				if (!this.fileIndex.has(basename)) {
					this.fileIndex.set(basename, []);
				}

				for (const card of cards) {
					if (typeof card !== 'object' || !card.id) continue;

					// Extract title from fields
					let title = card.id;
					if (card.fields && typeof card.fields === 'object') {
						title = card.fields.Front || card.id;
					}

					// Get line number if available
					const lineNumber = card.__line__ || 1;

					const node: CardNode = {
						id: card.id,
						title: String(title).slice(0, 100),
						filePath: file.path,
						lineNumber,
					};

					this.graphBuilder.addNode(node);

					// Add to file index
					this.fileIndex.get(basename)!.push(card.id);

					// Collect deps for second pass
					if (card.deps && typeof card.deps === 'object') {
						const requires = Array.isArray(card.deps.requires)
							? card.deps.requires
							: [];
						const related = Array.isArray(card.deps.related) ? card.deps.related : [];
						if (requires.length > 0 || related.length > 0) {
							pendingDeps.push({ cardId: card.id, requires, related });
						}
					}
				}
			} catch (e) {
				console.warn(`[DependencyResolver] Failed to parse ${file.path}:`, e);
			}
		}

		// Second pass: resolve references and add edges
		for (const { cardId, requires, related } of pendingDeps) {
			for (const ref of requires) {
				if (typeof ref === 'string') {
					const resolved = this.resolveReference(ref);
					for (const targetId of resolved) {
						this.graphBuilder.addRequires(cardId, targetId);
					}
				}
			}

			for (const ref of related) {
				if (typeof ref === 'string') {
					const resolved = this.resolveReference(ref);
					for (const targetId of resolved) {
						this.graphBuilder.addRelated(cardId, targetId);
					}
				}
			}
		}

	}

	/**
	 * Resolve a dependency reference to card ID(s).
	 * - arete_XXX: Direct card ID (returns single-element array if exists)
	 * - basename: All cards in that file (returns array of all card IDs)
	 */
	private resolveReference(ref: string): string[] {
		if (ref.startsWith('arete_')) {
			// Direct card ID lookup
			if (this.graphBuilder.hasNode(ref)) {
				return [ref];
			}
		} else {
			// Note basename → all cards in that file
			// Strip wikilinks brackets if present e.g. "[[Name]]" -> "Name"
			const normalizedRef = ref.replace(/^\[\[|\]\]$/g, '');

			if (this.fileIndex.has(normalizedRef)) {
				return this.fileIndex.get(normalizedRef)!;
			} else {
				console.warn(
					`[DependencyResolver] No file with basename '${normalizedRef}' found (raw: '${ref}')`,
				);
				return [];
			}
		}
		return [];
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
				const basename = node.filePath.replace(/\.md$/, '').split('/').pop() || node.filePath;
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
		const links: any[] = [];

		for (const sourceId of subgraphNodes) {
			// Check requires (outbound edges from sourceId)
			// requires map in GraphBuilder is Source -> Targets
			const targets = this.graphBuilder.getPrerequisites(sourceId); // nomenclature is confusing, let's assume getPrerequisites returns "things sourceId depends on"
			// Wait, I need to be sure about getPrerequisites.
			// In DependencyResolver line 90: getPrerequisites(cardId) returns this.requires.get(cardId).
			// In addRequires(from, to), we push to from's list. So yes, it returns outgoing edges.

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
	private walkPrereqs(
		startCardId: string,
		maxDepth: number,
		collected: Set<string>,
	): void {
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
	private walkDependents(
		startCardId: string,
		maxDepth: number,
		collected: Set<string>,
	): void {
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
		const visited = new Set<string>();
		const recStack = new Set<string>();
		const cycles: string[][] = [];
		const path: string[] = [];

		const dfs = (cid: string): void => {
			visited.add(cid);
			recStack.add(cid);
			path.push(cid);

			for (const prereqId of this.graphBuilder.getPrerequisites(cid)) {
				if (!this.graphBuilder.hasNode(prereqId)) continue;

				if (!visited.has(prereqId)) {
					dfs(prereqId);
				} else if (recStack.has(prereqId)) {
					// Found cycle
					const cycleStart = path.indexOf(prereqId);
					const cycle = path.slice(cycleStart);
					if (cycle.includes(cardId)) {
						cycles.push([...cycle]);
					}
				}
			}

			path.pop();
			recStack.delete(cid);
		};

		if (this.graphBuilder.hasNode(cardId)) {
			dfs(cardId);
		}

		return cycles;
	}
}
