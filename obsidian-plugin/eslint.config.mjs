import js from '@eslint/js';
import { defineConfig } from 'eslint/config';
import tseslint from 'typescript-eslint';
import prettierRecommended from 'eslint-plugin-prettier/recommended';
import globals from 'globals';

export default defineConfig([
	// Lint only TypeScript sources (preserves the old `--ext .ts` scope);
	// never lint build output, deps, coverage, or JS/MJS build helpers.
	{ ignores: ['node_modules/', 'coverage/', '**/*.js', '**/*.cjs', '**/*.mjs'] },

	// ESLint core recommended
	js.configs.recommended,

	// typescript-eslint recommended (replaces plugin:@typescript-eslint/recommended)
	...tseslint.configs.recommended,

	// Project rules + node globals (was env: { node: true })
	{
		files: ['**/*.ts'],
		languageOptions: {
			globals: { ...globals.node },
		},
		rules: {
			'@typescript-eslint/no-unused-vars': ['warn', { argsIgnorePattern: '^_' }],
			'@typescript-eslint/no-explicit-any': 'off',
			// Rules newly escalated by ESLint 10 + typescript-eslint 8 that the
			// prior config never enforced. Keep them as warnings so this deps bump
			// doesn't turn pre-existing, intentional patterns into hard errors
			// (no source/UI changes are in scope for a deps-only release).
			'@typescript-eslint/no-require-imports': 'warn',
			'no-useless-assignment': 'warn',
		},
	},

	// Jest globals for test files
	{
		files: ['tests/**/*.ts', '**/*.test.ts'],
		languageOptions: {
			globals: { ...globals.jest },
		},
	},

	// Prettier integration — must be last (disables conflicting format rules)
	prettierRecommended,
]);
