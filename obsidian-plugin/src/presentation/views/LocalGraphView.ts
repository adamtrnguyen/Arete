/**
 * LocalGraphView - Physics-based dependency graph utilizing D3.js
 *
 * Replaces the static column view with an interactive force-directed graph
 * that mimics Obsidian's native graph view behavior.
 */

import { ItemView, WorkspaceLeaf, setIcon, setTooltip, TFile, Component } from 'obsidian';
import * as d3 from 'd3';

import { DependencyResolver } from '@/application/services/DependencyResolver';
import { LocalGraphResult } from '@/domain/graph/types';
import { CardRenderer } from '@/presentation/renderers/CardRenderer';
import { debugLog } from '@/domain/debugLog';
import {
	destroyForceGraph3D,
	GraphColors,
	loadForceGraph3D,
	resolveGraphColors,
} from '@/presentation/graph/ForceGraph3D';
import {
	appendArrowMarker,
	applyFileClusteringForce2D,
	applyFileClusteringForce3D,
	createNodeDragBehavior,
} from '@/presentation/graph/GraphHelpers';

/** What this view needs from the plugin (main.ts satisfies it; no import of main). */
export interface LocalGraphHost {
	dependencyResolver: DependencyResolver;
	getCardContext(filePath: string): string | undefined;
	setCardContext(filePath: string, cardId: string): void;
}

export const LOCAL_GRAPH_VIEW_TYPE = 'arete-local-graph';

/** Gap between the pointer (or the canvas corner) and the hover tooltip. */
const TOOLTIP_OFFSET_PX = 12;
/** Width of the 2D hover tooltip's card, used before the tooltip has been laid out. */
const TOOLTIP_WIDTH_PX = 300;

interface GraphNode extends d3.SimulationNodeDatum {
	id: string;
	title: string;
	group: 'center' | 'prereq' | 'dependent' | 'related';
	filePath: string;
	lineNumber: number;
	radius?: number;
}

interface GraphLink extends d3.SimulationLinkDatum<GraphNode> {
	source: string | GraphNode;
	target: string | GraphNode;
	type: 'requires' | 'related';
}

export class LocalGraphView extends ItemView {
	plugin: LocalGraphHost;
	private resolver: DependencyResolver;
	private container!: HTMLElement;
	private depth = 2;
	private showRelated = true;
	private simulation: d3.Simulation<GraphNode, GraphLink> | null = null;
	private _svg: d3.Selection<SVGSVGElement, unknown, null, undefined> | null = null;
	private tooltipContainer: HTMLElement | null = null;
	private renderCardNodes = false;
	private currentFilePath: string | null = null;
	private centeredCardId: string | null = null;
	private viewMode: '2d' | '3d' = '2d';
	private clusteringEnabled = false;
	private graph3D: any = null; // 3d-force-graph instance
	/** Re-reads the theme colours into the current 3D drawing; null when none is drawn. */
	private repaint3D: (() => void) | null = null;

	/** Delay that lets bursts of leaf changes, selections and resizes settle. */
	private static readonly REFRESH_DEBOUNCE_MS = 100;
	private static nextMarkerId = 1;
	private readonly arrowId = `arete-local-arrow-${LocalGraphView.nextMarkerId++}`;
	private refreshTimer: number | null = null;
	/** Bumped by each refresh(); a refresh that is no longer the latest does not draw. */
	private refreshSeq = 0;
	/**
	 * What the current drawing shows: file, centre card, options and the resolver's data
	 * version. Null when no graph is drawn.
	 */
	private drawnKey: string | null = null;
	private drawnSize = { width: 0, height: 0 };
	/** Where the 2D layout is centred, in svg units. */
	private layoutCenter = { x: 0, y: 0 };
	/**
	 * Parent of what MarkdownRenderer attaches for the current 2D drawing (card-mode nodes
	 * and hover tooltips). A child of this view, unloaded when the drawing goes.
	 */
	private drawingComponent: Component | null = null;
	/**
	 * Hover tooltips of the current drawing, rendered on first hover, keyed by node id and
	 * the title they were rendered from, so a changed title is rendered afresh.
	 */
	private tooltipCache = new Map<string, HTMLElement>();

	constructor(leaf: WorkspaceLeaf, plugin: LocalGraphHost) {
		super(leaf);
		this.plugin = plugin;
		this.resolver = plugin.dependencyResolver;
	}

	getViewType(): string {
		return LOCAL_GRAPH_VIEW_TYPE;
	}

	getDisplayText(): string {
		return 'Arete local graph';
	}

	getIcon(): string {
		return 'network';
	}

	async onOpen(): Promise<void> {
		this.container = this.contentEl; // Safer than children[1]
		this.container.empty();
		this.container.addClass('arete-local-graph-d3');

		this.renderToolbar();

		const graphDiv = this.container.createDiv({ cls: 'arete-graph-canvas' });

		// Refresh when the canvas gains or changes size. A hidden canvas measures 0x0 and
		// refresh() skips it, so a zero size is forgotten: showing the view again at its
		// old size still refreshes. A pure size change only resizes the drawing.
		let lastSize = '';
		const observer = new ResizeObserver(([entry]) => {
			const { width, height } = entry.contentRect;
			if (width === 0 || height === 0) {
				lastSize = '';
				return;
			}
			const size = `${Math.round(width)}x${Math.round(height)}`;
			if (size === lastSize) return;
			lastSize = size;
			this.scheduleRefresh();
		});
		observer.observe(graphDiv);
		this.register(() => observer.disconnect());

		// Initial graph build
		// Ensure we capture initial file if no explicit context was set first
		if (!this.currentFilePath) {
			const activeFile = this.app.workspace.getActiveFile();
			if (activeFile && activeFile.extension === 'md') {
				this.currentFilePath = activeFile.path;
			}
		}
		await this.refresh();

		// Register events
		this.registerEvent(
			(this.app.workspace as any).on('arete:card-selected', (cardId: string) => {
				debugLog('[Arete] Received selection event:', cardId);
				this.centeredCardId = cardId;
				// If we receive a selection, we assume the file context is still valid or will handle in refresh
				this.scheduleRefresh();
			}),
		);

		this.registerEvent(
			this.app.workspace.on('active-leaf-change', () => {
				const af = this.app.workspace.getActiveFile();
				if (af && af.extension === 'md') {
					if (af.path !== this.currentFilePath) {
						this.currentFilePath = af.path;
						this.centeredCardId = null;
					}
				}
				this.scheduleRefresh();
			}),
		);
		this.registerEvent(
			this.app.vault.on('rename', (file, oldPath) => {
				if (oldPath !== this.currentFilePath) return;
				if (file instanceof TFile && file.extension === 'md') {
					this.currentFilePath = file.path;
				} else {
					this.currentFilePath = null;
					this.centeredCardId = null;
				}
				this.drawnKey = null;
				this.scheduleRefresh();
			}),
		);
		this.registerEvent(
			this.app.vault.on('delete', (file) => {
				if (file.path !== this.currentFilePath) return;
				this.currentFilePath = null;
				this.centeredCardId = null;
				this.drawnKey = null;
				this.scheduleRefresh();
			}),
		);
		// The 2D drawing follows the theme through CSS; a 3D one repaints in place, which
		// neither rebuilds it nor restarts its layout.
		this.registerEvent(this.app.workspace.on('css-change', () => this.repaint3D?.()));
		// No workspace 'resize' listener: the ResizeObserver above sees every size change
		// of the canvas, including those a workspace resize causes.
	}

	/** Refresh after events settle; refresh() itself skips a picture that is unchanged. */
	private scheduleRefresh(): void {
		if (this.refreshTimer) window.clearTimeout(this.refreshTimer);
		this.refreshTimer = window.setTimeout(() => {
			this.refreshTimer = null;
			void this.refresh();
		}, LocalGraphView.REFRESH_DEBOUNCE_MS);
	}

	renderToolbar() {
		const toolbar = this.container.createDiv({ cls: 'arete-graph-toolbar' });

		// Refresh Button
		const refreshBtn = toolbar.createDiv({ cls: 'clickable-icon' });
		setIcon(refreshBtn, 'refresh-cw');
		setTooltip(refreshBtn, 'Rebuild graph');
		refreshBtn.addEventListener('click', () => {
			void (async () => {
				try {
					await this.resolver.buildGraph(true);
					await this.refresh();
				} catch (e) {
					console.error('[Arete] Failed to rebuild local graph:', e);
					const canvas = this.container.querySelector<HTMLElement>('.arete-graph-canvas');
					if (canvas) {
						this.renderEmpty(
							canvas,
							`Could not load the graph: ${(e as Error).message}`,
						);
					}
				}
			})();
		});

		// Render Mode Toggle
		const renderToggle = toolbar.createDiv({
			cls: 'clickable-icon' + (this.renderCardNodes ? ' is-active' : ''),
		});
		setIcon(renderToggle, 'layout-template'); // icon for 'card' view
		setTooltip(renderToggle, 'Toggle card rendering');
		renderToggle.addEventListener('click', () => {
			this.renderCardNodes = !this.renderCardNodes;
			renderToggle.toggleClass('is-active', this.renderCardNodes);
			void this.refresh();
		});

		// Related Toggle
		const relatedToggle = toolbar.createDiv({
			cls: 'clickable-icon' + (this.showRelated ? ' is-active' : ''),
		});
		setIcon(relatedToggle, 'link');
		setTooltip(relatedToggle, 'Toggle related links');
		relatedToggle.addEventListener('click', () => {
			this.showRelated = !this.showRelated;
			relatedToggle.toggleClass('is-active', this.showRelated);
			void this.refresh();
		});

		// Depth Select
		toolbar.createSpan({ text: 'Depth:', cls: 'arete-graph-toolbar-label' });
		const depthSelect = toolbar.createEl('select');
		[1, 2, 3, 4].forEach((d) => {
			const opt = depthSelect.createEl('option', { value: String(d), text: String(d) });
			if (d === this.depth) opt.selected = true;
		});
		depthSelect.addEventListener('change', () => {
			this.depth = parseInt(depthSelect.value);
			void this.refresh();
		});

		// Separator
		toolbar.createSpan({ text: '|', cls: 'arete-graph-toolbar-sep' });

		// 2D/3D Toggle
		const dimensionToggle = toolbar.createDiv({
			cls: 'clickable-icon' + (this.viewMode === '3d' ? ' is-active' : ''),
		});
		setIcon(dimensionToggle, this.viewMode === '3d' ? 'box' : 'square');
		setTooltip(dimensionToggle, 'Toggle 2D/3D view');
		dimensionToggle.addEventListener('click', () => {
			this.viewMode = this.viewMode === '2d' ? '3d' : '2d';
			setIcon(dimensionToggle, this.viewMode === '3d' ? 'box' : 'square');
			dimensionToggle.toggleClass('is-active', this.viewMode === '3d');
			void this.refresh();
		});

		// Clustering Toggle
		const clusterToggle = toolbar.createDiv({
			cls: 'clickable-icon' + (this.clusteringEnabled ? ' is-active' : ''),
		});
		setIcon(clusterToggle, 'group');
		setTooltip(clusterToggle, 'Toggle clustering by file');
		clusterToggle.addEventListener('click', () => {
			this.clusteringEnabled = !this.clusteringEnabled;
			clusterToggle.toggleClass('is-active', this.clusteringEnabled);
			void this.refresh();
		});
	}

	// Public method to explicitly set context (robustness)
	async setContext(filePath: string, cardId?: string | null) {
		debugLog('[Arete] Explicit context set:', filePath, cardId);
		this.currentFilePath = filePath;
		this.centeredCardId = cardId || null;
		if (cardId) {
			this.plugin.setCardContext(filePath, cardId);
		}
		await this.refresh();
	}

	private getFallbackFile(): TFile | null {
		// Try to find *any* markdown file if we are lost
		const leaves = this.app.workspace.getLeavesOfType('markdown');
		if (leaves.length > 0) {
			const view = leaves[0].view as any;
			if (view.file instanceof TFile) return view.file;
		}
		return null;
	}

	async refresh() {
		debugLog('[Arete] Refreshing...');
		const canvas = this.container.querySelector('.arete-graph-canvas') as HTMLElement;
		if (!canvas) {
			console.error('[Arete] No canvas element found');
			return;
		}
		// A hidden view measures 0x0 and draws nothing, so it does not fetch the graph
		// either; the ResizeObserver refreshes once the view is shown.
		if (canvas.clientWidth === 0 || canvas.clientHeight === 0) return;
		const seq = ++this.refreshSeq;

		// 1. Get Data Resolution Strategy
		// Priority:
		// 1. Explicit / persisted currentFilePath (updated by setContext, clicks, and active-leaf-change)
		// 2. Active File (if markdown)
		// 3. Fallback to any open markdown file

		let targetPath: string | null = this.currentFilePath;
		if (!targetPath) {
			const activeFile = this.app.workspace.getActiveFile();
			if (activeFile && activeFile.extension === 'md') {
				targetPath = activeFile.path;
				this.currentFilePath = targetPath;
			} else {
				const fallback = this.getFallbackFile();
				if (fallback) {
					targetPath = fallback.path;
					this.currentFilePath = targetPath;
					debugLog('[Arete] Using fallback file:', targetPath);
				}
			}
		}

		if (!targetPath) {
			debugLog('[Arete] No active file resolveable');
			this.renderEmpty(canvas, 'No active file selected.');
			return;
		}

		const file = this.app.vault.getAbstractFileByPath(targetPath);
		if (!(file instanceof TFile)) {
			this.renderEmpty(canvas, 'File not found: ' + targetPath);
			return;
		}

		debugLog('[Arete] Rendering for:', targetPath);

		const cache = this.app.metadataCache.getFileCache(file);
		const cards = cache?.frontmatter?.arete === true ? cache.frontmatter.cards : undefined;
		const filePath = targetPath; // Alias for consistent usage below

		if (!cards || !Array.isArray(cards) || cards.length === 0) {
			debugLog('[Arete] No cards found in frontmatter');
			this.renderEmpty(canvas, 'No Arete cards found in this file.');
			return;
		}
		debugLog('[Arete] Found cards:', cards.length);

		// determine center card:
		let targetCardId = this.centeredCardId;

		// 1. Check central context first if no explicit target set
		if (!targetCardId && this.plugin.getCardContext(filePath)) {
			targetCardId = this.plugin.getCardContext(filePath) || null;
		}

		// Verify if targetCardId still exists in file
		const cardExists = targetCardId && cards.find((c: any) => c.id === targetCardId);

		if (!cardExists) {
			// 2. Check context again (maybe file changed but context is fresh?)
			const contextId = this.plugin.getCardContext(filePath);
			if (contextId && cards.find((c: any) => c.id === contextId)) {
				targetCardId = contextId;
			} else {
				// 3. Fallback to first card
				const firstCard = cards.find((c: any) => c.id);
				targetCardId = null; // never fall back to another file's card
				if (firstCard) {
					const firstId = String(firstCard.id);
					targetCardId = firstId;
					// Update context to match default
					this.centeredCardId = firstId;
					this.plugin.setCardContext(filePath, firstId);
					debugLog('[Arete] Defaulting to first card:', firstId);
				}
			}
		} else {
			// If we found a valid target, ensure context is synced
			if (targetCardId) {
				this.plugin.setCardContext(filePath, targetCardId);
			}
		}

		if (!targetCardId) {
			// Ids are assigned on first sync, and the graph is keyed by id.
			this.renderEmpty(
				canvas,
				'These cards have no Arete IDs yet. Sync the note to add them to the graph.',
			);
			return;
		}

		// The picture depends on these and on the graph data. The version is read before
		// the fetch, so a graph dropped while it loads does not match next time.
		const key = JSON.stringify([
			filePath,
			targetCardId,
			this.depth,
			this.viewMode,
			this.showRelated,
			this.renderCardNodes,
			this.clusteringEnabled,
			this.resolver.version,
		]);
		if (key === this.drawnKey) {
			this.resizeDrawing(canvas);
			return;
		}

		// Cached; dropped on any markdown edit (main.ts) or the refresh button. An uncached
		// build asks the Python side (~3 s), so say so instead of showing a stale message.
		this.renderEmpty(canvas, 'Loading graph…');
		try {
			await this.resolver.buildGraph();
		} catch (e) {
			if (seq !== this.refreshSeq) return;
			this.renderEmpty(canvas, `Could not load the graph: ${(e as Error).message}`);
			return;
		}
		if (seq !== this.refreshSeq) return; // a later refresh owns the canvas

		// Use resolver to get structured data
		const localGraph = this.resolver.getLocalGraph(targetCardId, this.depth);
		if (!localGraph) {
			this.renderEmpty(
				canvas,
				'This card is not in the graph yet. Sync, then press refresh.',
			);
			return;
		}

		// 2. Transform Data for D3
		const { nodes, links } = this.transformData(localGraph);
		debugLog(`[Arete] Transformed: ${nodes.length} nodes, ${links.length} links`);

		if (nodes.length === 0) {
			this.renderEmpty(canvas, 'Graph is empty: no nodes found.');
			return;
		}

		// 3. Render based on view mode. If the sidebar collapsed while buildGraph() was
		// awaiting, leave drawnKey unset so the next visible resize draws the graph.
		if (canvas.clientWidth === 0 || canvas.clientHeight === 0) return;
		const rendered =
			this.viewMode === '3d'
				? this.render3DGraph(canvas, nodes, links)
				: this.renderD3Graph(canvas, nodes, links);
		if (rendered === false || canvas.clientWidth === 0 || canvas.clientHeight === 0) return;
		this.drawnKey = key;
		this.drawnSize = { width: canvas.clientWidth, height: canvas.clientHeight };
	}

	/** Fit the current drawing to the canvas size, keeping its layout. */
	private resizeDrawing(canvas: HTMLElement): void {
		const width = canvas.clientWidth;
		const height = canvas.clientHeight;
		if (width === this.drawnSize.width && height === this.drawnSize.height) return;
		this.drawnSize = { width, height };
		if (this.viewMode === '3d') {
			this.graph3D?.width(width).height(height);
		} else {
			// Keep the layout centre in the middle of the canvas.
			const { x, y } = this.layoutCenter;
			this._svg?.attr('viewBox', [x - width / 2, y - height / 2, width, height]);
		}
	}

	/**
	 * Release the current drawing: its rendered markdown, and the 3D graph, whose render
	 * loop otherwise keeps drawing to a detached canvas. The next drawing starts empty.
	 */
	private releaseDrawing(): void {
		if (this.simulation) {
			this.simulation.stop();
			this.simulation = null;
		}
		this._svg = null;
		if (this.drawingComponent) this.removeChild(this.drawingComponent);
		this.drawingComponent = null;
		this.tooltipCache.clear();
		if (this.graph3D) {
			destroyForceGraph3D(this.graph3D);
			this.graph3D = null;
		}
		this.repaint3D = null;
	}

	/** The single tooltip container, created on first use; hidden until a node is hovered. */
	private ensureTooltip(): HTMLElement {
		if (!this.tooltipContainer) {
			this.tooltipContainer = this.container.createDiv({ cls: 'arete-graph-tooltip' });
		}
		return this.tooltipContainer;
	}

	/**
	 * Put the tooltip beside the pointer. The tooltip is positioned inside the view's
	 * container, so the pointer's window coordinates are taken relative to that box; near
	 * the right edge the tooltip goes to the pointer's left so it stays in the view.
	 */
	private placeTooltip(event: MouseEvent): void {
		const tip = this.tooltipContainer;
		if (!tip) return;
		const box = this.container.getBoundingClientRect();
		const x = event.clientX - box.left;
		const y = event.clientY - box.top;
		const width = tip.offsetWidth || TOOLTIP_WIDTH_PX;
		const right = x + TOOLTIP_OFFSET_PX + width > box.width;
		const left = right ? Math.max(0, x - TOOLTIP_OFFSET_PX - width) : x + TOOLTIP_OFFSET_PX;
		tip.style.left = `${left}px`;
		tip.style.top = `${y + TOOLTIP_OFFSET_PX}px`;
	}

	renderEmpty(container: HTMLElement, msg: string) {
		this.drawnKey = null;
		this.releaseDrawing();
		container.empty();
		container.createDiv({ text: msg, cls: 'arete-graph-empty-msg' });
	}

	// Helper to transform LocalGraphResult to D3 format
	transformData(graph: LocalGraphResult): { nodes: GraphNode[]; links: GraphLink[] } {
		const nodes: Map<string, GraphNode> = new Map();
		const links: GraphLink[] = [];

		// Helper to add node
		const addNode = (nodeData: any, group: 'center' | 'prereq' | 'dependent' | 'related') => {
			if (!nodes.has(nodeData.id)) {
				nodes.set(nodeData.id, {
					id: nodeData.id,
					title: nodeData.title,
					group,
					filePath: nodeData.filePath,
					lineNumber: nodeData.lineNumber,
					radius: group === 'center' ? 8 : 5,
				});
			}
		};

		// 1. Add All Nodes
		addNode(graph.center, 'center');

		graph.prerequisites.forEach((n) => addNode(n, 'prereq'));
		graph.dependents.forEach((n) => addNode(n, 'dependent'));

		if (this.showRelated) {
			graph.related.forEach((n) => addNode(n, 'related'));
		}

		// 2. Add Links from Graph Data: the resolver's edges for the subgraph
		if (graph.links) {
			graph.links.forEach((edge) => {
				// Only add link if both nodes are in the graph (e.g. might be hidden related)
				if (nodes.has(edge.fromId) && nodes.has(edge.toId)) {
					// Filter related links if hidden
					if (!this.showRelated && edge.type === 'related') return;

					links.push({
						source: edge.fromId,
						target: edge.toId,
						type: edge.type,
					});
				}
			});
		} else {
			// `links` is required by the type; this guards a malformed reply.
			console.warn('[Arete] No links provided by resolver, graph may look disconnected.');
		}

		return {
			nodes: Array.from(nodes.values()),
			links,
		};
	}

	renderD3Graph(container: HTMLElement, nodes: GraphNode[], links: GraphLink[]): boolean {
		this.releaseDrawing();
		container.empty();

		const width = container.clientWidth;
		const height = container.clientHeight;

		if (width === 0 || height === 0) {
			return false; // hidden; the ResizeObserver in onOpen renders once it has a size
		}

		const drawingComponent = this.addChild(new Component());
		this.drawingComponent = drawingComponent;
		this.ensureTooltip();

		const svg = d3
			.select(container)
			.append('svg')
			.attr('width', '100%')
			.attr('height', '100%')
			.attr('viewBox', [0, 0, width, height]);

		this._svg = svg;
		this.layoutCenter = { x: width / 2, y: height / 2 };
		const g = svg.append('g');

		// Zoom
		const zoom = d3
			.zoom<SVGSVGElement, unknown>()
			.scaleExtent([0.1, 4])
			.on('zoom', (event) => g.attr('transform', event.transform));
		svg.call(zoom);

		// Simulation
		this.simulation = d3
			.forceSimulation(nodes)
			.force(
				'link',
				d3
					.forceLink(links)
					.id((d: any) => d.id)
					.distance(this.renderCardNodes ? 250 : 150),
			)
			.force('charge', d3.forceManyBody().strength(this.renderCardNodes ? -2000 : -600))
			.force('center', d3.forceCenter(width / 2, height / 2))
			.force('collide', d3.forceCollide().radius(this.renderCardNodes ? 120 : 40));

		// Apply clustering force in 2D mode if enabled
		if (this.clusteringEnabled) {
			applyFileClusteringForce2D(this.simulation, nodes, {
				forceXName: 'cluster',
				radius: 300,
			});
		}

		// Arrows
		appendArrowMarker(svg, this.arrowId, 'arete-graph-arrow');

		// Links
		const link = g
			.append('g')
			.selectAll('line')
			.data(links)
			.join('line')
			.attr('class', (d) => `arete-graph-link mod-${d.type}`)
			.attr('marker-end', (d) => (d.type === 'requires' ? `url(#${this.arrowId})` : null));

		// Drag
		const dragBehavior = createNodeDragBehavior(() => this.simulation);

		// Nodes Group
		const nodeGroup = g
			.append('g')
			.selectAll('.arete-graph-node')
			.data(nodes)
			.join('g')
			.attr('class', 'arete-graph-node')
			.call(dragBehavior as any);

		if (this.renderCardNodes) {
			// --- Card Mode ---
			const app = this.app;
			const component = drawingComponent;

			nodeGroup.each(function (d) {
				const group = d3.select(this);

				// Card Container (ForeignObject)
				const fo = group
					.append('foreignObject')
					.attr('width', 200)
					.attr('height', 150)
					.attr('x', -100)
					.attr('y', -75);

				const div = fo
					.append('xhtml:div')
					.classed('arete-graph-card', true)
					.classed('arete-card-preview', true);

				// Fetch Real Card Data
				// We have d.filePath and d.id
				const file = app.vault.getAbstractFileByPath(d.filePath);
				if (file instanceof TFile) {
					const cache = app.metadataCache.getFileCache(file);
					const cards = cache?.frontmatter?.cards || [];
					const card = cards.find((c: any) => c.id === d.id);

					if (card) {
						// Async render the card content
						void CardRenderer.render(
							app,
							div.node() as HTMLElement,
							card,
							d.filePath,
							component,
						);
					} else {
						// Fallback if card not found in cache
						div.append('div')
							.attr('class', 'arete-graph-card-error')
							.text('Card not found');
						div.append('div').text(d.id);
					}
				} else {
					div.append('div')
						.attr('class', 'arete-graph-card-error')
						.text('File not found');
					div.append('div').text(d.filePath);
				}
			});
		} else {
			// --- Dot Mode ---
			nodeGroup
				.append('circle')
				.attr('r', (d) => (d.group === 'center' ? 10 : 6))
				.attr('class', (d) => `arete-graph-dot mod-${d.group}`);

			// Labels
			nodeGroup
				.append('text')
				.text((d) => (d.title.length > 25 ? d.title.slice(0, 23) + '...' : d.title))
				.attr('class', 'arete-graph-label')
				.attr('dx', 15)
				.attr('dy', 4);

			// Hover Tooltip
			nodeGroup
				.on('mouseover', (event, d) => {
					if (this.tooltipContainer) {
						this.tooltipContainer.addClass('is-visible');
						this.placeTooltip(event);

						// Populate tooltip. A node's tooltip is rendered on its first hover in
						// this drawing and reattached on later ones.
						this.tooltipContainer.empty();
						const cacheKey = JSON.stringify([d.id, d.title]);
						const cached = this.tooltipCache.get(cacheKey);
						if (cached) {
							this.tooltipContainer.appendChild(cached);
							return;
						}
						const cardDiv = this.tooltipContainer.createDiv({
							cls: 'arete-card-preview arete-graph-tooltip-card',
						});
						this.tooltipCache.set(cacheKey, cardDiv);
						cardDiv.style.width = `${TOOLTIP_WIDTH_PX}px`;

						// The preview shows the id and title only, not the full card.
						const cardMock = {
							id: d.id,
							Front: d.title,
						};

						void CardRenderer.render(
							this.app,
							cardDiv,
							cardMock,
							d.filePath,
							drawingComponent,
						);
					}
				})
				.on('mousemove', (event) => this.placeTooltip(event))
				.on('mouseout', () => this.tooltipContainer?.removeClass('is-visible'));
		}

		// Click Interaction
		nodeGroup.on('click', (event, d) => {
			event.stopPropagation(); // prevent zoom click
			this.handleNodeClick(d);
		});

		nodeGroup.append('title').text((d) => `${d.title}\n(${d.id})`);

		this.simulation.on('tick', () => {
			link.attr('x1', (d: any) => d.source.x)
				.attr('y1', (d: any) => d.source.y)
				.attr('x2', (d: any) => d.target.x)
				.attr('y2', (d: any) => d.target.y);

			// Update group positions (handles both circles and cards)
			nodeGroup.attr('transform', (d: any) => `translate(${d.x},${d.y})`);
		});
		return true;
	}

	private handleNodeClick(node: { id: string; filePath: string }): void {
		debugLog('[Arete] Switching graph center to:', node.id);
		this.centeredCardId = node.id;
		this.currentFilePath = node.filePath;
		this.plugin.setCardContext(node.filePath, node.id);

		const activeFile = this.app.workspace.getActiveFile();
		if (activeFile && node.filePath === activeFile.path) {
			void this.refresh();
		} else {
			void this.openFile(node.filePath).then(() => this.refresh());
		}
	}

	async openFile(filePath: string) {
		this.currentFilePath = filePath;
		const file = this.app.vault.getAbstractFileByPath(filePath);
		if (file instanceof TFile) {
			await this.app.workspace.getLeaf().openFile(file);
		}
	}

	async onClose() {
		if (this.refreshTimer) window.clearTimeout(this.refreshTimer);
		this.releaseDrawing();
	}

	/** Colour a 3D drawing from the theme; also the whole of what a theme change redoes. */
	private paint3D(graph: any, colors: GraphColors): void {
		if (!graph) return;
		graph
			.backgroundColor(colors.background)
			.nodeColor((node: any) => {
				if (node.group === 'center') return colors.focused;
				if (node.group === 'prereq') return colors.green;
				if (node.group === 'dependent') return colors.orange;
				return colors.unresolved;
			})
			.linkColor((link: any) => (link.type === 'requires' ? colors.line : colors.unresolved));
	}

	/**
	 * Render the graph in 3D using 3d-force-graph.
	 */
	render3DGraph(container: HTMLElement, nodes: GraphNode[], links: GraphLink[]): boolean {
		this.releaseDrawing();
		container.empty();

		const width = container.clientWidth;
		const height = container.clientHeight;

		if (width === 0 || height === 0) {
			return false; // hidden; the ResizeObserver in onOpen renders once it has a size
		}

		// Transform links for 3d-force-graph (needs source/target as id strings)
		const graphData = {
			nodes: nodes.map((n) => ({
				id: n.id,
				title: n.title,
				group: n.group,
				filePath: n.filePath,
				lineNumber: n.lineNumber,
			})),
			links: links.map((l) => ({
				source: typeof l.source === 'string' ? l.source : l.source.id,
				target: typeof l.target === 'string' ? l.target : l.target.id,
				type: l.type,
			})),
		};

		const ForceGraph3D = loadForceGraph3D();

		// Create 3D graph
		this.graph3D = ForceGraph3D()(container)
			.width(width)
			.height(height)
			.graphData(graphData)
			.nodeLabel((node: any) => `${node.title}\n(${node.id})`)
			.nodeVal((node: any) => (node.group === 'center' ? 3 : 1))
			.linkWidth((link: any) => (link.type === 'requires' ? 2 : 1))
			.linkDirectionalArrowLength((link: any) => (link.type === 'requires' ? 4 : 0))
			.linkDirectionalArrowRelPos(1)
			.onNodeClick((node: any) => {
				this.handleNodeClick(node);
			});
		// Theme colours are read once per drawing, and again when the theme changes.
		this.repaint3D = () => this.paint3D(this.graph3D, resolveGraphColors(container));
		this.repaint3D();

		// Apply clustering force in 3D mode if enabled
		if (this.clusteringEnabled) {
			applyFileClusteringForce3D(
				this.graph3D,
				nodes.map((n) => n.filePath),
				{ radius: 300 },
			);
		}

		// Setup hover tooltip (reuse single container)
		this.ensureTooltip();

		this.graph3D.onNodeHover((node: any) => {
			if (node && this.tooltipContainer) {
				// The hover callback has no pointer position, and 3d-force-graph shows its own
				// label at the pointer, so the card sits in the canvas's top-left corner.
				this.tooltipContainer.addClass('is-visible');
				this.tooltipContainer.style.left = `${container.offsetLeft + TOOLTIP_OFFSET_PX}px`;
				this.tooltipContainer.style.top = `${container.offsetTop + TOOLTIP_OFFSET_PX}px`;
				this.tooltipContainer.empty();
				const cardDiv = this.tooltipContainer.createDiv({
					cls: 'arete-card-preview arete-graph-tooltip-card mod-3d',
				});

				// Simple preview (full CardRenderer could be added)
				cardDiv.createDiv({ text: node.title, cls: 'arete-card-title' });
				cardDiv.createDiv({ text: `ID: ${node.id}`, cls: 'arete-card-id' });
				cardDiv.createDiv({ text: `File: ${node.filePath}`, cls: 'arete-card-file' });
			} else if (this.tooltipContainer) {
				this.tooltipContainer.removeClass('is-visible');
			}
		});
		return true;
	}
}
