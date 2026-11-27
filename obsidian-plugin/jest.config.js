module.exports = {
	testEnvironment: 'node',
	moduleFileExtensions: ['ts', 'js'],
	transform: {
		// Transpile only via esbuild: `tsc --noEmit` (just types-obsidian, part of `just qa`) is the type check.
		'^.+\\.ts$': '<rootDir>/scripts/jest-esbuild-transform.cjs',
	},
	testMatch: ['**/tests/**/*.test.ts'],
	// Coverage is opt-in: `npm run test:coverage` (just coverage-obsidian) turns it on and enforces the threshold.
	coverageProvider: 'v8',
	collectCoverageFrom: ['src/**/*.ts'],
	coverageThreshold: {
		global: {
			branches: 70,
			functions: 70,
			lines: 80,
			statements: 80,
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
