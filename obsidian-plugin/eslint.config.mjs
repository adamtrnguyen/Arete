import js from '@eslint/js';
import { defineConfig } from 'eslint/config';
import tseslint from 'typescript-eslint';
import prettierRecommended from 'eslint-plugin-prettier/recommended';
import globals from 'globals';

// Layer and lifecycle rules (docs: obsidian-plugin/CLAUDE.md). Rule ids in parentheses.
const LAZY_3D = {
	name: '3d-force-graph',
	message:
		'Load it on first 3D render through loadForceGraph3D(); a static import evaluates three.js at plugin load.',
}; // R7
const RELATIVE = {
	group: ['./*', '../*'],
	message: 'Import through a path alias, not a relative path.',
}; // L5
const BACKEND_ONLY = [
	{ name: 'child_process', message: 'Processes are spawned in src/infrastructure/arete only.' },
	{
		name: 'fs',
		message:
			'File system access is in src/infrastructure/arete only; use the vault API elsewhere.',
	},
]; // L4

// Selectors that flag nothing today. They share one rule id, so none of them may be bulk-suppressed.
const SYNTAX = [
	{
		selector:
			"CallExpression[callee.property.name='read'][callee.object.property.name='vault']",
		message: 'Use vault.cachedRead(); vault.read() always goes to disk.',
	}, // R4
	{
		selector:
			":matches(CallExpression[callee.name='setInterval'], CallExpression[callee.property.name='setInterval']):not(CallExpression[callee.property.name='registerInterval'] > CallExpression)",
		message: 'Use this.registerInterval(window.setInterval(...)).',
	}, // C2
	{
		selector:
			"CallExpression[callee.property.name='addEventListener'][callee.object.name=/^(window|document|activeWindow|activeDocument)$/]",
		message: 'Use this.registerDomEvent(...) for window/document listeners.',
	}, // C2
	{
		selector:
			"ClassDeclaration[superClass.name=/^(GutterMarker|WidgetType)$/]:not(:has(MethodDefinition[key.name='eq']))",
		message:
			'GutterMarker/WidgetType subclasses implement eq(); the default re-renders every update.',
	}, // R8
	{
		selector: "Program > VariableDeclaration CallExpression[callee.name='require']",
		message: 'No top-level require(): it runs at plugin load.',
	}, // R7
];
const PARSE_ONCE = {
	selector: "CallExpression[callee.property.name='parseCards']",
	message: 'Read parsed cards from cardRangesField; do not parse the note again.',
}; // R8

// Rules with violations on record. Each has its own rule id (arete/*) so that bulk suppressions
// (eslint-suppressions.json) count per concern: a new violation of one kind cannot hide behind
// a fixed violation of another. Re-record with: npm run lint:rebaseline
const ratchet = (selector, message) => ({
	meta: { type: 'suggestion', schema: [] },
	create: (context) => ({ [selector]: (node) => context.report({ node, message }) }),
});
const aretePlugin = {
	rules: {
		'no-literal-inline-style': ratchet(
			"AssignmentExpression[left.object.property.name='style'][right.type='Literal']",
			'A constant style belongs in a CSS class (src/styles/*.css).',
		), // S1
		'no-emoji': ratchet(
			'Literal[value=/\\p{Extended_Pictographic}/u], TemplateElement[value.raw=/\\p{Extended_Pictographic}/u]',
			'No emoji in UI text; use setIcon with a Lucide icon.',
		), // N4
		'no-title-tooltip': ratchet(
			"CallExpression[callee.property.name='setAttribute'][arguments.0.value=/^(title|aria-label)$/], AssignmentExpression[left.property.name='title'][left.object.type!='ThisExpression'], Property[key.name='attr'] > ObjectExpression > Property:matches([key.name='title'], [key.value='title'], [key.value='aria-label'])",
			'Tooltips go through setTooltip(el, text).',
		), // N5
		'no-hand-built-button': ratchet(
			"CallExpression[callee.property.name='createEl'][arguments.0.value='button']",
			'Use ButtonComponent (or ExtraButtonComponent for an icon button).',
		), // N5
		'no-heading-element': ratchet(
			"CallExpression[callee.property.name='createEl'][arguments.0.value=/^h[1-6]$/]",
			'Settings headings use new Setting(el).setName(…).setHeading(); modal titles use this.setTitle(…).',
		), // N2, N5
		'no-literal-delay': ratchet(
			"CallExpression[callee.name='setTimeout'][arguments.1.type='Literal'][arguments.1.value>0]",
			'Name the delay: a *_MS constant.',
		), // R2
		'no-keystroke-save': ratchet(
			"CallExpression[callee.property.name='addText'] CallExpression[callee.property.name='saveSettings']",
			'A text field saves on every keystroke; debounce it or save on blur.',
		), // T2
	},
};

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

	// Plugin source rules. A later block replaces a rule's options, so blocks repeat shared lists.
	{
		files: ['src/**/*.ts'],
		plugins: { arete: aretePlugin },
		rules: {
			'no-console': ['error', { allow: ['error', 'warn'] }], // G1
			'no-restricted-imports': ['error', { paths: [LAZY_3D], patterns: [RELATIVE] }],
			'no-restricted-syntax': ['error', ...SYNTAX],
			'@typescript-eslint/no-require-imports': ['error', { allow: ['^3d-force-graph$'] }],
			'arete/no-literal-inline-style': 'error',
			'arete/no-emoji': 'error',
			'arete/no-title-tooltip': 'error',
			'arete/no-hand-built-button': 'error',
			'arete/no-literal-delay': 'error',
			'arete/no-keystroke-save': 'error',
		},
	},
	{
		files: ['src/**/*.ts'],
		ignores: ['src/infrastructure/**'],
		rules: {
			'no-restricted-imports': [
				'error',
				{ paths: [LAZY_3D, ...BACKEND_ONLY], patterns: [RELATIVE] },
			],
		},
	},
	{
		files: ['src/presentation/**/*.ts', 'src/main.ts'],
		ignores: ['src/presentation/extensions/CardGutterExtension.ts'],
		rules: { 'no-restricted-syntax': ['error', ...SYNTAX, PARSE_ONCE] },
	},
	{
		files: ['src/presentation/settings/**/*.ts', 'src/presentation/modals/**/*.ts'],
		rules: { 'arete/no-heading-element': 'error' },
	},
	{ files: ['src/domain/debugLog.ts'], rules: { 'no-console': 'off' } },

	// Prettier integration — must be last (disables conflicting format rules)
	prettierRecommended,
]);
