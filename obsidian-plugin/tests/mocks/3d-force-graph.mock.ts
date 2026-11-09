// Mock for 3d-force-graph, shaped like the real package under a bundler's require(): the
// package is ESM-only, so require() yields a module namespace whose only key is `default`.
// The default is a kapsule factory, used as ForceGraph3D()(element).
const createInstance = () => {
	const graphInstance = {
		width: jest.fn().mockReturnThis(),
		height: jest.fn().mockReturnThis(),
		graphData: jest.fn().mockReturnThis(),
		nodeLabel: jest.fn().mockReturnThis(),
		nodeColor: jest.fn().mockReturnThis(),
		nodeVal: jest.fn().mockReturnThis(),
		linkColor: jest.fn().mockReturnThis(),
		linkWidth: jest.fn().mockReturnThis(),
		linkDirectionalArrowLength: jest.fn().mockReturnThis(),
		linkDirectionalArrowRelPos: jest.fn().mockReturnThis(),
		onNodeClick: jest.fn().mockReturnThis(),
		onNodeHover: jest.fn().mockReturnThis(),
		d3Force: jest.fn().mockReturnThis(),
		_destructor: jest.fn(),
	};
	return graphInstance;
};

const ForceGraph3D = jest.fn(() => jest.fn((_element: unknown) => createInstance()));

export default ForceGraph3D;
