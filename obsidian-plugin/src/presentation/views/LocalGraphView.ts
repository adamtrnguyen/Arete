/**
 * LocalGraphView - Physics-based dependency graph utilizing D3.js
 *
 * Replaces the static column view with an interactive force-directed graph
 * that mimics Obsidian's native graph view behavior.
 */

import { ItemView, WorkspaceLeaf, setIcon, TFile, Component } from 'obsidian';
import * as d3 from 'd3';
// eslint-disable-next-line @typescript-eslint/no-var-requires
const ForceGraph3D = require('3d-force-graph');
import type AretePlugin from '@/main';
import { DependencyResolver } from '@/application/services/DependencyResolver';
import { LocalGraphResult } from '@/domain/graph/types';
import { CardRenderer } from '@/presentation/renderers/CardRenderer';

export const LOCAL_GRAPH_VIEW_TYPE = 'arete-local-graph';

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
	plugin: AretePlugin;
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

	constructor(leaf: WorkspaceLeaf, plugin: AretePlugin) {
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

		// Initial graph build
		// Ensure we capture initial file
		const activeFile = this.app.workspace.getActiveFile();
		if (activeFile) this.currentFilePath = activeFile.path;
		await this.refresh();

		// Register events
		this.registerEvent(
			(this.app.workspace as any).on('arete:card-selected', (cardId: string) => {
				console.log('[Arete Graph] Received selection event:', cardId);
				this.centeredCardId = cardId;
				// If we receive a selection, we assume the file context is still valid or will handle in refresh
				this.refresh();
			}),
		);

		this.registerEvent(
			this.app.workspace.on('active-leaf-change', () => {
				const af = this.app.workspace.getActiveFile();
				if (af) {
					this.currentFilePath = af.path;
				}
				this.refresh();
			}),
		);
		// Update on resize
		this.registerEvent(
			this.app.workspace.on('resize', () => {
				this.refresh();
			}),
		);
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
			this.resolver.buildGraph().then(() => this.refresh());
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
		console.log('[Arete Graph] Explicit context set:', filePath, cardId);
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
		console.log('[Arete Graph] Refreshing...');
		const canvas = this.container.querySelector('.arete-graph-canvas') as HTMLElement;
		if (!canvas) {
			console.error('[Arete Graph] No canvas element found');
			return;
		}

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
				console.log('[Arete Graph] Using fallback file:', targetPath);
			}
		}

		if (!targetPath) {
			console.log('[Arete Graph] No active file resolveable');
			this.renderEmpty(canvas, 'No active file selected.');
			return;
		}

		const file = this.app.vault.getAbstractFileByPath(targetPath);
		if (!(file instanceof TFile)) {
			this.renderEmpty(canvas, 'File not found: ' + targetPath);
			return;
		}

		console.log('[Arete Graph] Rendering for:', targetPath);

		const cache = this.app.metadataCache.getFileCache(file);
		const cards = cache?.frontmatter?.cards;
		const filePath = targetPath; // Alias for consistent usage below

		if (!cards || !Array.isArray(cards) || cards.length === 0) {
			console.log('[Arete Graph] No cards found in frontmatter');
			this.renderEmpty(canvas, 'No Arete cards found in this file.');
			return;
		}
		console.log('[Arete Graph] Found cards:', cards.length);

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
				if (firstCard) {
					targetCardId = firstCard.id;
					// Update context to match default
					this.centeredCardId = targetCardId;
					this.plugin.setCardContext(filePath, targetCardId);
					console.log('[Arete Graph] Defaulting to first card:', targetCardId);
				}
			}
		} else {
			// If we found a valid target, ensure context is synced
			if (targetCardId) {
				this.plugin.setCardContext(filePath, targetCardId);
			}
		}

		if (!targetCardId) {
			console.log('[Arete Graph] No valid card ID found');
			this.renderEmpty(canvas, 'No valid cards found.');
			return;
		}

		// Force rebuild graph to ensure fresh data
		await this.resolver.buildGraph();

		// Use resolver to get structured data
		const localGraph = this.resolver.getLocalGraph(targetCardId, this.depth);
		if (!localGraph) {
			this.renderEmpty(canvas, 'Graph parsing failed or empty.');
			return;
		}

		// 2. Transform Data for D3
		const { nodes, links } = this.transformData(localGraph);
		console.log(`[Arete Graph] Transformed: ${nodes.length} nodes, ${links.length} links`);

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
	}

	renderEmpty(container: HTMLElement, msg: string) {
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
			console.warn('[Arete Graph] Container has 0 dimensions. Waiting for resize.');
			// Retry once after a delay
			setTimeout(() => this.refresh(), 100);
			return;
		}

		const svg = d3
			.select(container)
			.append('svg')
			.attr('width', '100%')
			.attr('height', '100%')
			.attr('viewBox', [0, 0, width, height]);

		this._svg = svg;
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
			const component = this.tooltipComponent;

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
						this.tooltipContainer.style.left = event.pageX + 10 + 'px';
						this.tooltipContainer.style.top = event.pageY + 10 + 'px';

						// Populate tooltip
						this.tooltipContainer.empty();
						const cardDiv = this.tooltipContainer.createDiv({
							cls: 'arete-card-preview',
						});
						cardDiv.style.width = '300px';
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
							this.tooltipComponent,
						);
					}
				})
				.on('mousemove', (event) => {
					if (this.tooltipContainer) {
						this.tooltipContainer.style.left = event.clientX + 15 + 'px';
						this.tooltipContainer.style.top = event.clientY + 15 + 'px';
					}
				})
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
				console.log('[Arete] Switching graph center to:', d.id);
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
		if (this.simulation) this.simulation.stop();
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

		// Destroy previous 3D graph if exists
		if (this.graph3D) {
			this.graph3D._destructor?.();
			this.graph3D = null;
		}

		const width = container.clientWidth;
		const height = container.clientHeight;

		if (width === 0 || height === 0) {
			console.warn('[Arete Graph 3D] Container has 0 dimensions. Waiting for resize.');
			setTimeout(() => this.refresh(), 100);
			return;
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
				console.log('[Arete Graph 3D] Clicked:', node.id);
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
				this.tooltipContainer.style.display = 'block';
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
