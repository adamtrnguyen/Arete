/** @type {import('dependency-cruiser').IConfiguration} */
module.exports = {
	forbidden: [
		// Domain Isolation: Domain cannot import from any outer layer
		{
			name: 'domain-isolation',
			comment: 'Domain layer must be pure and cannot depend on outer layers',
			severity: 'error',
			from: { path: '^src/domain' },
			to: { path: '^src/(application|infrastructure|presentation)' },
		},
		// Infrastructure cannot import from Presentation
		{
			name: 'infrastructure-no-presentation',
			comment: 'Infrastructure cannot depend on Presentation',
			severity: 'error',
			from: { path: '^src/infrastructure' },
			to: { path: '^src/presentation' },
		},
		// Application cannot import from Presentation
		{
			name: 'application-no-presentation',
			comment: 'Application cannot depend on Presentation',
			severity: 'error',
			from: { path: '^src/application' },
			to: { path: '^src/presentation' },
		},
		// L1: domain imports only domain (no obsidian, npm package or Node builtin)
		{
			name: 'domain-imports-only-domain',
			comment: 'L1: domain is pure: no obsidian, no npm package, no Node builtin, no outer layer.',
			severity: 'error',
			from: { path: '^src/domain' },
			to: { pathNot: '^src/domain' },
		},
		// L2: services depend on ports
		{
			name: 'application-no-infrastructure',
			comment: 'L2 / PL3: services depend on ports in src/domain/ports.ts.',
			severity: 'error',
			from: { path: '^src/application' },
			to: { path: '^src/infrastructure' },
		},
		// L3: main.ts is imported by nobody
		{
			name: 'nothing-imports-main',
			comment: 'L3 / PL2: hosts are declared as types; main.ts is imported by nobody.',
			severity: 'error',
			from: { pathNot: '^src/main\\.ts$' },
			to: { path: '^src/main\\.ts$' },
		},
		// Circular dependencies (ignore main.ts which must import views for registration)
		{
			name: 'no-circular',
			comment: 'Circular dependencies are not allowed',
			severity: 'error',
			from: { pathNot: '^src/main\\.ts$' },
			to: { circular: true },
		},
	],
	options: {
		doNotFollow: {
			path: 'node_modules',
		},
		tsPreCompilationDeps: true,
		tsConfig: {
			fileName: 'tsconfig.json',
		},
		enhancedResolveOptions: {
			exportsFields: ['exports'],
			conditionNames: ['import', 'require', 'node', 'default'],
		},
		reporterOptions: {
			dot: {
				collapsePattern: 'node_modules/(@[^/]+/[^/]+|[^/]+)',
			},
		},
	},
};
