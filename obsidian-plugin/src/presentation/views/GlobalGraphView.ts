/**
 * GlobalGraphView - Vault-wide dependency graph visualization.
 *
 * Two view modes:
 * - File mode (default): Each .md file is a node, edges aggregate cross-file deps.
 * - Card mode: Individual card nodes, clustered by parent file.
 *
 * Includes a detail panel, search, and 2D/3D toggle.
 */

import { ItemView, WorkspaceLeaf, setIcon, setTooltip, TFile, Component } from 'obsidian';
import * as d3 from 'd3';

import { DependencyResolver } from '@/application/services/DependencyResolver';
import { CardNode, GlobalGraphResult, DependencyEdge } from '@/domain/graph/types';
import {
	GraphColors,
	loadForceGraph3D,
	resolveGraphColors,
} from '@/presentation/graph/ForceGraph3D';
import {
	appendArrowMarker,
	applyFileClusteringForce2D,
	applyFileClusteringForce3D,
	createNodeDragBehavior,
	hashToHue,
} from '@/presentation/graph/GraphHelpers';
import {
	CardGraphNode,
	FileGraphNode,
	GlobalGraphDetailCallbacks,
	renderCardDetailPanel,
	renderDetailEmptyPanel,
	renderFileDetailPanel,
} from '@/presentation/graph/GlobalGraphDetailPanel';

/** What this view needs from the plugin (main.ts satisfies it; no import of main). */
export interface GlobalGraphHost {
	dependencyResolver: DependencyResolver;
}

export const GLOBAL_GRAPH_VIEW_TYPE = 'arete-global-graph';

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
	private static nextMarkerId = 1;
	private readonly arrowId = `arete-global-arrow-${GlobalGraphView.nextMarkerId++}`;
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
	private fitToView: (() => void) | null = null;
	/** Re-reads the theme colours into the current 3D drawing; null when none is drawn. */
	private repaint3D: (() => void) | null = null;

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

		this.toolbarEl = this.container.createDiv({ cls: 'arete-global-graph-toolbar' });
		this.renderToolbar();

		const body = this.container.createDiv({ cls: 'arete-global-graph-body' });

		this.graphContainer = body.createDiv({ cls: 'arete-global-graph-canvas' });
		this.detailPanel = body.createDiv({ cls: 'arete-global-graph-detail' });
		this.renderDetailEmpty();

		try {
			await this.loadData();
			this.render();
		} catch (e) {
			console.error('[Arete] Failed to load global graph:', e);
			this.renderEmpty(`Could not load the graph: ${(e as Error).message}`);
		}

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

	private async loadData(force = false): Promise<void> {
		await this.resolver.buildGraph(force);
		this.graphData = this.resolver.getGlobalGraph();
		this.cardById = new Map(this.graphData.cards.map((c) => [c.id, c]));
	}

	private renderToolbar(): void {
		this.toolbarEl.empty();

		const refreshBtn = this.toolbarEl.createDiv({ cls: 'clickable-icon' });
		setIcon(refreshBtn, 'refresh-cw');
		setTooltip(refreshBtn, 'Rebuild graph');
		refreshBtn.addEventListener('click', async () => {
			try {
				await this.loadData(true);
				this.render();
			} catch (e) {
				console.error('[Arete] Failed to rebuild global graph:', e);
				this.renderEmpty(`Could not load the graph: ${(e as Error).message}`);
			}
		});

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

		this.toolbarEl.createSpan({ text: '|', cls: 'arete-global-graph-sep' });

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

		const fitBtn = this.toolbarEl.createDiv({ cls: 'clickable-icon' });
		setIcon(fitBtn, 'maximize-2');
		setTooltip(fitBtn, 'Fit to view');
		fitBtn.addEventListener('click', () => {
			if (this.fitToView) this.fitToView();
			else this.render();
		});
	}

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

	private buildAggregatedFileEdges(
		cards: CardNode[],
		requiresEdges: DependencyEdge[],
		relatedEdges: DependencyEdge[],
	): Array<{ source: string; target: string; type: 'requires' | 'related'; weight: number }> {
		const cardToFile = new Map<string, string>();
		for (const card of cards) {
			cardToFile.set(card.id, card.filePath);
		}

		const fileEdgeMap = new Map<
			string,
			{ source: string; target: string; type: 'requires' | 'related'; weight: number }
		>();
		const addEdge = (edges: DependencyEdge[], type: 'requires' | 'related') => {
			if (type === 'related' && !this.showRelated) return;
			for (const e of edges) {
				const fromFile = cardToFile.get(e.fromId);
				const toFile = cardToFile.get(e.toId);
				if (!fromFile || !toFile || fromFile === toFile) continue;
				const key = JSON.stringify([fromFile, toFile, type]);
				const existing = fileEdgeMap.get(key);
				if (existing) {
					existing.weight++;
				} else {
					fileEdgeMap.set(key, { source: fromFile, target: toFile, type, weight: 1 });
				}
			}
		};
		addEdge(requiresEdges, 'requires');
		addEdge(relatedEdges, 'related');
		return Array.from(fileEdgeMap.values());
	}

	private renderFileMode(): void {
		this.stopSimulation();
		this.graphContainer.empty();
		if (!this.graphData) return;

		const { files, cards, requiresEdges, relatedEdges } = this.graphData;
		if (files.length === 0) {
			this.renderEmpty('No Arete cards found in vault.');
			return;
		}

		const fileNodes: FileGraphNode[] = files.map((f) => ({
			id: f.path,
			basename: f.basename,
			cardCount: f.cardCount,
			cardIds: [...f.cardIds],
			radius: Math.max(8, Math.sqrt(f.cardCount) * 6),
			type: 'file' as const,
		}));

		const fileLinks: GlobalLink[] = this.buildAggregatedFileEdges(
			cards,
			requiresEdges,
			relatedEdges,
		).map((val) => ({
			source: val.source,
			target: val.target,
			edgeType: val.type,
			weight: val.weight,
		}));

		this.renderD3(this.graphContainer, fileNodes, fileLinks);
	}

	private renderCardMode(): void {
		this.stopSimulation();
		this.graphContainer.empty();
		if (!this.graphData) return;

		const { cards, requiresEdges, relatedEdges } = this.graphData;
		if (cards.length === 0) {
			this.renderEmpty('No Arete cards found in vault.');
			return;
		}

		const cardNodeSet = new Set(cards.map((c) => c.id));
		const cardNodes: CardGraphNode[] = cards.map((c) => ({
			id: c.id,
			title: c.title,
			filePath: c.filePath,
			lineNumber: c.lineNumber,
			radius: 5,
			fileHue: hashToHue(c.filePath),
			type: 'card' as const,
		}));

		const cardLinks: GlobalLink[] = requiresEdges
			.filter((e) => cardNodeSet.has(e.fromId) && cardNodeSet.has(e.toId))
			.map((e) => ({
				source: e.fromId,
				target: e.toId,
				edgeType: 'requires' as const,
				weight: 1,
			}));

		if (this.showRelated) {
			for (const e of relatedEdges) {
				if (cardNodeSet.has(e.fromId) && cardNodeSet.has(e.toId)) {
					cardLinks.push({
						source: e.fromId,
						target: e.toId,
						edgeType: 'related',
						weight: 1,
					});
				}
			}
		}

		this.renderD3(this.graphContainer, cardNodes, cardLinks);
	}

	private renderD3(container: HTMLElement, nodes: GlobalNode[], links: GlobalLink[]): void {
		container.empty();

		let width = container.clientWidth;
		let height = container.clientHeight;
		if (width === 0 || height === 0) {
			return;
		}

		const svg = d3
			.select(container)
			.append('svg')
			.attr('width', '100%')
			.attr('height', '100%')
			.attr('viewBox', [0, 0, width, height]);

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

		const zoom = d3
			.zoom<SVGSVGElement, unknown>()
			.scaleExtent([0.05, 6])
			.on('zoom', (event) => g.attr('transform', event.transform));
		svg.call(zoom);

		appendArrowMarker(svg, this.arrowId, 'arete-global-graph-arrow');

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

		if (!isFileMode && this.clusteringEnabled) {
			applyFileClusteringForce2D(sim, nodes as CardGraphNode[]);
		}

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
			.attr('marker-end', (d) =>
				d.edgeType === 'requires' ? `url(#${this.arrowId})` : null,
			);

		const dragBehavior = createNodeDragBehavior(() => sim);

		const nodeGroup = g
			.append('g')
			.selectAll('.arete-global-graph-node')
			.data(nodes)
			.join('g')
			.attr('class', `arete-global-graph-node mod-${isFileMode ? 'file' : 'card'}`)
			.call(dragBehavior as any);

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

		nodeGroup.on('click', (event, d) => {
			event.stopPropagation();
			this._selectedNodeId = d.id;
			if (isFileMode) {
				this.renderFileDetail(d as FileGraphNode);
			} else {
				this.renderCardDetail(d as CardGraphNode);
			}
		});

		nodeGroup.on('dblclick', async (event, d) => {
			event.stopPropagation();
			if (isFileMode) {
				this.viewMode = 'card';
				this.renderToolbar();
				this.render();
			} else {
				const cardNode = d as CardGraphNode;
				await this.openFileAtLine(cardNode.filePath, cardNode.lineNumber);
			}
		});

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
			.attr('fill', (d: any) => `hsl(${hashToHue(d.id)}, 60%, 55%)`);

		nodeGroup
			.append('text')
			.text((d: any) => {
				const name = d.basename;
				return name.length > 30 ? name.slice(0, 28) + '...' : name;
			})
			.attr('dx', (d: any) => d.radius + 4)
			.attr('dy', 3)
			.attr('class', GlobalGraphView.FILE_LABEL_CLASS);

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

	private renderClusterLabels(
		g: d3.Selection<SVGGElement, unknown, null, undefined>,
		nodes: CardGraphNode[],
	): void {
		g.selectAll('.arete-global-graph-file-label').remove();

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

	private renderFile3D(): void {
		this.stopSimulation();
		this.graphContainer.empty();
		if (!this.graphData) return;

		const { files, cards, requiresEdges, relatedEdges } = this.graphData;
		if (files.length === 0) {
			this.renderEmpty('No Arete cards found in vault.');
			return;
		}

		const width = this.graphContainer.clientWidth;
		const height = this.graphContainer.clientHeight;
		if (width === 0 || height === 0) {
			return;
		}

		const graphData3D = {
			nodes: files.map((f) => ({
				id: f.path,
				basename: f.basename,
				cardCount: f.cardCount,
				val: Math.max(1, Math.sqrt(f.cardCount)),
			})),
			links: this.buildAggregatedFileEdges(cards, requiresEdges, relatedEdges),
		};

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
				.nodeColor((n: any) => `hsl(${hashToHue(n.id)}, 60%, 55%)`)
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
		this.stopSimulation();
		this.graphContainer.empty();
		if (!this.graphData) return;

		const { cards, requiresEdges, relatedEdges } = this.graphData;
		if (cards.length === 0) {
			this.renderEmpty('No Arete cards found in vault.');
			return;
		}

		const width = this.graphContainer.clientWidth;
		const height = this.graphContainer.clientHeight;
		if (width === 0 || height === 0) {
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
				fileHue: hashToHue(c.filePath),
			})),
			links: allLinks,
		};

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

		if (this.clusteringEnabled) {
			applyFileClusteringForce3D(
				this.graph3D,
				cards.map((c) => c.filePath),
			);
		}
	}

	private detailCallbacks(): GlobalGraphDetailCallbacks {
		const toCardNode = (card: CardNode): CardGraphNode => ({
			id: card.id,
			title: card.title,
			filePath: card.filePath,
			lineNumber: card.lineNumber,
			radius: 5,
			fileHue: hashToHue(card.filePath),
			type: 'card',
		});
		return {
			onSelectCardFromFile: (card) => {
				this.viewMode = 'card';
				this._selectedNodeId = card.id;
				this.renderToolbar();
				this.render();
				this.renderCardDetail(toCardNode(card));
			},
			onSelectCard: (card) => {
				this._selectedNodeId = card.id;
				this.renderCardDetail(toCardNode(card));
			},
			onOpenFileAtLine: (filePath, lineNumber) => this.openFileAtLine(filePath, lineNumber),
		};
	}

	private renderDetailEmpty(): void {
		renderDetailEmptyPanel(this.detailPanel);
	}

	private renderFileDetail(node: FileGraphNode): void {
		if (!this.graphData) return;
		renderFileDetailPanel(
			this.detailPanel,
			node,
			this.graphData,
			this.cardById,
			this.detailCallbacks(),
		);
	}

	private renderCardDetail(node: CardGraphNode): void {
		if (!this.graphData) return;
		renderCardDetailPanel(
			this.detailPanel,
			node,
			this.graphData,
			this.cardById,
			this.showRelated,
			this.detailCallbacks(),
		);
	}

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
				eState: { line: Math.max(0, lineNumber - 1) },
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
