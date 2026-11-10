module.exports = {
	preset: 'ts-jest',
	testEnvironment: 'node',
	moduleFileExtensions: ['ts', 'js'],
	transform: {
		// Transpile only: `tsc --noEmit` (just types-obsidian, part of `just qa`) is the type check.
		'^.+\\.ts$': ['ts-jest', { tsconfig: '<rootDir>/tsconfig.jest.json' }],
	},
	transformIgnorePatterns: ['node_modules/(?!((d3.*)|internmap|delaunator|robust-predicates))'],
	testMatch: ['**/tests/**/*.test.ts'],
	// Coverage is opt-in: `npm run test:coverage` (just coverage-obsidian) turns it on and enforces the threshold.
	coverageThreshold: {
		global: {
			branches: 5,
			functions: 5,
			lines: 5,
			statements: 5,
		},
	},
	moduleNameMapper: {
		'^@/(.*)$': '<rootDir>/src/$1',
		'^@domain/(.*)$': '<rootDir>/src/domain/$1',
		'^@application/(.*)$': '<rootDir>/src/application/$1',
		'^@infrastructure/(.*)$': '<rootDir>/src/infrastructure/$1',
		'^@presentation/(.*)$': '<rootDir>/src/presentation/$1',
		'^obsidian$': '<rootDir>/tests/obsidian-mock.ts',
		'^d3$': '<rootDir>/tests/mocks/d3.mock.ts',
		'^3d-force-graph$': '<rootDir>/tests/mocks/3d-force-graph.mock.ts',
	},
	setupFilesAfterEnv: ['<rootDir>/tests/test-setup.ts'],
};
