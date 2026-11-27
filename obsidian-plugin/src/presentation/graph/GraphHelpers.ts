import * as d3 from 'd3';

export interface ClusterCentroid {
	x: number;
	y: number;
	z: number;
}

export function hashToHue(str: string): number {
	let hash = 0;
	for (let i = 0; i < str.length; i++) {
		hash = str.charCodeAt(i) + ((hash << 5) - hash);
	}
	return Math.abs(hash) % 360;
}

/**
 * Distribute distinct file clusters around a ring (with alternating z depth for 3D).
 */
export function computeFileClusterCentroids(
	filePaths: Iterable<string>,
	radius?: number,
): Map<string, ClusterCentroid> {
	const files = new Set<string>();
	for (const fp of filePaths) {
		files.add(fp || 'unknown');
	}
	const centroids = new Map<string, ClusterCentroid>();
	const count = files.size;
	if (count === 0) return centroids;

	const ringRadius = radius ?? Math.max(300, count * 30);
	let i = 0;
	for (const fp of files) {
		const angle = (i / count) * 2 * Math.PI;
		centroids.set(fp, {
			x: Math.cos(angle) * ringRadius,
			y: Math.sin(angle) * ringRadius,
			z: (i % 2 === 0 ? 1 : -1) * 100,
		});
		i++;
	}
	return centroids;
}

export function applyFileClusteringForce2D<N extends d3.SimulationNodeDatum & { filePath: string }>(
	simulation: d3.Simulation<N, any>,
	nodes: N[],
	options: { forceXName?: string; radius?: number; strength?: number } = {},
): void {
	const { forceXName = 'clusterX', radius, strength = 0.3 } = options;
	const centroids = computeFileClusterCentroids(
		nodes.map((n) => n.filePath),
		radius,
	);
	simulation.force(
		forceXName,
		d3
			.forceX<N>()
			.x((d) => centroids.get(d.filePath)?.x || 0)
			.strength(strength),
	);
	simulation.force(
		'clusterY',
		d3
			.forceY<N>()
			.y((d) => centroids.get(d.filePath)?.y || 0)
			.strength(strength),
	);
}

export function applyFileClusteringForce3D(
	graph3D: any,
	filePaths: Iterable<string>,
	options: { radius?: number; strength?: number } = {},
): void {
	const { radius, strength = 0.3 } = options;
	const centroids = computeFileClusterCentroids(filePaths, radius);
	graph3D
		.d3Force(
			'clusterX',
			d3
				.forceX<any>()
				.x((d: any) => centroids.get(d.filePath)?.x || 0)
				.strength(strength),
		)
		.d3Force(
			'clusterY',
			d3
				.forceY<any>()
				.y((d: any) => centroids.get(d.filePath)?.y || 0)
				.strength(strength),
		);
}

export function appendArrowMarker(
	svg: d3.Selection<SVGSVGElement, unknown, null, undefined>,
	id: string,
	arrowClass: string,
): void {
	svg.append('defs')
		.append('marker')
		.attr('id', id)
		.attr('viewBox', '0 -5 10 10')
		.attr('refX', 20)
		.attr('refY', 0)
		.attr('markerWidth', 6)
		.attr('markerHeight', 6)
		.attr('orient', 'auto')
		.append('path')
		.attr('d', 'M0,-5L10,0L0,5')
		.attr('class', arrowClass);
}

export function createNodeDragBehavior(
	getSimulation: () => d3.Simulation<any, any> | null,
): d3.DragBehavior<any, any, any> {
	return d3
		.drag<any, any>()
		.on('start', (event, d) => {
			if (!event.active) getSimulation()?.alphaTarget(0.3).restart();
			d.fx = d.x;
			d.fy = d.y;
		})
		.on('drag', (event, d) => {
			d.fx = event.x;
			d.fy = event.y;
		})
		.on('end', (event, d) => {
			if (!event.active) getSimulation()?.alphaTarget(0);
			d.fx = null;
			d.fy = null;
		});
}
