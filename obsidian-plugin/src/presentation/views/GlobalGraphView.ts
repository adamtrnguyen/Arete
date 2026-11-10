/**
 * GlobalGraphView - Vault-wide dependency graph visualization.
 *
 * Two view modes:
 * - File mode (default): Each .md file is a node, edges aggregate cross-file deps.
 * - Card mode: Individual card nodes, clustered by parent file.
 *
 * Includes a detail panel, search, and 2D/3D toggle.
 */

import {
	ButtonComponent,
	ItemView,
	WorkspaceLeaf,
	setIcon,
	setTooltip,
	TFile,
	Component,
} from 'obsidian';
import * as d3 from 'd3';

import { DependencyResolver } from '@/application/services/DependencyResolver';
import { CardNode, GlobalGraphResult, DependencyEdge } from '@/domain/graph/types';
import {
	GraphColors,
	loadForceGraph3D,
	resolveGraphColors,
} from '@/presentation/graph/ForceGraph3D';

/** What this view needs from the plugin (main.ts satisfies it; no import of main). */
export interface GlobalGraphHost {
	dependencyResolver: DependencyResolver;
}

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
	plugin: GlobalGraphHost;
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
	/** `graphData.cards` by id, rebuilt with `graphData`. */
	private cardById = new Map<string, CardNode>();

	/** Delay before a search keystroke or a pane resize is applied. */
	private static readonly INPUT_DEBOUNCE_MS = 150;
	/** Duration of the camera move that "Fit to View" makes in 3D. */
	private static readonly ZOOM_TO_FIT_MS = 400;
	/** Class of a file node's name label, the text the search dims. */
	private static readonly FILE_LABEL_CLASS = 'arete-global-graph-node-label';
	private searchTimer: ReturnType<typeof setTimeout> | null = null;
	private resizeTimer: ReturnType<typeof setTimeout> | null = null;
	/** The current 2D drawing's elements the search restyles; null when none is drawn. */
	private searchTargets: {
		nodeGroup: d3.Selection<any, GlobalNode, any, any>;
		link: d3.Selection<any, GlobalLink, any, any>;
		isFileMode: boolean;
	} | null = null;
	/** Fits the current drawing to a new pane size; null when nothing is drawn. */
	private resizeViewport: (() => void) | null = null;

	// Layout elements
	private toolbarEl: HTMLElement;
	private graphContainer: HTMLElement;
	private detailPanel: HTMLElement;

	constructor(leaf: WorkspaceLeaf, plugin: GlobalGraphHost) {
		super(leaf);
		this.plugin = plugin;
		this.resolver = plugin.dependencyResolver;
	}

	getViewType(): string {
		return GLOBAL_GRAPH_VIEW_TYPE;
	}

	getDisplayText(): string {
		return 'Arete global graph';
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

		// Follow the canvas size. A render while hidden measures 0x0 and draws nothing, so
		// the first non-zero size renders; later sizes only resize the drawing (debounced),
		// keeping the layout.
		let lastSize = '';
		const observer = new ResizeObserver(([entry]) => {
			const { width, height } = entry.contentRect;
			const size = `${Math.round(width)}x${Math.round(height)}`;
			if (width === 0 || height === 0 || size === lastSize) return;
			lastSize = size;
			this.onCanvasResize();
		});
		observer.observe(this.graphContainer);
		this.register(() => observer.disconnect());

		// 2D follows the theme through CSS; a 3D drawing repaints in place.
		this.registerEvent(this.app.workspace.on('css-change', () => this.repaint3D?.()));
	}

	// --- Data Loading ---

	private async loadData(force = false): Promise<void> {
		await this.resolver.buildGraph(force);
		this.graphData = this.resolver.getGlobalGraph();
		this.cardById = new Map(this.graphData.cards.map((c) => [c.id, c]));
	}

	// --- Toolbar ---

	private renderToolbar(): void {
		this.toolbarEl.empty();

		// Refresh
		const refreshBtn = this.toolbarEl.createDiv({ cls: 'clickable-icon' });
		setIcon(refreshBtn, 'refresh-cw');
		setTooltip(refreshBtn, 'Rebuild graph');
		refreshBtn.addEventListener('click', async () => {
			await this.loadData(true);
			this.render();
		});

		// Search
		const searchInput = this.toolbarEl.createEl('input', {
			cls: 'arete-global-graph-search',
			attr: { type: 'text', placeholder: 'Search nodes…' },
		});
		searchInput.value = this.searchQuery;
		searchInput.addEventListener('input', () => {
			this.searchQuery = searchInput.value.toLowerCase();
			if (this.searchTimer) clearTimeout(this.searchTimer);
			this.searchTimer = setTimeout(() => {
				this.searchTimer = null;
				this.applySearch();
			}, GlobalGraphView.INPUT_DEBOUNCE_MS);
		});

		// Separator
		this.toolbarEl.createSpan({ text: '|', cls: 'arete-global-graph-sep' });

		// View mode toggle (File / Card)
		const viewToggle = this.toolbarEl.createDiv({
			cls: 'clickable-icon' + (this.viewMode === 'card' ? ' is-active' : ''),
		});
		setIcon(viewToggle, this.viewMode === 'file' ? 'folder' : 'layers');
		setTooltip(
			viewToggle,
			this.viewMode === 'file' ? 'Switch to card mode' : 'Switch to file mode',
		);
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
		setTooltip(relatedToggle, 'Toggle related edges');
		relatedToggle.addEventListener('click', () => {
			this.showRelated = !this.showRelated;
			this.renderToolbar();
			this.render();
		});

		// 2D/3D toggle
		const dimToggle = this.toolbarEl.createDiv({
			cls: 'clickable-icon' + (this.renderMode === '3d' ? ' is-active' : ''),
		});
		setIcon(dimToggle, this.renderMode === '3d' ? 'box' : 'square');
		setTooltip(dimToggle, 'Toggle 2D/3D');
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
			setTooltip(clusterToggle, 'Toggle clustering by file');
			clusterToggle.addEventListener('click', () => {
				this.clusteringEnabled = !this.clusteringEnabled;
				this.renderToolbar();
				this.render();
			});
		}

		// Fit/reset zoom
		const fitBtn = this.toolbarEl.createDiv({ cls: 'clickable-icon' });
		setIcon(fitBtn, 'maximize-2');
		setTooltip(fitBtn, 'Fit to view');
		fitBtn.addEventListener('click', () => {
			if (this.fitToView) this.fitToView();
			else this.render();
		});
	}

	// --- Search and resize ---

	/**
	 * Dim the 2D nodes and links that do not match the search, in place: the layout and
	 * the simulation are left alone. The 3D views do not show the search.
	 */
	private applySearch(): void {
		const t = this.searchTargets;
		if (!t) return;
		const nodes = t.nodeGroup.data();
		const matching = t.isFileMode
			? this.getMatchingFileIds(nodes as FileGraphNode[])
			: this.getMatchingCardIds(nodes as CardGraphNode[]);
		const dim = t.isFileMode ? 0.15 : 0.1;
		const base = t.isFileMode ? 1 : 0.85;
		const opacity = (d: GlobalNode) => (matching ? (matching.has(d.id) ? 1 : dim) : base);
		t.link.attr('stroke-opacity', matching ? 0.1 : 0.4);
		t.nodeGroup.select('circle').attr('opacity', opacity);
		if (t.isFileMode) {
			t.nodeGroup.select(`.${GlobalGraphView.FILE_LABEL_CLASS}`).attr('opacity', opacity);
		}
	}

	/** Draw on the first usable size; afterwards resize the drawing, debounced. */
	private onCanvasResize(): void {
		if (!this.resizeViewport) {
			this.render();
			return;
		}
		if (this.resizeTimer) clearTimeout(this.resizeTimer);
		this.resizeTimer = setTimeout(() => {
			this.resizeTimer = null;
			this.resizeViewport?.();
		}, GlobalGraphView.INPUT_DEBOUNCE_MS);
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

		this.renderD3(this.graphContainer, fileNodes, fileLinks);
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

		this.renderD3(this.graphContainer, cardNodes, cardLinks);
	}

	// --- Unified D3 2D Renderer ---

	private renderD3(container: HTMLElement, nodes: GlobalNode[], links: GlobalLink[]): void {
		container.empty();

		let width = container.clientWidth;
		let height = container.clientHeight;
		if (width === 0 || height === 0) {
			// hidden; the ResizeObserver re-renders once it has a size
			return;
		}

		const svg = d3
			.select(container)
			.append('svg')
			.attr('width', '100%')
			.attr('height', '100%')
			.attr('viewBox', [0, 0, width, height]);

		// A pane resize keeps the layout: map the viewport to the new size and fit to it.
		// While the layout still runs, its end handler does the fit with the new size.
		this.resizeViewport = () => {
			if (container.clientWidth === 0 || container.clientHeight === 0) return;
			width = container.clientWidth;
			height = container.clientHeight;
			svg.attr('viewBox', [0, 0, width, height]);
			if (this.simulation && this.simulation.alpha() < this.simulation.alphaMin()) {
				this.fitToView?.();
			}
		};

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
			.attr('class', 'arete-global-graph-arrow');

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

		// Zoom to the laid-out nodes. A few hundred files spread far past the pane, so an
		// unfitted graph shows one corner; "Fit to View" zooms to the nodes, it does not re-render.
		this.fitToView = () => {
			const placed = (nodes as any[]).filter((n) => Number.isFinite(n.x));
			if (placed.length === 0) return;
			const pad = (n: any) => (n.radius || 5) + 20;
			const minX = Math.min(...placed.map((n) => n.x - pad(n)));
			const maxX = Math.max(...placed.map((n) => n.x + pad(n)));
			const minY = Math.min(...placed.map((n) => n.y - pad(n)));
			const maxY = Math.max(...placed.map((n) => n.y + pad(n)));
			const k = Math.max(
				0.05,
				Math.min(6, 0.95 / Math.max((maxX - minX) / width, (maxY - minY) / height)),
			);
			const transform = d3.zoomIdentity
				.translate(width / 2, height / 2)
				.scale(k)
				.translate(-(minX + maxX) / 2, -(minY + maxY) / 2);
			svg.transition().duration(400).call(zoom.transform, transform);
		};
		sim.on('end.fit', () => this.fitToView?.());

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
			.attr('class', (d) => `arete-global-graph-link mod-${d.edgeType}`)
			.attr('stroke-width', (d) => {
				if (isFileMode) return Math.min(1 + d.weight * 0.5, 6);
				return d.edgeType === 'requires' ? 1.5 : 0.8;
			})
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
			.selectAll('.arete-global-graph-node')
			.data(nodes)
			.join('g')
			.attr('class', `arete-global-graph-node mod-${isFileMode ? 'file' : 'card'}`)
			.call(dragBehavior as any);

		// Render nodes
		if (isFileMode) {
			this.renderFileNodes(nodeGroup);
		} else {
			this.renderCardNodes(nodeGroup);
		}
		this.searchTargets = { nodeGroup, link, isFileMode };
		this.applySearch();

		// Hover highlight: global-graph.css dims everything while the svg has
		// `is-hovering`, except elements marked `is-connected`. Only the hovered
		// node, its neighbours and its links are marked, so a hover costs its degree.
		const { neighbours, incident } = this.indexLinks(links);
		const nodeEls = new Map<string, Element>();
		nodeGroup.each(function (d) {
			nodeEls.set(d.id, this as Element);
		});
		const linkEls = link.nodes() as Element[];
		const svgEl = svg.node() as Element;
		let marked: Element[] = [];
		const clearHover = () => {
			svgEl.classList.remove('is-hovering');
			for (const el of marked) el.classList.remove('is-connected');
			marked = [];
		};
		nodeGroup
			.on('mouseover', (_event, d) => {
				clearHover();
				const mark = (el: Element | undefined) => {
					if (!el) return;
					el.classList.add('is-connected');
					marked.push(el);
				};
				mark(nodeEls.get(d.id));
				for (const id of neighbours.get(d.id) ?? []) mark(nodeEls.get(id));
				for (const i of incident.get(d.id) ?? []) mark(linkEls[i]);
				svgEl.classList.add('is-hovering');
			})
			.on('mouseout', clearHover);

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

	private renderFileNodes(nodeGroup: d3.Selection<any, GlobalNode, any, any>): void {
		nodeGroup
			.append('circle')
			.attr('r', (d: any) => d.radius)
			.attr('fill', (d: any) => {
				const hue = this.hashToHue(d.id);
				return `hsl(${hue}, 60%, 55%)`;
			});

		// Labels
		nodeGroup
			.append('text')
			.text((d: any) => {
				const name = d.basename;
				return name.length > 30 ? name.slice(0, 28) + '...' : name;
			})
			.attr('dx', (d: any) => d.radius + 4)
			.attr('dy', 3)
			.attr('class', GlobalGraphView.FILE_LABEL_CLASS);

		// Card count badge
		nodeGroup
			.append('text')
			.text((d: any) => d.cardCount)
			.attr('dy', 3)
			.attr('class', 'arete-global-graph-node-count');
	}

	private renderCardNodes(nodeGroup: d3.Selection<any, GlobalNode, any, any>): void {
		nodeGroup
			.append('circle')
			.attr('r', (d: any) => d.radius)
			.attr('fill', (d: any) => `hsl(${d.fileHue}, 55%, 55%)`);

		// A card's label exists only while the card is hovered, so the svg does not carry
		// one hidden <text> per card.
		nodeGroup
			.on('mouseover.label', function (_event, d: any) {
				const t = d.title || d.id;
				const group = d3.select(this);
				group.selectAll('.arete-global-graph-card-label').remove();
				group
					.append('text')
					.text(t.length > 20 ? t.slice(0, 18) + '...' : t)
					.attr('dx', 8)
					.attr('dy', 3)
					.attr('class', 'arete-global-graph-card-label');
			})
			.on('mouseout.label', function () {
				d3.select(this).selectAll('.arete-global-graph-card-label').remove();
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
			// hidden; the ResizeObserver re-renders once it has a size
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

		const ForceGraph3D = loadForceGraph3D();
		this.graph3D = ForceGraph3D()(this.graphContainer)
			.width(width)
			.height(height)
			.graphData(graphData3D)
			.nodeLabel((n: any) => `${n.basename} (${n.cardCount} cards)`)
			.nodeVal((n: any) => n.val)
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
		this.paintOnThemeChange((graph, colors) =>
			graph
				.backgroundColor(colors.background)
				.nodeColor((n: any) => `hsl(${this.hashToHue(n.id)}, 60%, 55%)`)
				.linkColor(() => colors.line),
		);
		this.resizeViewport = () => this.resize3D();
		this.fitToView = () => this.graph3D?.zoomToFit(GlobalGraphView.ZOOM_TO_FIT_MS);
	}

	/**
	 * Colour the current 3D drawing from the theme now, and again on a theme change. The
	 * theme is read once per call, so neither a frame nor a node resolves a variable; a
	 * repaint swaps the colour accessors and leaves the layout alone.
	 */
	private paintOnThemeChange(paint: (graph: any, colors: GraphColors) => unknown): void {
		const graph = this.graph3D;
		this.repaint3D = () => paint(graph, resolveGraphColors(this.graphContainer));
		this.repaint3D();
	}

	/** Resize the 3D renderer to the canvas; the layout is kept. */
	private resize3D(): void {
		const width = this.graphContainer.clientWidth;
		const height = this.graphContainer.clientHeight;
		if (!this.graph3D || width === 0 || height === 0) return;
		this.graph3D.width(width).height(height);
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
			// hidden; the ResizeObserver re-renders once it has a size
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

		const ForceGraph3D = loadForceGraph3D();
		this.graph3D = ForceGraph3D()(this.graphContainer)
			.width(width)
			.height(height)
			.graphData(graphData3D)
			.nodeLabel((n: any) => `${n.title}\n(${n.id})`)
			.nodeVal(1)
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
		this.paintOnThemeChange((graph, colors) =>
			graph
				.backgroundColor(colors.background)
				.nodeColor((n: any) => `hsl(${n.fileHue}, 55%, 55%)`)
				.linkColor((l: any) => (l.type === 'requires' ? colors.line : colors.unresolved)),
		);
		this.resizeViewport = () => this.resize3D();
		this.fitToView = () => this.graph3D?.zoomToFit(GlobalGraphView.ZOOM_TO_FIT_MS);

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
				const card = this.cardById.get(cid);
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
		new ButtonComponent(section)
			.setButtonText('Open file')
			.setClass('arete-global-graph-detail-btn')
			.onClick(async () => {
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
			.map((e) => this.cardById.get(e.toId))
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
			.map((e) => this.cardById.get(e.fromId))
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
					return this.cardById.get(otherId);
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
		new ButtonComponent(section)
			.setButtonText('Open in editor')
			.setClass('arete-global-graph-detail-btn')
			.onClick(async () => {
				await this.openFileAtLine(node.filePath, node.lineNumber);
			});
	}

	// --- Helpers ---

	/**
	 * For each node id: the nodes one link away (either direction) and the indexes in
	 * `links` of the links that touch it. Accepts endpoints as ids or as resolved nodes.
	 */
	private indexLinks(links: GlobalLink[]): {
		neighbours: Map<string, Set<string>>;
		incident: Map<string, number[]>;
	} {
		const neighbours = new Map<string, Set<string>>();
		const incident = new Map<string, number[]>();
		links.forEach((l, i) => {
			const src = typeof l.source === 'string' ? l.source : (l.source as GlobalNode).id;
			const tgt = typeof l.target === 'string' ? l.target : (l.target as GlobalNode).id;
			for (const [a, b] of [
				[src, tgt],
				[tgt, src],
			]) {
				let set = neighbours.get(a);
				if (!set) neighbours.set(a, (set = new Set()));
				set.add(b);
			}
			for (const id of src === tgt ? [src] : [src, tgt]) {
				let list = incident.get(id);
				if (!list) incident.set(id, (list = []));
				list.push(i);
			}
		});
		return { neighbours, incident };
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

	private fitToView: (() => void) | null = null;
	/** Re-reads the theme colours into the current 3D drawing; null when none is drawn. */
	private repaint3D: (() => void) | null = null;

	private stopSimulation(): void {
		this.searchTargets = null;
		this.repaint3D = null;
		this.resizeViewport = null;
		this.fitToView = null;
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
		if (this.searchTimer) clearTimeout(this.searchTimer);
		if (this.resizeTimer) clearTimeout(this.resizeTimer);
		this.stopSimulation();
		this.tooltipComponent.unload();
	}
}
