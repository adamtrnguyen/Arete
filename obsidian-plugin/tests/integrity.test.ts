import './test-setup';
import { App, Notice, TFile } from 'obsidian';
import AretePlugin from '@/main';
import { LinkCheckerService } from '@application/services/LinkCheckerService';

describe('AretePlugin Integrity Check', () => {
	let plugin: AretePlugin;
	let app: App;
	let cachedRead: jest.Mock;

	beforeEach(() => {
		jest.clearAllMocks();
		app = new App();
		cachedRead = jest.fn();
		(app.vault as any).cachedRead = cachedRead;
		plugin = new AretePlugin(app, { dir: 'test-plugin-dir' } as any);
		plugin.linkCheckerService = new LinkCheckerService(app, { getCheckResult: jest.fn() });
	});

	test('reports notes with a YAML block the cache could not parse, reading only those', async () => {
		const goodFile = { path: 'Good.md' } as TFile;
		const badFile = { path: 'Bad.md' } as TFile;
		const plainFile = { path: 'Plain.md' } as TFile;
		(app.vault.getMarkdownFiles as jest.Mock).mockReturnValue([goodFile, badFile, plainFile]);
		cachedRead.mockImplementation(async (file: TFile) =>
			file.path === 'Bad.md' ? '---\n\tTabError: True\n---\nContent' : '',
		);
		(app.metadataCache.getFileCache as jest.Mock).mockImplementation((file: TFile) => {
			if (file.path === 'Good.md') return { frontmatter: { title: 'Good' } };
			if (file.path === 'Bad.md') return { sections: [{ type: 'thematicBreak' }] };
			return { sections: [{ type: 'paragraph' }] };
		});
		const consoleError = jest.spyOn(console, 'error').mockImplementation(() => undefined);
		jest.spyOn(console, 'log').mockImplementation(() => undefined);

		await plugin.checkVaultIntegrity();

		expect(consoleError).toHaveBeenCalledTimes(1);
		expect(consoleError).toHaveBeenCalledWith(expect.stringContaining('[FAIL] Bad.md'));
		expect(Notice).toHaveBeenCalledWith(
			'Found 1 files with Invalid Properties (Check Console)',
		);
		expect(cachedRead).toHaveBeenCalledTimes(1);
		expect(app.vault.read).not.toHaveBeenCalled();
		consoleError.mockRestore();
	});

	test('says so when every note parses', async () => {
		(app.vault.getMarkdownFiles as jest.Mock).mockReturnValue([{ path: 'Good.md' }]);
		(app.metadataCache.getFileCache as jest.Mock).mockReturnValue({ frontmatter: {} });
		jest.spyOn(console, 'log').mockImplementation(() => undefined);

		await plugin.checkVaultIntegrity();

		expect(Notice).toHaveBeenCalledWith(
			'Integrity Check Passed (Obsidian parses all YAML correctly)',
		);
	});
});
