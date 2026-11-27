import { defineConfig } from 'eslint/config';
import obsidianmd from 'eslint-plugin-obsidianmd';
import tseslint from 'typescript-eslint';
import eslintConfigPrettier from 'eslint-config-prettier';
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
	{
		name: 'obsidian',
		importNames: ['requestUrl'],
		message: 'HTTP requests belong in src/infrastructure/arete only.',
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
	{
		selector: 'ImportExpression',
		message: 'Use static imports; import() defers nothing in this CJS bundle.',
	}, // R9
];
const PARSE_ONCE = {
	selector: "CallExpression[callee.property.name='parseCards']",
	message: 'Read parsed cards from cardRangesField; do not parse the note again.',
}; // R8

const ratchet = (selector, message) => ({
	meta: { type: 'suggestion', schema: [] },
	create: (context) => ({ [selector]: (node) => context.report({ node, message }) }),
});

const aretePlugin = {
	rules: {
		'no-emoji': ratchet(
			'Literal[value=/\\p{Extended_Pictographic}/u], TemplateElement[value.raw=/\\p{Extended_Pictographic}/u]',
			'No emoji in UI text; use setIcon with a Lucide icon.',
		), // N4
		// Excludes iframe accessibility title (frame.title).
		'no-title-tooltip': ratchet(
			"CallExpression[callee.property.name='setAttribute'][arguments.0.value=/^(title|aria-label)$/], AssignmentExpression[left.property.name='title'][left.object.type!='ThisExpression'][left.object.name!='frame'], Property[key.name='attr'] > ObjectExpression > Property:matches([key.name='title'], [key.value='title'], [key.value='aria-label'])",
			'Tooltips go through setTooltip(el, text).',
		), // N5
		'no-hand-built-button': ratchet(
			"CallExpression[callee.property.name='createEl'][arguments.0.value='button']",
			'Use ButtonComponent (or ExtraButtonComponent for an icon button).',
		), // N5
		'no-heading-element': ratchet(
			"CallExpression[callee.property.name='createEl'][arguments.0.value=/^h[1-6]$/]",
			'Modal titles use this.setTitle(…).',
		), // N5
		'no-literal-delay': ratchet(
			":matches(CallExpression[callee.name='setTimeout'], CallExpression[callee.property.name='setTimeout'])[arguments.1.type='Literal'][arguments.1.value>0]",
			'Name the delay: a *_MS constant.',
		), // R2
		'no-keystroke-save': ratchet(
			"CallExpression[callee.property.name='addText'] CallExpression[callee.property.name='saveSettings']",
			'A text field saves on every keystroke; debounce it or save on blur.',
		), // T2
		'log-prefix': {
			meta: { type: 'suggestion', schema: [] },
			create: (context) => ({
				CallExpression(node) {
					const { callee } = node;
					const isConsole =
						callee.type === 'MemberExpression' &&
						callee.object.type === 'Identifier' &&
						callee.object.name === 'console' &&
						callee.property.type === 'Identifier' &&
						['log', 'warn', 'error', 'info', 'debug'].includes(callee.property.name);
					const isDebugLog = callee.type === 'Identifier' && callee.name === 'debugLog';
					if (!isConsole && !isDebugLog) return;
					const first = node.arguments[0];
					if (!first) return;
					let text = null;
					if (first.type === 'Literal' && typeof first.value === 'string') {
						text = first.value;
					} else if (first.type === 'TemplateLiteral' && first.quasis.length > 0) {
						text = first.quasis[0].value.cooked ?? first.quasis[0].value.raw;
					}
					if (text !== null && !text.startsWith('[Arete]')) {
						context.report({
							node: first,
							message: 'Console and debugLog messages must start with "[Arete]".',
						});
					}
				},
			}),
		}, // G2
	},
};

const disableTypeCheckedObsidian = Object.fromEntries(
	Object.keys(obsidianmd.ruleConfigs.recommendedTypeChecked).map((rule) => [rule, 'off']),
);

export default defineConfig([
	// Never lint build output, deps, coverage, or JS/MJS build helpers.
	{ ignores: ['node_modules/', 'coverage/', 'main.js', '**/*.js', '**/*.cjs', '**/*.mjs'] },

	// Official Obsidian plugin rules (includes core ESLint recommended,
	// typescript-eslint recommendedTypeChecked, @microsoft/sdl, no-unsanitized,
	// manifest.json / LICENSE / package.json validation).
	...obsidianmd.configs.recommended,

	// Dev-only tooling/test dependencies are never bundled into main.js.
	{
		files: ['package.json'],
		rules: {
			'depend/ban-dependencies': [
				'error',
				{
					presets: ['native', 'microutilities', 'preferred'],
					allowed: ['builtin-modules', 'js-yaml'],
				},
			],
		},
	},

	// Type-checked linting for plugin source files via tsconfig.json.
	{
		files: ['src/**/*.ts'],
		languageOptions: {
			parserOptions: {
				projectService: true,
				tsconfigRootDir: import.meta.dirname,
			},
		},
		plugins: { arete: aretePlugin },
		rules: {
			// T3: shipped arete-* command ids stay fixed so user hotkeys and gui-test never break.
			'obsidianmd/commands/no-plugin-id-in-command-id': 'off',
			// T4: keep imperative display() until minAppVersion is raised to 1.13.
			'obsidianmd/settings-tab/prefer-setting-definitions': 'off',
			// N1: sentence case for UI strings with Arete's domain proper nouns/acronyms.
			'obsidianmd/ui/sentence-case': [
				'error',
				{
					ignoreWords: ['Arete', 'AnkiConnect', 'AnkiDirect', 'MathJax', 'Python'],
					acronyms: [
						'FSRS',
						'YAML',
						'ULID',
						'SM-2',
						'CLI',
						'CSS',
						'HTML',
						'JSON',
						'URL',
						'ID',
					],
				},
			],
			// Obsidian's FrontMatterCache and D3 callbacks are typed `any` upstream; keep
			// the real bug-catching type-checked rules (no-floating-promises, no-misused-promises,
			// prefer-promise-reject-errors, no-base-to-string, unbound-method, etc.) active.
			'@typescript-eslint/no-explicit-any': 'off',
			'@typescript-eslint/no-unsafe-assignment': 'off',
			'@typescript-eslint/no-unsafe-member-access': 'off',
			'@typescript-eslint/no-unsafe-call': 'off',
			'@typescript-eslint/no-unsafe-argument': 'off',
			'@typescript-eslint/no-unsafe-return': 'off',
			// X2: forbid @ts-ignore; require @ts-expect-error with description.
			'@typescript-eslint/ban-ts-comment': 'error',
			'@typescript-eslint/no-unused-vars': ['warn', { argsIgnorePattern: '^_' }],
			'@typescript-eslint/no-require-imports': ['error', { allow: ['^3d-force-graph$'] }],
			'no-restricted-imports': ['error', { paths: [LAZY_3D], patterns: [RELATIVE] }],
			'no-restricted-syntax': ['error', ...SYNTAX],
			'arete/no-emoji': 'error',
			'arete/no-title-tooltip': 'error',
			'arete/no-hand-built-button': 'error',
			'arete/no-literal-delay': 'error',
			'arete/no-keystroke-save': 'error',
			'arete/log-prefix': 'error',
		},
	},
	{
		files: ['src/**/*.ts'],
		ignores: ['src/infrastructure/arete/**'],
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
		files: ['src/presentation/modals/**/*.ts'],
		rules: { 'arete/no-heading-element': 'error' },
	},
	// S6: CardPreviewFrame populates a sandboxed iframe (allow-same-origin, scripts disabled).
	// Allow a single described inline disable for innerHTML here only.
	{
		files: ['src/presentation/renderers/CardPreviewFrame.ts'],
		rules: {
			'eslint-comments/no-restricted-disable': [
				'error',
				'obsidianmd/*',
				'!obsidianmd/no-misused-promises',
				'no-console',
				'no-restricted-globals',
				'@typescript-eslint/no-restricted-imports',
				'no-alert',
				'@typescript-eslint/no-deprecated',
				'@typescript-eslint/no-explicit-any',
				'@microsoft/sdl/no-document-write',
				'no-eval',
				'obsidianmd/no-nodejs-modules',
			],
		},
	},
	// G1: debugLog is the single debug-mode-gated wrapper around console.log.
	{
		files: ['src/domain/debugLog.ts'],
		rules: {
			'obsidianmd/rule-custom-message': 'off',
			'no-new-func': 'error',
		},
	},

	// Test files: syntax-only TS linting + Jest globals (tsconfig.json excludes tests/).
	{
		files: ['tests/**/*.ts', '**/*.test.ts'],
		...tseslint.configs.disableTypeChecked,
		languageOptions: {
			...tseslint.configs.disableTypeChecked.languageOptions,
			globals: { ...globals.node, ...globals.jest },
		},
		rules: {
			...tseslint.configs.disableTypeChecked.rules,
			...disableTypeCheckedObsidian,
			'@typescript-eslint/no-explicit-any': 'off',
			'@typescript-eslint/no-unused-vars': ['warn', { argsIgnorePattern: '^_' }],
			'no-useless-assignment': 'warn',
			// Test mocks simulate MarkdownRenderer.render by writing innerHTML on stub elements.
			'@microsoft/sdl/no-inner-html': 'off',
			'no-unsanitized/property': 'off',
		},
	},

	// Prettier compatibility — must be last (disables rules that conflict with Prettier)
	eslintConfigPrettier,
]);
