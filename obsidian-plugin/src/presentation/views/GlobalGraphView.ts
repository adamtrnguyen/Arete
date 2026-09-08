/**
 * GlobalGraphView - Vault-wide dependency graph visualization.
 *
 * Two view modes:
 * - File mode (default): Each .md file is a node, edges aggregate cross-file deps.
 * - Card mode: Individual card nodes, clustered by parent file.
 *
 * Includes a detail panel, search, and 2D/3D toggle.
 */

import { ItemView, WorkspaceLeaf, setIcon, TFile, Component } from 'obsidian';
import * as d3 from 'd3';

const ForceGraph3D = require('3d-force-graph');
import type AretePlugin from '@/main';
import { DependencyResolver } from '@/application/services/DependencyResolver';
import { CardNode, GlobalGraphResult, DependencyEdge } from '@/domain/graph/types';

export const GLOBAL_GRAPH_VIEW_TYPE = 'arete-global-graph';

// --- D3 node/link interfaces ---

interface FileGraphNode extends d3.SimulationNodeDatum {
	id: string; // filePath
	basename: string;
	cardCount: number;
	cardIds: string[];
	radius: number;
	type: 'file';
}

interface CardGraphNode extends d3.SimulationNodeDatum {
	id: string; // card arete ID
	title: string;
	filePath: string;
	lineNumber: number;
	radius: number;
	fileHue: number;
	type: 'card';
}

type GlobalNode = FileGraphNode | CardGraphNode;

interface GlobalLink extends d3.SimulationLinkDatum<GlobalNode> {
	source: string | GlobalNode;
	target: string | GlobalNode;
	edgeType: 'requires' | 'related';
	weight: number; // For file mode aggregation
}

export class GlobalGraphView extends ItemView {
	plugin: AretePlugin;
	private resolver: DependencyResolver;
	private container: HTMLElement;
	private simulation: d3.Simulation<any, any> | null = null;
	private graph3D: any = null;
	private tooltipComponent: Component = new Component();

	// State
	private viewMode: 'file' | 'card' = 'file';
	private renderMode: '2d' | '3d' = '2d';
	private showRelated = false;
	private clusteringEnabled = true;
	private searchQuery = '';
	private _selectedNodeId: string | null = null;

	// Cached data
	private graphData: GlobalGraphResult | null = null;

	// Layout elements
	private toolbarEl: HTMLElement;
	private graphContainer: HTMLElement;
	private detailPanel: HTMLElement;

	constructor(leaf: WorkspaceLeaf, plugin: AretePlugin) {
		super(leaf);
		this.plugin = plugin;
		this.resolver = plugin.dependencyResolver;
	}

	getViewType(): string {
		return GLOBAL_GRAPH_VIEW_TYPE;
	}

	getDisplayText(): string {
		return 'Arete Global Graph';
	}

	getIcon(): string {
		return 'globe';
	}

	async onOpen(): Promise<void> {
		this.container = this.contentEl;
		this.container.empty();
		this.container.addClass('arete-global-graph');

		// Build toolbar
		this.toolbarEl = this.container.createDiv({ cls: 'arete-global-graph-toolbar' });
		this.renderToolbar();

		// Main layout: graph + detail panel
		const body = this.container.createDiv({ cls: 'arete-global-graph-body' });

		this.graphContainer = body.createDiv({ cls: 'arete-global-graph-canvas' });
		this.detailPanel = body.createDiv({ cls: 'arete-global-graph-detail' });
		this.renderDetailEmpty();

		// Load and render
		await this.loadData();
		this.render();

		// Resize handler
		this.registerEvent(
			this.app.workspace.on('resize', () => {
				this.render();
			}),
		);
	}

	// --- Data Loading ---

	private async loadData(): Promise<void> {
		await this.resolver.buildGraph();
		this.graphData = this.resolver.getGlobalGraph();
	}

	// --- Toolbar ---

	private renderToolbar(): void {
		this.toolbarEl.empty();

		// Refresh
		const refreshBtn = this.toolbarEl.createDiv({ cls: 'clickable-icon' });
		setIcon(refreshBtn, 'refresh-cw');
		refreshBtn.setAttribute('title', 'Rebuild Graph');
		refreshBtn.addEventListener('click', async () => {
			await this.loadData();
			this.render();
		});

		// Search
		const searchInput = this.toolbarEl.createEl('input', {
			cls: 'arete-global-graph-search',
			attr: { type: 'text', placeholder: 'Search nodes...' },
		});
		searchInput.value = this.searchQuery;
		searchInput.addEventListener('input', () => {
			this.searchQuery = searchInput.value.toLowerCase();
			this.render();
		});

		// Separator
		this.toolbarEl.createSpan({ text: '|', cls: 'arete-global-graph-sep' });

		// View mode toggle (File / Card)
		const viewToggle = this.toolbarEl.createDiv({
			cls: 'clickable-icon' + (this.viewMode === 'card' ? ' is-active' : ''),
		});
		setIcon(viewToggle, this.viewMode === 'file' ? 'folder' : 'layers');
		viewToggle.setAttribute(
			'title',
			this.viewMode === 'file' ? 'Switch to Card Mode' : 'Switch to File Mode',
		);
		if (this.viewMode === 'card') viewToggle.style.color = 'var(--interactive-accent)';
		viewToggle.addEventListener('click', () => {
			this.viewMode = this.viewMode === 'file' ? 'card' : 'file';
			this._selectedNodeId = null;
			this.renderToolbar();
			this.render();
			this.renderDetailEmpty();
		});

		// Related edges toggle
		const relatedToggle = this.toolbarEl.createDiv({
			cls: 'clickable-icon' + (this.showRelated ? ' is-active' : ''),
		});
		setIcon(relatedToggle, 'link');
		relatedToggle.setAttribute('title', 'Toggle Related Edges');
		if (this.showRelated) relatedToggle.style.color = 'var(--interactive-accent)';
		relatedToggle.addEventListener('click', () => {
			this.showRelated = !this.showRelated;
			this.renderToolbar();
			this.render();
		});

		// 2D/3D toggle
		const dimToggle = this.toolbarEl.createDiv({ cls: 'clickable-icon' });
		setIcon(dimToggle, this.renderMode === '3d' ? 'box' : 'square');
		dimToggle.setAttribute('title', 'Toggle 2D/3D');
		if (this.renderMode === '3d') dimToggle.style.color = 'var(--interactive-accent)';
		dimToggle.addEventListener('click', () => {
			this.renderMode = this.renderMode === '2d' ? '3d' : '2d';
			this.renderToolbar();
			this.render();
		});

		// Clustering toggle (card mode only)
		if (this.viewMode === 'card') {
			const clusterToggle = this.toolbarEl.createDiv({
				cls: 'clickable-icon' + (this.clusteringEnabled ? ' is-active' : ''),
			});
			setIcon(clusterToggle, 'group');
			clusterToggle.setAttribute('title', 'Toggle Clustering by File');
			if (this.clusteringEnabled) clusterToggle.style.color = 'var(--interactive-accent)';
			clusterToggle.addEventListener('click', () => {
				this.clusteringEnabled = !this.clusteringEnabled;
				this.renderToolbar();
				this.render();
			});
		}

		// Fit/reset zoom
		const fitBtn = this.toolbarEl.createDiv({ cls: 'clickable-icon' });
		setIcon(fitBtn, 'maximize-2');
		fitBtn.setAttribute('title', 'Fit to View');
		fitBtn.addEventListener('click', () => {
			this.render();
		});
	}

	// --- Main Render Dispatch ---

	private render(): void {
		if (!this.graphData) return;

		if (this.viewMode === 'file') {
			if (this.renderMode === '3d') {
				this.renderFile3D();
			} else {
				this.renderFileMode();
			}
		} else {
			if (this.renderMode === '3d') {
				this.renderCard3D();
			} else {
				this.renderCardMode();
			}
		}
	}

	// --- File Mode (2D) ---

	private renderFileMode(): void {
		this.graphContainer.empty();
		this.stopSimulation();
		if (!this.graphData) return;

		const { files, cards, requiresEdges, relatedEdges } = this.graphData;
		if (files.length === 0) {
			this.renderEmpty('No Arete cards found in vault.');
			return;
		}

		// Build card → file lookup
		const cardToFile = new Map<string, string>();
		for (const card of cards) {
			cardToFile.set(card.id, card.filePath);
		}

		// Build file nodes
		const fileNodes: FileGraphNode[] = files.map((f) => ({
			id: f.path,
			basename: f.basename,
			cardCount: f.cardCount,
			cardIds: [...f.cardIds],
			radius: Math.max(8, Math.sqrt(f.cardCount) * 6),
			type: 'file' as const,
		}));

		// Aggregate edges at file level
		const fileEdgeMap = new Map<string, { type: 'requires' | 'related'; weight: number }>();
		const addFileEdge = (edges: DependencyEdge[], type: 'requires' | 'related') => {
			if (type === 'related' && !this.showRelated) return;
			for (const e of edges) {
				const fromFile = cardToFile.get(e.fromId);
				const toFile = cardToFile.get(e.toId);
				if (!fromFile || !toFile || fromFile === toFile) continue;
				const key = `${fromFile}→${toFile}→${type}`;
				const existing = fileEdgeMap.get(key);
				if (existing) {
					existing.weight++;
				} else {
					fileEdgeMap.set(key, { type, weight: 1 });
				}
			}
		};
		addFileEdge(requiresEdges, 'requires');
		addFileEdge(relatedEdges, 'related');

		const fileLinks: GlobalLink[] = [];
		for (const [key, val] of fileEdgeMap) {
			const [source, target] = key.split('→');
			fileLinks.push({
				source,
				target,
				edgeType: val.type,
				weight: val.weight,
			});
		}

		// Filter by search
		const matchingIds = this.getMatchingFileIds(fileNodes);

		this.renderD3(this.graphContainer, fileNodes, fileLinks, matchingIds);
	}

	// --- Card Mode (2D) ---

	private renderCardMode(): void {
		this.graphContainer.empty();
		this.stopSimulation();
		if (!this.graphData) return;

		const { cards, requiresEdges, relatedEdges } = this.graphData;
		if (cards.length === 0) {
			this.renderEmpty('No Arete cards found in vault.');
			return;
		}

		// Build card nodes
		const cardNodeSet = new Set(cards.map((c) => c.id));
		const cardNodes: CardGraphNode[] = cards.map((c) => ({
			id: c.id,
			title: c.title,
			filePath: c.filePath,
			lineNumber: c.lineNumber,
			radius: 5,
			fileHue: this.hashToHue(c.filePath),
			type: 'card' as const,
		}));

		// Card-level edges
		const edges = requiresEdges.filter(
			(e) => cardNodeSet.has(e.fromId) && cardNodeSet.has(e.toId),
		);
		const cardLinks: GlobalLink[] = edges.map((e) => ({
			source: e.fromId,
			target: e.toId,
			edgeType: 'requires' as const,
			weight: 1,
		}));

		if (this.showRelated) {
			const relEdges = relatedEdges.filter(
				(e) => cardNodeSet.has(e.fromId) && cardNodeSet.has(e.toId),
			);
			for (const e of relEdges) {
				cardLinks.push({
					source: e.fromId,
					target: e.toId,
					edgeType: 'related',
					weight: 1,
				});
			}
		}

		// Filter by search
		const matchingIds = this.getMatchingCardIds(cardNodes);

		this.renderD3(this.graphContainer, cardNodes, cardLinks, matchingIds);
	}

	// --- Unified D3 2D Renderer ---

	private renderD3(
		container: HTMLElement,
		nodes: GlobalNode[],
		links: GlobalLink[],
		matchingIds: Set<string> | null,
	): void {
		container.empty();

		const width = container.clientWidth;
		const height = container.clientHeight;
		if (width === 0 || height === 0) {
			setTimeout(() => this.render(), 100);
			return;
		}

		const svg = d3
			.select(container)
			.append('svg')
			.attr('width', '100%')
			.attr('height', '100%')
			.attr('viewBox', [0, 0, width, height]);

		const g = svg.append('g');

		// Zoom
		const zoom = d3
			.zoom<SVGSVGElement, unknown>()
			.scaleExtent([0.05, 6])
			.on('zoom', (event) => g.attr('transform', event.transform));
		svg.call(zoom);

		// Arrow marker
		svg.append('defs')
			.append('marker')
			.attr('id', 'global-arrow')
			.attr('viewBox', '0 -5 10 10')
			.attr('refX', 20)
			.attr('refY', 0)
			.attr('markerWidth', 6)
			.attr('markerHeight', 6)
			.attr('orient', 'auto')
			.append('path')
			.attr('d', 'M0,-5L10,0L0,5')
			.attr('fill', 'var(--text-muted)');

		// Simulation
		const isFileMode = this.viewMode === 'file';
		const sim = d3
			.forceSimulation(nodes as any[])
			.force(
				'link',
				d3
					.forceLink(links as any[])
					.id((d: any) => d.id)
					.distance(isFileMode ? 200 : 100),
			)
			.force('charge', d3.forceManyBody().strength(isFileMode ? -300 : -150))
			.force('center', d3.forceCenter(width / 2, height / 2))
			.force(
				'collide',
				d3.forceCollide().radius((d: any) => (d.radius || 5) + 4),
			);

		this.simulation = sim;

		// Clustering for card mode
		if (!isFileMode && this.clusteringEnabled) {
			this.applyClusteringForce(sim, nodes as CardGraphNode[]);
		}

		// Links
		const link = g
			.append('g')
			.selectAll('line')
			.data(links)
			.join('line')
			.attr('stroke', 'var(--text-muted)')
			.attr('stroke-opacity', () => {
				if (matchingIds && matchingIds.size > 0) return 0.1;
				return 0.4;
			})
			.attr('stroke-width', (d) => {
				if (isFileMode) return Math.min(1 + d.weight * 0.5, 6);
				return d.edgeType === 'requires' ? 1.5 : 0.8;
			})
			.attr('stroke-dasharray', (d) => (d.edgeType === 'related' ? '4 4' : null))
			.attr('marker-end', (d) => (d.edgeType === 'requires' ? 'url(#global-arrow)' : null));

		// Drag
		const dragBehavior = d3
			.drag<any, any>()
			.on('start', (event, d) => {
				if (!event.active) sim.alphaTarget(0.3).restart();
				d.fx = d.x;
				d.fy = d.y;
			})
			.on('drag', (event, d) => {
				d.fx = event.x;
				d.fy = event.y;
			})
			.on('end', (event, d) => {
				if (!event.active) sim.alphaTarget(0);
				d.fx = null;
				d.fy = null;
			});

		// Node groups
		const nodeGroup = g
			.append('g')
			.selectAll('.node')
			.data(nodes)
			.join('g')
			.attr('class', 'node')
			.call(dragBehavior as any);

		// Render nodes
		if (isFileMode) {
			this.renderFileNodes(nodeGroup, matchingIds);
		} else {
			this.renderCardNodes(nodeGroup, matchingIds);
		}

		// Hover highlight
		nodeGroup
			.on('mouseover', (_event, d) => {
				this.highlightConnected(d.id, nodeGroup, link, links, true);
			})
			.on('mouseout', () => {
				this.highlightConnected(null, nodeGroup, link, links, false);
			});

		// Click
		nodeGroup.on('click', (event, d) => {
			event.stopPropagation();
			this._selectedNodeId = d.id;
			if (isFileMode) {
				this.renderFileDetail(d as FileGraphNode);
			} else {
				this.renderCardDetail(d as CardGraphNode);
			}
		});

		// Double-click
		nodeGroup.on('dblclick', async (event, d) => {
			event.stopPropagation();
			if (isFileMode) {
				// Switch to card mode showing this file's neighborhood
				this.viewMode = 'card';
				this.renderToolbar();
				this.render();
			} else {
				// Open file at line
				const cardNode = d as CardGraphNode;
				await this.openFileAtLine(cardNode.filePath, cardNode.lineNumber);
			}
		});

		// Tick
		const wantClusterLabels = !isFileMode && this.clusteringEnabled;
		let tickCount = 0;
		sim.on('tick', () => {
			link.attr('x1', (d: any) => d.source.x)
				.attr('y1', (d: any) => d.source.y)
				.attr('x2', (d: any) => d.target.x)
				.attr('y2', (d: any) => d.target.y);

			nodeGroup.attr('transform', (d: any) => `translate(${d.x},${d.y})`);

			if (wantClusterLabels) {
				tickCount++;
				if (tickCount === 100) {
					this.renderClusterLabels(g, nodes as CardGraphNode[]);
				}
			}
		});

		if (wantClusterLabels) {
			sim.on('end', () => {
				this.renderClusterLabels(g, nodes as CardGraphNode[]);
			});
		}
	}

	private renderFileNodes(
		nodeGroup: d3.Selection<any, GlobalNode, any, any>,
		matchingIds: Set<string> | null,
	): void {
		nodeGroup
			.append('circle')
			.attr('r', (d: any) => d.radius)
			.attr('fill', (d: any) => {
				const hue = this.hashToHue(d.id);
				return `hsl(${hue}, 60%, 55%)`;
			})
			.attr('stroke', 'var(--background-primary)')
			.attr('stroke-width', 2)
			.attr('opacity', (d) => {
				if (matchingIds && matchingIds.size > 0) {
					return matchingIds.has(d.id) ? 1 : 0.15;
				}
				return 1;
			})
			.style('cursor', 'pointer');

		// Labels
		nodeGroup
			.append('text')
			.text((d: any) => {
				const name = d.basename;
				return name.length > 30 ? name.slice(0, 28) + '...' : name;
			})
			.attr('font-size', '9px')
			.attr('dx', (d: any) => d.radius + 4)
			.attr('dy', 3)
			.attr('fill', 'var(--text-muted)')
			.attr('opacity', (d) => {
				if (matchingIds && matchingIds.size > 0) {
					return matchingIds.has(d.id) ? 1 : 0.15;
				}
				return 1;
			})
			.style('pointer-events', 'none');

		// Card count badge
		nodeGroup
			.append('text')
			.text((d: any) => d.cardCount)
			.attr('font-size', '8px')
			.attr('text-anchor', 'middle')
			.attr('dy', 3)
			.attr('fill', 'white')
			.attr('font-weight', 'bold')
			.style('pointer-events', 'none');
	}

	private renderCardNodes(
		nodeGroup: d3.Selection<any, GlobalNode, any, any>,
		matchingIds: Set<string> | null,
	): void {
		nodeGroup
			.append('circle')
			.attr('r', (d: any) => d.radius)
			.attr('fill', (d: any) => `hsl(${d.fileHue}, 55%, 55%)`)
			.attr('stroke', 'var(--background-primary)')
			.attr('stroke-width', 1.5)
			.attr('opacity', (d) => {
				if (matchingIds && matchingIds.size > 0) {
					return matchingIds.has(d.id) ? 1 : 0.1;
				}
				return 0.85;
			})
			.style('cursor', 'pointer');

		// Labels (only on hover or for selected)
		nodeGroup
			.append('text')
			.text((d: any) => {
				const t = d.title || d.id;
				return t.length > 20 ? t.slice(0, 18) + '...' : t;
			})
			.attr('font-size', '8px')
			.attr('dx', 8)
			.attr('dy', 3)
			.attr('fill', 'var(--text-muted)')
			.attr('opacity', 0)
			.style('pointer-events', 'none')
			.attr('class', 'arete-global-graph-card-label');

		// Show labels on hover
		nodeGroup
			.on('mouseover.label', function () {
				d3.select(this).select('.arete-global-graph-card-label').attr('opacity', 1);
			})
			.on('mouseout.label', function () {
				d3.select(this).select('.arete-global-graph-card-label').attr('opacity', 0);
			});
	}

	// --- Cluster Labels ---

	private renderClusterLabels(
		g: d3.Selection<SVGGElement, unknown, null, undefined>,
		nodes: CardGraphNode[],
	): void {
		// Remove existing cluster labels
		g.selectAll('.arete-global-graph-file-label').remove();

		// Compute centroids per file
		const fileGroups = new Map<string, { xs: number[]; ys: number[]; basename: string }>();
		for (const n of nodes) {
			const simNode = n as any;
			if (simNode.x == null || simNode.y == null) continue;
			if (!fileGroups.has(n.filePath)) {
				const basename = n.filePath.replace(/\.md$/, '').split('/').pop() || n.filePath;
				fileGroups.set(n.filePath, { xs: [], ys: [], basename });
			}
			const group = fileGroups.get(n.filePath)!;
			group.xs.push(simNode.x);
			group.ys.push(simNode.y);
		}

		for (const [, group] of fileGroups) {
			if (group.xs.length === 0) continue;
			const cx = group.xs.reduce((a, b) => a + b, 0) / group.xs.length;
			const cy = group.ys.reduce((a, b) => a + b, 0) / group.ys.length;

			g.append('text')
				.attr('class', 'arete-global-graph-file-label')
				.attr('x', cx)
				.attr('y', cy - 15)
				.attr('text-anchor', 'middle')
				.attr('font-size', '10px')
				.attr('font-weight', '600')
				.attr('fill', 'var(--text-muted)')
				.attr('opacity', 0.6)
				.style('pointer-events', 'none')
				.text(
					group.basename.length > 25
						? group.basename.slice(0, 23) + '...'
						: group.basename,
				);
		}
	}

	// --- 3D Rendering ---

	private renderFile3D(): void {
		this.graphContainer.empty();
		this.stopSimulation();
		if (!this.graphData) return;

		const { files, cards, requiresEdges, relatedEdges } = this.graphData;
		if (files.length === 0) {
			this.renderEmpty('No Arete cards found in vault.');
			return;
		}

		const width = this.graphContainer.clientWidth;
		const height = this.graphContainer.clientHeight;
		if (width === 0 || height === 0) {
			setTimeout(() => this.render(), 100);
			return;
		}

		// Build card → file lookup
		const cardToFile = new Map<string, string>();
		for (const card of cards) {
			cardToFile.set(card.id, card.filePath);
		}

		// Aggregate edges
		const fileEdgeMap = new Map<string, { type: string; weight: number }>();
		const addEdge = (edges: DependencyEdge[], type: string) => {
			if (type === 'related' && !this.showRelated) return;
			for (const e of edges) {
				const fromFile = cardToFile.get(e.fromId);
				const toFile = cardToFile.get(e.toId);
				if (!fromFile || !toFile || fromFile === toFile) continue;
				const key = `${fromFile}→${toFile}`;
				const existing = fileEdgeMap.get(key);
				if (existing) existing.weight++;
				else fileEdgeMap.set(key, { type, weight: 1 });
			}
		};
		addEdge(requiresEdges, 'requires');
		addEdge(relatedEdges, 'related');

		const graphData3D = {
			nodes: files.map((f) => ({
				id: f.path,
				basename: f.basename,
				cardCount: f.cardCount,
				val: Math.max(1, Math.sqrt(f.cardCount)),
			})),
			links: Array.from(fileEdgeMap.entries()).map(([key, val]) => {
				const [source, target] = key.split('→');
				return { source, target, type: val.type, weight: val.weight };
			}),
		};

		if (this.graph3D) {
			this.graph3D._destructor?.();
			this.graph3D = null;
		}

		this.graph3D = ForceGraph3D()(this.graphContainer)
			.width(width)
			.height(height)
			.graphData(graphData3D)
			.nodeLabel((n: any) => `${n.basename} (${n.cardCount} cards)`)
			.nodeColor((n: any) => `hsl(${this.hashToHue(n.id)}, 60%, 55%)`)
			.nodeVal((n: any) => n.val)
			.linkColor(() => '#6b7280')
			.linkWidth((l: any) => Math.min(1 + l.weight * 0.5, 5))
			.linkDirectionalArrowLength((l: any) => (l.type === 'requires' ? 4 : 0))
			.linkDirectionalArrowRelPos(1)
			.onNodeClick((node: any) => {
				this._selectedNodeId = node.id;
				const fileNode: FileGraphNode = {
					id: node.id,
					basename: node.basename,
					cardCount: node.cardCount,
					cardIds: [],
					radius: 0,
					type: 'file',
				};
				this.renderFileDetail(fileNode);
			});
	}

	private renderCard3D(): void {
		this.graphContainer.empty();
		this.stopSimulation();
		if (!this.graphData) return;

		const { cards, requiresEdges, relatedEdges } = this.graphData;
		if (cards.length === 0) {
			this.renderEmpty('No Arete cards found in vault.');
			return;
		}

		const width = this.graphContainer.clientWidth;
		const height = this.graphContainer.clientHeight;
		if (width === 0 || height === 0) {
			setTimeout(() => this.render(), 100);
			return;
		}

		const cardIdSet = new Set(cards.map((c) => c.id));
		let allLinks = requiresEdges
			.filter((e) => cardIdSet.has(e.fromId) && cardIdSet.has(e.toId))
			.map((e) => ({ source: e.fromId, target: e.toId, type: 'requires' }));

		if (this.showRelated) {
			const relLinks = relatedEdges
				.filter((e) => cardIdSet.has(e.fromId) && cardIdSet.has(e.toId))
				.map((e) => ({ source: e.fromId, target: e.toId, type: 'related' }));
			allLinks = [...allLinks, ...relLinks];
		}

		const graphData3D = {
			nodes: cards.map((c) => ({
				id: c.id,
				title: c.title,
				filePath: c.filePath,
				lineNumber: c.lineNumber,
				fileHue: this.hashToHue(c.filePath),
			})),
			links: allLinks,
		};

		if (this.graph3D) {
			this.graph3D._destructor?.();
			this.graph3D = null;
		}

		this.graph3D = ForceGraph3D()(this.graphContainer)
			.width(width)
			.height(height)
			.graphData(graphData3D)
			.nodeLabel((n: any) => `${n.title}\n(${n.id})`)
			.nodeColor((n: any) => `hsl(${n.fileHue}, 55%, 55%)`)
			.nodeVal(1)
			.linkColor((l: any) => (l.type === 'requires' ? '#6b7280' : '#d1d5db'))
			.linkWidth((l: any) => (l.type === 'requires' ? 1.5 : 0.8))
			.linkDirectionalArrowLength((l: any) => (l.type === 'requires' ? 3 : 0))
			.linkDirectionalArrowRelPos(1)
			.onNodeClick((node: any) => {
				this._selectedNodeId = node.id;
				const cardNode: CardGraphNode = {
					id: node.id,
					title: node.title,
					filePath: node.filePath,
					lineNumber: node.lineNumber,
					radius: 5,
					fileHue: node.fileHue,
					type: 'card',
				};
				this.renderCardDetail(cardNode);
			});

		// Clustering in 3D card mode
		if (this.clusteringEnabled) {
			const centroids = this.getClusterCentroids3D(cards);
			this.graph3D
				.d3Force(
					'clusterX',
					d3
						.forceX<any>()
						.x((d: any) => centroids.get(d.filePath)?.x || 0)
						.strength(0.3),
				)
				.d3Force(
					'clusterY',
					d3
						.forceY<any>()
						.y((d: any) => centroids.get(d.filePath)?.y || 0)
						.strength(0.3),
				);
		}
	}

	// --- Detail Panel ---

	private renderDetailEmpty(): void {
		this.detailPanel.empty();
		const placeholder = this.detailPanel.createDiv({ cls: 'arete-global-graph-detail-empty' });
		placeholder.setText('Click a node to see details');
	}

	private renderFileDetail(node: FileGraphNode): void {
		this.detailPanel.empty();
		if (!this.graphData) return;

		// Find full file node from data
		const fileData = this.graphData.files.find((f) => f.path === node.id);
		const cardIds = fileData?.cardIds || node.cardIds;
		const cardCount = fileData?.cardCount || node.cardCount;

		const section = this.detailPanel.createDiv({ cls: 'arete-global-graph-detail-section' });

		// Title
		section.createEl('h3', { text: node.basename || node.id });

		// Stats
		const stats = section.createDiv({ cls: 'arete-global-graph-detail-stats' });
		stats.createEl('div', { text: `Cards: ${cardCount}` });
		stats.createEl('div', { text: `Path: ${node.id}` });

		// Count inbound/outbound edges
		if (this.graphData) {
			const cardToFile = new Map<string, string>();
			for (const card of this.graphData.cards) {
				cardToFile.set(card.id, card.filePath);
			}
			let inbound = 0;
			let outbound = 0;
			for (const e of this.graphData.requiresEdges) {
				const fromFile = cardToFile.get(e.fromId);
				const toFile = cardToFile.get(e.toId);
				if (toFile === node.id && fromFile !== node.id) inbound++;
				if (fromFile === node.id && toFile !== node.id) outbound++;
			}
			stats.createEl('div', { text: `Inbound deps: ${inbound}` });
			stats.createEl('div', { text: `Outbound deps: ${outbound}` });
		}

		// Card list
		if (cardIds.length > 0) {
			const listSection = section.createDiv();
			listSection.createEl('h4', { text: 'Cards' });
			const list = listSection.createEl('ul', { cls: 'arete-global-graph-detail-list' });
			for (const cid of cardIds) {
				const card = this.graphData?.cards.find((c) => c.id === cid);
				const li = list.createEl('li');
				const link = li.createEl('a', {
					text: card?.title || cid,
					href: '#',
				});
				link.addEventListener('click', (e) => {
					e.preventDefault();
					if (card) {
						// Switch to card mode and select this card
						this.viewMode = 'card';
						this._selectedNodeId = cid;
						this.renderToolbar();
						this.render();
						if (card) {
							this.renderCardDetail({
								id: card.id,
								title: card.title,
								filePath: card.filePath,
								lineNumber: card.lineNumber,
								radius: 5,
								fileHue: this.hashToHue(card.filePath),
								type: 'card',
							});
						}
					}
				});
			}
		}

		// Open file button
		const btn = section.createEl('button', {
			text: 'Open File',
			cls: 'arete-global-graph-detail-btn',
		});
		btn.addEventListener('click', async () => {
			await this.openFileAtLine(node.id, 1);
		});
	}

	private renderCardDetail(node: CardGraphNode): void {
		this.detailPanel.empty();
		if (!this.graphData) return;

		const section = this.detailPanel.createDiv({ cls: 'arete-global-graph-detail-section' });

		// Title
		section.createEl('h3', { text: node.title || node.id });

		// Info
		const info = section.createDiv({ cls: 'arete-global-graph-detail-stats' });
		info.createEl('div', { text: `ID: ${node.id}` });

		// Parent file (clickable)
		const fileLink = info.createEl('div');
		fileLink.createSpan({ text: 'File: ' });
		const fLink = fileLink.createEl('a', {
			text: node.filePath,
			href: '#',
		});
		fLink.addEventListener('click', async (e) => {
			e.preventDefault();
			await this.openFileAtLine(node.filePath, 1);
		});

		info.createEl('div', { text: `Line: ${node.lineNumber}` });

		// Prerequisites
		const prereqs = this.graphData.requiresEdges
			.filter((e) => e.fromId === node.id)
			.map((e) => this.graphData!.cards.find((c) => c.id === e.toId))
			.filter(Boolean) as CardNode[];

		if (prereqs.length > 0) {
			const prereqSection = section.createDiv();
			prereqSection.createEl('h4', { text: `Prerequisites (${prereqs.length})` });
			const list = prereqSection.createEl('ul', { cls: 'arete-global-graph-detail-list' });
			for (const p of prereqs) {
				const li = list.createEl('li');
				const link = li.createEl('a', { text: p.title || p.id, href: '#' });
				link.addEventListener('click', (e) => {
					e.preventDefault();
					this._selectedNodeId = p.id;
					this.renderCardDetail({
						id: p.id,
						title: p.title,
						filePath: p.filePath,
						lineNumber: p.lineNumber,
						radius: 5,
						fileHue: this.hashToHue(p.filePath),
						type: 'card',
					});
				});
			}
		}

		// Dependents
		const dependents = this.graphData.requiresEdges
			.filter((e) => e.toId === node.id)
			.map((e) => this.graphData!.cards.find((c) => c.id === e.fromId))
			.filter(Boolean) as CardNode[];

		if (dependents.length > 0) {
			const depSection = section.createDiv();
			depSection.createEl('h4', { text: `Dependents (${dependents.length})` });
			const list = depSection.createEl('ul', { cls: 'arete-global-graph-detail-list' });
			for (const d of dependents) {
				const li = list.createEl('li');
				const link = li.createEl('a', { text: d.title || d.id, href: '#' });
				link.addEventListener('click', (e) => {
					e.preventDefault();
					this._selectedNodeId = d.id;
					this.renderCardDetail({
						id: d.id,
						title: d.title,
						filePath: d.filePath,
						lineNumber: d.lineNumber,
						radius: 5,
						fileHue: this.hashToHue(d.filePath),
						type: 'card',
					});
				});
			}
		}

		// Related
		if (this.showRelated) {
			const related = this.graphData.relatedEdges
				.filter((e) => e.fromId === node.id || e.toId === node.id)
				.map((e) => {
					const otherId = e.fromId === node.id ? e.toId : e.fromId;
					return this.graphData!.cards.find((c) => c.id === otherId);
				})
				.filter(Boolean) as CardNode[];

			if (related.length > 0) {
				const relSection = section.createDiv();
				relSection.createEl('h4', { text: `Related (${related.length})` });
				const list = relSection.createEl('ul', { cls: 'arete-global-graph-detail-list' });
				for (const r of related) {
					const li = list.createEl('li');
					const link = li.createEl('a', { text: r.title || r.id, href: '#' });
					link.addEventListener('click', (e) => {
						e.preventDefault();
						this._selectedNodeId = r.id;
						this.renderCardDetail({
							id: r.id,
							title: r.title,
							filePath: r.filePath,
							lineNumber: r.lineNumber,
							radius: 5,
							fileHue: this.hashToHue(r.filePath),
							type: 'card',
						});
					});
				}
			}
		}

		// Open in Editor button
		const btn = section.createEl('button', {
			text: 'Open in Editor',
			cls: 'arete-global-graph-detail-btn',
		});
		btn.addEventListener('click', async () => {
			await this.openFileAtLine(node.filePath, node.lineNumber);
		});
	}

	// --- Helpers ---

	private highlightConnected(
		hoveredId: string | null,
		nodeGroup: d3.Selection<any, GlobalNode, any, any>,
		linkSel: d3.Selection<any, GlobalLink, any, any>,
		links: GlobalLink[],
		highlight: boolean,
	): void {
		if (!highlight || !hoveredId) {
			// Reset
			nodeGroup.select('circle').attr('opacity', 1);
			nodeGroup.selectAll('text').attr('opacity', () => {
				// Keep card labels hidden
				if (this.viewMode === 'card') return 0;
				return 1;
			});
			linkSel.attr('stroke-opacity', 0.4);
			return;
		}

		// Find connected node IDs
		const connected = new Set<string>([hoveredId]);
		for (const l of links) {
			const srcId = typeof l.source === 'string' ? l.source : (l.source as GlobalNode).id;
			const tgtId = typeof l.target === 'string' ? l.target : (l.target as GlobalNode).id;
			if (srcId === hoveredId) connected.add(tgtId);
			if (tgtId === hoveredId) connected.add(srcId);
		}

		nodeGroup.select('circle').attr('opacity', (d) => (connected.has(d.id) ? 1 : 0.1));
		nodeGroup
			.selectAll('text:not(.arete-global-graph-card-label)')
			.attr('opacity', (d: any) => (connected.has(d.id) ? 1 : 0.1));
		linkSel.attr('stroke-opacity', (d: any) => {
			const srcId = typeof d.source === 'string' ? d.source : d.source.id;
			const tgtId = typeof d.target === 'string' ? d.target : d.target.id;
			return srcId === hoveredId || tgtId === hoveredId ? 0.8 : 0.05;
		});
	}

	private applyClusteringForce(
		simulation: d3.Simulation<any, any>,
		nodes: CardGraphNode[],
		strength = 0.3,
	): void {
		const centroids = this.getClusterCentroids2D(nodes);
		simulation.force(
			'clusterX',
			d3
				.forceX<any>()
				.x((d: any) => centroids.get(d.filePath)?.x || 0)
				.strength(strength),
		);
		simulation.force(
			'clusterY',
			d3
				.forceY<any>()
				.y((d: any) => centroids.get(d.filePath)?.y || 0)
				.strength(strength),
		);
	}

	private getClusterCentroids2D(nodes: CardGraphNode[]): Map<string, { x: number; y: number }> {
		const files = new Set(nodes.map((n) => n.filePath));
		const centroids = new Map<string, { x: number; y: number }>();
		let i = 0;
		const count = files.size;
		const radius = Math.max(300, count * 30);
		for (const fp of files) {
			const angle = (i / count) * 2 * Math.PI;
			centroids.set(fp, {
				x: Math.cos(angle) * radius,
				y: Math.sin(angle) * radius,
			});
			i++;
		}
		return centroids;
	}

	private getClusterCentroids3D(
		cards: CardNode[],
	): Map<string, { x: number; y: number; z: number }> {
		const files = new Set(cards.map((c) => c.filePath));
		const centroids = new Map<string, { x: number; y: number; z: number }>();
		let i = 0;
		const count = files.size;
		const radius = Math.max(300, count * 30);
		for (const fp of files) {
			const angle = (i / count) * 2 * Math.PI;
			centroids.set(fp, {
				x: Math.cos(angle) * radius,
				y: Math.sin(angle) * radius,
				z: (i % 2 === 0 ? 1 : -1) * 100,
			});
			i++;
		}
		return centroids;
	}

	private hashToHue(str: string): number {
		let hash = 0;
		for (let i = 0; i < str.length; i++) {
			hash = str.charCodeAt(i) + ((hash << 5) - hash);
		}
		return Math.abs(hash) % 360;
	}

	private getMatchingFileIds(nodes: FileGraphNode[]): Set<string> | null {
		if (!this.searchQuery) return null;
		const q = this.searchQuery;
		const matching = new Set<string>();
		for (const n of nodes) {
			if (n.basename.toLowerCase().includes(q) || n.id.toLowerCase().includes(q)) {
				matching.add(n.id);
			}
		}
		return matching.size > 0 ? matching : null;
	}

	private getMatchingCardIds(nodes: CardGraphNode[]): Set<string> | null {
		if (!this.searchQuery) return null;
		const q = this.searchQuery;
		const matching = new Set<string>();
		for (const n of nodes) {
			if (
				n.title.toLowerCase().includes(q) ||
				n.id.toLowerCase().includes(q) ||
				n.filePath.toLowerCase().includes(q)
			) {
				matching.add(n.id);
			}
		}
		return matching.size > 0 ? matching : null;
	}

	private renderEmpty(msg: string): void {
		this.graphContainer.empty();
		const div = this.graphContainer.createDiv({ cls: 'arete-global-graph-empty' });
		div.setText(msg);
	}

	private stopSimulation(): void {
		if (this.simulation) {
			this.simulation.stop();
			this.simulation = null;
		}
		if (this.graph3D) {
			this.graph3D._destructor?.();
			this.graph3D = null;
		}
	}

	private async openFileAtLine(filePath: string, lineNumber: number): Promise<void> {
		const file = this.app.vault.getAbstractFileByPath(filePath);
		if (file instanceof TFile) {
			const leaf = this.app.workspace.getLeaf();
			await leaf.openFile(file, {
				eState: { line: lineNumber },
			});
		}
	}

	async onClose(): Promise<void> {
		this.stopSimulation();
		this.tooltipComponent.unload();
	}
}
