const fs = jest.requireActual('fs') as typeof import('fs');
import * as path from 'path';

const SRC = path.resolve(__dirname, '../src');

/** Files whose console and debugLog messages are checked (the shell, settings, services). */
const FILES = [
	'main.ts',
	'presentation/settings/SettingTab.ts',
	'presentation/modals/CheckResultModal.ts',
	'presentation/modals/DependencySelectionModal.ts',
	'presentation/components/DependencyField.ts',
	'presentation/views/CardYamlEditorView.ts',
	'presentation/renderers/CardPreviewFrame.ts',
	'presentation/renderers/CardRenderer.ts',
	...fs
		.readdirSync(path.join(SRC, 'application/services'))
		.map((f) => `application/services/${f}`),
	...fs
		.readdirSync(path.join(SRC, 'infrastructure/arete'))
		.map((f) => `infrastructure/arete/${f}`),
];

const CALL = /\b(?:console\.(?:log|warn|error|info|debug)|debugLog)\(\s*(['"`])([^'"`]{0,24})/g;

describe('log prefix', () => {
	test.each(FILES)(
		'%s starts every literal console and debugLog message with [Arete]',
		(file) => {
			const source = fs.readFileSync(path.join(SRC, file), 'utf8');
			const stray: string[] = [];
			for (const match of source.matchAll(CALL)) {
				if (!match[2].startsWith('[Arete]')) stray.push(match[0]);
			}
			expect(stray).toEqual([]);
		},
	);
});
