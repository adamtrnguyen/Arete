/**
 * LocalGraphView - Physics-based dependency graph utilizing D3.js
 *
 * Replaces the static column view with an interactive force-directed graph
 * that mimics Obsidian's native graph view behavior.
 */

import { ItemView, WorkspaceLeaf, setIcon, TFile, Component } from 'obsidian';
import * as d3 from 'd3';

import { DependencyResolver } from '@/application/services/DependencyResolver';
import { LocalGraphResult } from '@/domain/graph/types';
import { CardRenderer } from '@/presentation/renderers/CardRenderer';
import { debugLog } from '@/domain/debugLog';

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

/** 3d-force-graph's kapsule call form: `ForceGraph3D()(element)`. */
type ForceGraph3DFactory = () => (element: HTMLElement) => ForceGraph3DInstance;

/**
 * The 3d-force-graph instance methods this view calls; each setter returns the instance.
 * The package's own typings import three's, which this project does not install.
 */
type ForceGraph3DInstance = {
	[K in
		| 'width'
		| 'height'
		| 'graphData'
		| 'nodeLabel'
		| 'nodeColor'
		| 'nodeVal'
		| 'linkColor'
		| 'linkWidth'
		| 'linkDirectionalArrowLength'
		| 'linkDirectionalArrowRelPos'
		| 'onNodeClick'
		| 'onNodeHover'
		| 'd3Force']: (...args: any[]) => ForceGraph3DInstance;
} & { _destructor?: () => void };

/**
 * `require('3d-force-graph')`, called on the first 3D render rather than at module top:
 * the package pulls in three.js, most of the bundle, and esbuild evaluates it on the
 * first call of this function instead of at plugin load. The package is ESM-only, so the
 * required value can be its namespace object (`{ default }`) or the function; take either.
 */
function loadForceGraph3D(): ForceGraph3DFactory {
	const mod = require('3d-force-graph') as ForceGraph3DFactory | { default: ForceGraph3DFactory };
	return 'default' in mod ? mod.default : mod;
}

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
	private container: HTMLElement;
	private depth = 2;
	private showRelated = true;
	private simulation: d3.Simulation<GraphNode, GraphLink> | null = null;
	private _svg: d3.Selection<SVGSVGElement, unknown, null, undefined> | null = null;
	private tooltipContainer: HTMLElement | null = null;
	private tooltipComponent: Component = new Component();
	private renderCardNodes = false;
	private currentFilePath: string | null = null;
	private viewMode: '2d' | '3d' = '2d';
	private clusteringEnabled = false;
	private graph3D: any = null; // 3d-force-graph instance

	/** Delay that lets bursts of leaf changes, selections and resizes settle. */
	private static readonly REFRESH_DEBOUNCE_MS = 100;
	private refreshTimer: ReturnType<typeof setTimeout> | null = null;
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
	 * and hover tooltips). A child of tooltipComponent, unloaded when the drawing goes.
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
		return 'Arete Local Graph';
	}

	getIcon(): string {
		return 'network';
	}

	async onOpen(): Promise<void> {
		this.container = this.contentEl; // Safer than children[1]
		this.container.empty();
		this.container.addClass('arete-local-graph-d3');
		this.container.style.display = 'flex';
		this.container.style.flexDirection = 'column';
		this.container.style.height = '100%';
		this.container.style.overflow = 'hidden';
		this.container.style.position = 'relative'; // For absolute tooltip

		this.renderToolbar();

		const graphDiv = this.container.createDiv({ cls: 'arete-graph-canvas' });
		graphDiv.style.flex = '1';
		graphDiv.style.overflow = 'hidden'; // SVG handles scroll via zoom
		graphDiv.style.position = 'relative';

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
		// Ensure we capture initial file
		const activeFile = this.app.workspace.getActiveFile();
		if (activeFile) this.currentFilePath = activeFile.path;
		await this.refresh();

		// Register events
		this.registerEvent(
			(this.app.workspace as any).on('arete:card-selected', (cardId: string) => {
				debugLog('[Arete Graph] Received selection event:', cardId);
				this.centeredCardId = cardId;
				// If we receive a selection, we assume the file context is still valid or will handle in refresh
				this.scheduleRefresh();
			}),
		);

		this.registerEvent(
			this.app.workspace.on('active-leaf-change', () => {
				const af = this.app.workspace.getActiveFile();
				if (af) {
					this.currentFilePath = af.path;
				}
				this.scheduleRefresh();
			}),
		);
		// No workspace 'resize' listener: the ResizeObserver above sees every size change
		// of the canvas, including those a workspace resize causes.
	}

	/** Refresh after events settle; refresh() itself skips a picture that is unchanged. */
	private scheduleRefresh(): void {
		if (this.refreshTimer) clearTimeout(this.refreshTimer);
		this.refreshTimer = setTimeout(() => {
			this.refreshTimer = null;
			void this.refresh();
		}, LocalGraphView.REFRESH_DEBOUNCE_MS);
	}

	renderToolbar() {
		const toolbar = this.container.createDiv({ cls: 'arete-graph-toolbar' });
		toolbar.style.padding = '8px';
		toolbar.style.borderBottom = '1px solid var(--background-modifier-border)';
		toolbar.style.display = 'flex';
		toolbar.style.gap = '10px';
		toolbar.style.alignItems = 'center';

		// Refresh Button
		const refreshBtn = toolbar.createDiv({ cls: 'clickable-icon' });
		setIcon(refreshBtn, 'refresh-cw');
		refreshBtn.setAttribute('title', 'Rebuild Graph');
		refreshBtn.addEventListener('click', () => {
			this.resolver.buildGraph(true).then(() => this.refresh());
		});

		// Render Mode Toggle
		const renderToggle = toolbar.createDiv({
			cls: 'clickable-icon' + (this.renderCardNodes ? ' is-active' : ''),
		});
		setIcon(renderToggle, 'layout-template'); // icon for 'card' view
		renderToggle.setAttribute('title', 'Toggle Card Rendering');
		if (this.renderCardNodes) renderToggle.style.color = 'var(--interactive-accent)';
		renderToggle.addEventListener('click', () => {
			this.renderCardNodes = !this.renderCardNodes;
			if (this.renderCardNodes) {
				renderToggle.style.color = 'var(--interactive-accent)';
				renderToggle.addClass('is-active');
			} else {
				renderToggle.style.color = '';
				renderToggle.removeClass('is-active');
			}
			this.refresh();
		});

		// Related Toggle
		const relatedToggle = toolbar.createDiv({
			cls: 'clickable-icon' + (this.showRelated ? ' is-active' : ''),
		});
		setIcon(relatedToggle, 'link');
		relatedToggle.setAttribute('title', 'Toggle Related Links');
		if (this.showRelated) relatedToggle.style.color = 'var(--interactive-accent)';
		relatedToggle.addEventListener('click', () => {
			this.showRelated = !this.showRelated;
			if (this.showRelated) relatedToggle.style.color = 'var(--interactive-accent)';
			else relatedToggle.style.color = '';
			this.refresh();
		});

		// Depth Select
		const depthLabel = toolbar.createSpan({ text: 'Depth:' });
		depthLabel.style.fontSize = '0.8em';
		const depthSelect = toolbar.createEl('select');
		[1, 2, 3, 4].forEach((d) => {
			const opt = depthSelect.createEl('option', { value: String(d), text: String(d) });
			if (d === this.depth) opt.selected = true;
		});
		depthSelect.addEventListener('change', () => {
			this.depth = parseInt(depthSelect.value);
			this.refresh();
		});

		// Separator
		toolbar.createSpan({ text: '|' }).style.color = 'var(--text-muted)';

		// 2D/3D Toggle
		const dimensionToggle = toolbar.createDiv({
			cls: 'clickable-icon',
		});
		setIcon(dimensionToggle, this.viewMode === '3d' ? 'box' : 'square');
		dimensionToggle.setAttribute('title', 'Toggle 2D/3D View');
		if (this.viewMode === '3d') dimensionToggle.style.color = 'var(--interactive-accent)';
		dimensionToggle.addEventListener('click', () => {
			this.viewMode = this.viewMode === '2d' ? '3d' : '2d';
			setIcon(dimensionToggle, this.viewMode === '3d' ? 'box' : 'square');
			if (this.viewMode === '3d') {
				dimensionToggle.style.color = 'var(--interactive-accent)';
			} else {
				dimensionToggle.style.color = '';
			}
			this.refresh();
		});

		// Clustering Toggle
		const clusterToggle = toolbar.createDiv({
			cls: 'clickable-icon' + (this.clusteringEnabled ? ' is-active' : ''),
		});
		setIcon(clusterToggle, 'group');
		clusterToggle.setAttribute('title', 'Toggle Clustering by File');
		if (this.clusteringEnabled) clusterToggle.style.color = 'var(--interactive-accent)';
		clusterToggle.addEventListener('click', () => {
			this.clusteringEnabled = !this.clusteringEnabled;
			if (this.clusteringEnabled) {
				clusterToggle.style.color = 'var(--interactive-accent)';
				clusterToggle.addClass('is-active');
			} else {
				clusterToggle.style.color = '';
				clusterToggle.removeClass('is-active');
			}
			this.refresh();
		});
	}

	// ... inside LocalGraphView class
	private centeredCardId: string | null = null;
	// ...

	// Public method to explicitly set context (robustness)
	async setContext(filePath: string, cardId?: string | null) {
		debugLog('[Arete Graph] Explicit context set:', filePath, cardId);
		this.currentFilePath = filePath;
		this.centeredCardId = cardId || null;
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
		debugLog('[Arete Graph] Refreshing...');
		const canvas = this.container.querySelector('.arete-graph-canvas') as HTMLElement;
		if (!canvas) {
			console.error('[Arete Graph] No canvas element found');
			return;
		}
		// A hidden view measures 0x0 and draws nothing, so it does not fetch the graph
		// either; the ResizeObserver refreshes once the view is shown.
		if (canvas.clientWidth === 0 || canvas.clientHeight === 0) return;
		const seq = ++this.refreshSeq;

		// 1. Get Data Resolution Strategy
		// Priority:
		// 1. Explicit Active File (if markdown)
		// 2. Persisted currentFilePath
		// 3. Fallback to any open markdown file

		let targetPath: string | null = null;

		const activeFile = this.app.workspace.getActiveFile();
		if (activeFile && activeFile.extension === 'md') {
			targetPath = activeFile.path;
			this.currentFilePath = targetPath; // Update persistence
		} else if (this.currentFilePath) {
			targetPath = this.currentFilePath;
		} else {
			// Fallback
			const fallback = this.getFallbackFile();
			if (fallback) {
				targetPath = fallback.path;
				this.currentFilePath = targetPath;
				debugLog('[Arete Graph] Using fallback file:', targetPath);
			}
		}

		if (!targetPath) {
			debugLog('[Arete Graph] No active file resolveable');
			this.renderEmpty(canvas, 'No active file selected.');
			return;
		}

		const file = this.app.vault.getAbstractFileByPath(targetPath);
		if (!(file instanceof TFile)) {
			this.renderEmpty(canvas, 'File not found: ' + targetPath);
			return;
		}

		debugLog('[Arete Graph] Rendering for:', targetPath);

		const cache = this.app.metadataCache.getFileCache(file);
		const cards = cache?.frontmatter?.arete === true ? cache.frontmatter.cards : undefined;
		const filePath = targetPath; // Alias for consistent usage below

		if (!cards || !Array.isArray(cards) || cards.length === 0) {
			debugLog('[Arete Graph] No cards found in frontmatter');
			this.renderEmpty(canvas, 'No Arete cards found in this file.');
			return;
		}
		debugLog('[Arete Graph] Found cards:', cards.length);

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
					targetCardId = firstCard.id;
					// Update context to match default
					this.centeredCardId = targetCardId;
					this.plugin.setCardContext(filePath, targetCardId);
					debugLog('[Arete Graph] Defaulting to first card:', targetCardId);
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
		debugLog(`[Arete Graph] Transformed: ${nodes.length} nodes, ${links.length} links`);

		if (nodes.length === 0) {
			this.renderEmpty(canvas, 'Graph is empty (No nodes found).');
			return;
		}

		// 3. Render based on view mode
		if (this.viewMode === '3d') {
			this.render3DGraph(canvas, nodes, links);
		} else {
			this.renderD3Graph(canvas, nodes, links);
		}
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
		if (this.drawingComponent) this.tooltipComponent.removeChild(this.drawingComponent);
		this.drawingComponent = null;
		this.tooltipCache.clear();
		if (this.graph3D) {
			this.graph3D._destructor?.();
			this.graph3D = null;
		}
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
		container.createDiv({
			text: msg,
			cls: 'arete-graph-empty-msg',
		}).style.padding = '20px';
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

		// 2. Add Links from Graph Data
		// The resolver now provides valid edges for the subgraph
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
			// Fallback for types safety if links missing (shouldn't happen with new resolver)
			console.warn(
				'[Arete Graph] No links provided by resolver, graph may look disconnected.',
			);
		}

		return {
			nodes: Array.from(nodes.values()),
			links,
		};
	}

	renderD3Graph(container: HTMLElement, nodes: GraphNode[], links: GraphLink[]) {
		container.empty();
		this.releaseDrawing();
		const drawingComponent = this.tooltipComponent.addChild(new Component());
		this.drawingComponent = drawingComponent;

		// Setup Tooltip Container if not exists
		if (!this.tooltipContainer) {
			this.tooltipContainer = this.container.createDiv({ cls: 'arete-graph-tooltip' });
			this.tooltipContainer.style.position = 'absolute';
			this.tooltipContainer.style.display = 'none';
			this.tooltipContainer.style.zIndex = '1000';
			this.tooltipContainer.style.zIndex = '1000';
			this.tooltipContainer.style.pointerEvents = 'none'; // Don't block mouse
		}
		const width = container.clientWidth;
		const height = container.clientHeight;

		if (width === 0 || height === 0) {
			return; // hidden; the ResizeObserver in onOpen renders once it has a size
		}

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
			this.applyClusteringForce(this.simulation, nodes);
		}

		// Arrows
		svg.append('defs')
			.append('marker')
			.attr('id', 'arrow')
			.attr('viewBox', '0 -5 10 10')
			.attr('refX', 20)
			.attr('refY', 0)
			.attr('markerWidth', 6)
			.attr('markerHeight', 6)
			.attr('orient', 'auto')
			.append('path')
			.attr('d', 'M0,-5L10,0L0,5')
			.attr('fill', 'var(--text-muted)');

		// Links
		const link = g
			.append('g')
			.selectAll('line')
			.data(links)
			.join('line')
			.attr('stroke', 'var(--text-muted)')
			.attr('stroke-opacity', 0.6)
			.attr('stroke-width', (d) => (d.type === 'requires' ? 2 : 1))
			.attr('stroke-dasharray', (d) => (d.type === 'related' ? '4 4' : null))
			.attr('marker-end', (d) => (d.type === 'requires' ? 'url(#arrow)' : null));

		// Drag
		const dragBehavior = d3
			.drag<any, any>()
			.on('start', (event, d) => {
				if (!event.active) this.simulation?.alphaTarget(0.3).restart();
				d.fx = d.x;
				d.fy = d.y;
			})
			.on('drag', (event, d) => {
				d.fx = event.x;
				d.fy = event.y;
			})
			.on('end', (event, d) => {
				if (!event.active) this.simulation?.alphaTarget(0);
				d.fx = null;
				d.fy = null;
			});

		// Nodes Group
		const nodeGroup = g
			.append('g')
			.selectAll('.node')
			.data(nodes)
			.join('g')
			.attr('class', 'node')
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
					.style('width', '100%')
					.style('height', '100%')
					.style('overflow', 'hidden') // Hide overflow for cleanliness
					.style('background', 'var(--background-primary)')
					.style('border', '1px solid var(--background-modifier-border)')
					.style('border-radius', '8px')
					.style('font-size', '10px')
					.style('padding', '8px')
					.style('box-shadow', '0 2px 4px rgba(0,0,0,0.1)')
					.classed('arete-card-preview', true); // Apply preview styling class

				// Fetch Real Card Data
				// We have d.filePath and d.id
				const file = app.vault.getAbstractFileByPath(d.filePath);
				if (file instanceof TFile) {
					const cache = app.metadataCache.getFileCache(file);
					const cards = cache?.frontmatter?.cards || [];
					const card = cards.find((c: any) => c.id === d.id);

					if (card) {
						// Async render the card content
						CardRenderer.render(
							app,
							div.node() as HTMLElement,
							card,
							d.filePath,
							component,
						);
					} else {
						// Fallback if card not found in cache
						div.html(
							`<div style="font-weight:bold;margin-bottom:4px;color:red">Card Not Found</div><div>${d.id}</div>`,
						);
					}
				} else {
					div.html(
						`<div style="font-weight:bold;margin-bottom:4px;color:red">File Not Found</div><div>${d.filePath}</div>`,
					);
				}
			});
		} else {
			// --- Dot Mode ---
			nodeGroup
				.append('circle')
				.attr('r', (d) => (d.group === 'center' ? 10 : 6))
				.attr('fill', (d) => {
					if (d.group === 'center') return 'var(--interactive-accent)';
					if (d.group === 'prereq') return 'var(--text-normal)';
					return 'var(--text-muted)';
				})
				.attr('stroke', 'var(--background-primary)')
				.attr('stroke-width', 2)
				.style('cursor', 'pointer');

			// Labels
			nodeGroup
				.append('text')
				.text((d) => (d.title.length > 25 ? d.title.slice(0, 23) + '...' : d.title))
				.attr('font-size', '10px')
				.attr('dx', 15)
				.attr('dy', 4)
				.attr('fill', 'var(--text-muted)')
				.style('pointer-events', 'none');

			// Hover Tooltip
			nodeGroup
				.on('mouseover', async (event, d) => {
					if (this.tooltipContainer) {
						this.tooltipContainer.style.display = 'block';
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
							cls: 'arete-card-preview',
						});
						this.tooltipCache.set(cacheKey, cardDiv);
						cardDiv.style.width = `${TOOLTIP_WIDTH_PX}px`;
						cardDiv.style.maxHeight = '300px';
						cardDiv.style.overflow = 'auto';

						// Re-construct basic card object for display
						// In a real scenario we'd want the full card from cache
						const cardMock = {
							id: d.id,
							Front: d.title,
							// We'd need to fetch more data for full preview
							// For now showing ID and Title is a start
						};

						await CardRenderer.render(
							this.app,
							cardDiv,
							cardMock,
							d.filePath,
							drawingComponent,
						);
					}
				})
				.on('mousemove', (event) => this.placeTooltip(event))
				.on('mouseout', () => {
					if (this.tooltipContainer) {
						this.tooltipContainer.style.display = 'none';
					}
				});
		}

		// Click Interaction
		nodeGroup.on('click', async (event, d) => {
			event.stopPropagation(); // prevent zoom click

			// If node is in same file, re-center graph
			const activeFile = this.app.workspace.getActiveFile();
			if (activeFile && d.filePath === activeFile.path) {
				debugLog('[Arete] Switching graph center to:', d.id);
				this.centeredCardId = d.id;
				this.refresh();
			} else {
				// Navigate to different file
				await this.openFile(d.filePath);
			}
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
	}

	async openFile(filePath: string) {
		this.currentFilePath = filePath;
		const file = this.app.vault.getAbstractFileByPath(filePath);
		if (file instanceof TFile) {
			await this.app.workspace.getLeaf().openFile(file);
		}
	}

	async onClose() {
		if (this.refreshTimer) clearTimeout(this.refreshTimer);
		if (this.simulation) this.simulation.stop();
		this.releaseDrawing();
		this.tooltipComponent.unload();
	}

	/**
	 * Compute cluster centroids based on filePath for grouping nodes.
	 */
	private getClusterCentroids(
		nodes: GraphNode[],
	): Map<string, { x: number; y: number; z: number }> {
		const clusters = new Map<string, GraphNode[]>();
		nodes.forEach((n) => {
			const key = n.filePath || 'unknown';
			if (!clusters.has(key)) clusters.set(key, []);
			clusters.get(key)!.push(n);
		});

		const centroids = new Map<string, { x: number; y: number; z: number }>();
		let i = 0;
		const clusterCount = clusters.size;
		clusters.forEach((_, key) => {
			// Distribute clusters in a circle for 2D, sphere for 3D
			const angle = (i / clusterCount) * 2 * Math.PI;
			const radius = 300;
			centroids.set(key, {
				x: Math.cos(angle) * radius,
				y: Math.sin(angle) * radius,
				z: (i % 2 === 0 ? 1 : -1) * 100, // Alternate z for 3D depth
			});
			i++;
		});
		return centroids;
	}

	/**
	 * Custom clustering force that pulls nodes toward their cluster centroid.
	 */
	private applyClusteringForce(
		simulation: d3.Simulation<GraphNode, GraphLink>,
		nodes: GraphNode[],
		strength = 0.3,
	) {
		const centroids = this.getClusterCentroids(nodes);

		simulation.force(
			'cluster',
			d3
				.forceX<GraphNode>()
				.x((d) => centroids.get(d.filePath)?.x || 0)
				.strength(strength),
		);
		simulation.force(
			'clusterY',
			d3
				.forceY<GraphNode>()
				.y((d) => centroids.get(d.filePath)?.y || 0)
				.strength(strength),
		);
	}

	/**
	 * Render the graph in 3D using 3d-force-graph.
	 */
	render3DGraph(container: HTMLElement, nodes: GraphNode[], links: GraphLink[]) {
		container.empty();
		this.releaseDrawing();

		const width = container.clientWidth;
		const height = container.clientHeight;

		if (width === 0 || height === 0) {
			return; // hidden; the ResizeObserver in onOpen renders once it has a size
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
				source: typeof l.source === 'string' ? l.source : (l.source as GraphNode).id,
				target: typeof l.target === 'string' ? l.target : (l.target as GraphNode).id,
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
			.nodeColor((node: any) => {
				if (node.group === 'center') return '#7c3aed'; // purple
				if (node.group === 'prereq') return '#10b981'; // green
				if (node.group === 'dependent') return '#f59e0b'; // amber
				return '#6b7280'; // gray for related
			})
			.nodeVal((node: any) => (node.group === 'center' ? 3 : 1))
			.linkColor((link: any) => (link.type === 'requires' ? '#6b7280' : '#d1d5db'))
			.linkWidth((link: any) => (link.type === 'requires' ? 2 : 1))
			.linkDirectionalArrowLength((link: any) => (link.type === 'requires' ? 4 : 0))
			.linkDirectionalArrowRelPos(1)
			.onNodeClick((node: any) => {
				debugLog('[Arete Graph 3D] Clicked:', node.id);
				this.centeredCardId = node.id;
				this.currentFilePath = node.filePath;
				this.refresh();
			});

		// Apply clustering force in 3D mode if enabled
		if (this.clusteringEnabled) {
			const centroids = this.getClusterCentroids(nodes);
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
			// Note: 3d-force-graph uses d3-force-3d which handles Z via its own simulation
		}

		// Setup hover tooltip (reuse single container)
		if (!this.tooltipContainer) {
			this.tooltipContainer = this.container.createDiv({ cls: 'arete-graph-tooltip' });
			this.tooltipContainer.style.position = 'absolute';
			this.tooltipContainer.style.display = 'none';
			this.tooltipContainer.style.zIndex = '1000';
			this.tooltipContainer.style.pointerEvents = 'none';
		}

		this.graph3D.onNodeHover((node: any) => {
			if (node && this.tooltipContainer) {
				// The hover callback has no pointer position, and 3d-force-graph shows its own
				// label at the pointer, so the card sits in the canvas's top-left corner.
				this.tooltipContainer.style.display = 'block';
				this.tooltipContainer.style.left = `${container.offsetLeft + TOOLTIP_OFFSET_PX}px`;
				this.tooltipContainer.style.top = `${container.offsetTop + TOOLTIP_OFFSET_PX}px`;
				this.tooltipContainer.empty();
				const cardDiv = this.tooltipContainer.createDiv({ cls: 'arete-card-preview' });
				cardDiv.style.width = '250px';
				cardDiv.style.maxHeight = '200px';
				cardDiv.style.overflow = 'auto';
				cardDiv.style.background = 'var(--background-primary)';
				cardDiv.style.border = '1px solid var(--background-modifier-border)';
				cardDiv.style.borderRadius = '8px';
				cardDiv.style.padding = '8px';
				cardDiv.style.boxShadow = '0 4px 12px rgba(0,0,0,0.2)';

				// Simple preview (full CardRenderer could be added)
				cardDiv.createEl('div', {
					text: node.title,
					cls: 'arete-card-title',
				}).style.fontWeight = 'bold';
				cardDiv.createEl('div', {
					text: `ID: ${node.id}`,
					cls: 'arete-card-id',
				}).style.fontSize = '0.8em';
				cardDiv.createEl('div', {
					text: `File: ${node.filePath}`,
					cls: 'arete-card-file',
				}).style.fontSize = '0.75em';
			} else if (this.tooltipContainer) {
				this.tooltipContainer.style.display = 'none';
			}
		});
	}
}
