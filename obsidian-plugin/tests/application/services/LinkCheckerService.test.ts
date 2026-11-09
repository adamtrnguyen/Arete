import '../../test-setup';
import { App, TFile } from 'obsidian';
import { LinkCheckerService } from '@application/services/LinkCheckerService';

describe('LinkCheckerService', () => {
	let service: LinkCheckerService;
	let app: App;
	let plugin: any;

	beforeEach(() => {
		jest.clearAllMocks();
		app = new App();
		plugin = {
			checkService: {
				getCheckResult: jest.fn(),
			},
		};
		(app.vault as any).cachedRead = jest.fn();
		service = new LinkCheckerService(app, plugin.checkService);
	});

	describe('checkIntegrity', () => {
		test('scans markdown files and aggregates results', async () => {
			const file = { path: 'test.md' } as TFile;
			(app.vault.getMarkdownFiles as jest.Mock).mockReturnValue([file]);
			(app.metadataCache.getFileCache as jest.Mock).mockReturnValue({
				frontmatter: { arete: true, cards: [{ Front: '![[broken]]' }] },
			});
			(app.metadataCache.getFirstLinkpathDest as jest.Mock).mockReturnValue(null);

			const results = await service.checkIntegrity();
			expect(results).toHaveLength(1);
			expect(results[0].linkPath).toBe('broken');
		});
	});

	describe('getBrokenReferences', () => {
		test('ignores cards in a note without arete: true', () => {
			const file = { path: 'plain.md' } as TFile;
			(app.metadataCache.getFileCache as jest.Mock).mockReturnValue({
				frontmatter: { cards: [{ Front: '![[broken]]' }] },
			});
			(app.metadataCache.getFirstLinkpathDest as jest.Mock).mockReturnValue(null);

			expect(service.getBrokenReferences(file)).toHaveLength(0);
		});

		test('scans YAML cards for broken embeds', () => {
			const file = { path: 'test.md' } as TFile;
			(app.metadataCache.getFileCache as jest.Mock).mockReturnValue({
				frontmatter: {
					arete: true,
					cards: [{ Front: '![[broken-img]]', Back: 'Valid content' }],
				},
			});
			(app.metadataCache.getFirstLinkpathDest as jest.Mock).mockReturnValue(null);

			const broken = service.getBrokenReferences(file);
			expect(broken).toHaveLength(1);
			expect(broken[0].linkPath).toBe('broken-img');
			expect(broken[0].type).toBe('image');
		});

		test('scans standard body embeds for images', () => {
			const file = { path: 'test.md' } as TFile;
			(app.metadataCache.getFileCache as jest.Mock).mockReturnValue({
				embeds: [
					{ link: 'missing.png', original: '![[missing.png]]', position: {} },
					{ link: 'transclusion.md', original: '![[transclusion.md]]', position: {} },
				],
			});
			(app.metadataCache.getFirstLinkpathDest as jest.Mock).mockReturnValue(null);

			const broken = service.getBrokenReferences(file);
			expect(broken).toHaveLength(1);
			expect(broken[0].linkPath).toBe('missing.png');
		});
	});

	describe('getInvalidFrontmatter', () => {
		test('returns null for valid frontmatter', async () => {
			(app.metadataCache.getFileCache as jest.Mock).mockReturnValue({ frontmatter: {} });
			const result = await service.getInvalidFrontmatter({} as any);
			expect(result).toBeNull();
		});

		test('detects empty YAML', async () => {
			(app.metadataCache.getFileCache as jest.Mock).mockReturnValue({});
			((app.vault as any).cachedRead as jest.Mock).mockResolvedValue('---\n---');

			const result = await service.getInvalidFrontmatter({} as any);
			expect(result?.linkText).toBe('EMPTY YAML');
		});

		test('detects invalid YAML and uses CLI for reason', async () => {
			const file = { path: 'invalid.md' } as TFile;
			(app.metadataCache.getFileCache as jest.Mock).mockReturnValue({});
			((app.vault as any).cachedRead as jest.Mock).mockResolvedValue(
				'---\ninvalid: [unclosed',
			);
			((app.vault.adapter as any).getBasePath as jest.Mock).mockReturnValue('/mock/vault');
			plugin.checkService.getCheckResult.mockResolvedValue({
				ok: false,
				errors: [{ line: 2, message: 'Unclosed array' }],
			});

			const result = await service.getInvalidFrontmatter(file);
			expect(result?.linkText).toBe('INVALID YAML');
			expect(result?.errorMessage).toContain('Line 2: Unclosed array');
		});

		test('handles CLI check failure gracefully', async () => {
			const file = { path: 'invalid.md' } as TFile;
			(app.metadataCache.getFileCache as jest.Mock).mockReturnValue({});
			((app.vault as any).cachedRead as jest.Mock).mockResolvedValue('---\ninvalid: yaml');
			plugin.checkService.getCheckResult.mockRejectedValue(new Error('CLI error'));

			const result = await service.getInvalidFrontmatter(file);
			expect(result?.errorMessage).toContain('Check Failed: CLI error');
		});
	});

	describe('integrity scan cost', () => {
		const note = (path: string) => ({ path }) as TFile;

		test('reads through the cache, never from disk', async () => {
			(app.metadataCache.getFileCache as jest.Mock).mockReturnValue({});
			((app.vault as any).cachedRead as jest.Mock).mockResolvedValue('# Title');
			await service.getInvalidFrontmatter(note('a.md'));
			expect((app.vault as any).cachedRead).toHaveBeenCalledTimes(1);
			expect(app.vault.read).not.toHaveBeenCalled();
		});

		test('does not read a note whose first section cannot start a YAML block', async () => {
			for (const type of ['paragraph', 'heading', 'list', 'callout']) {
				(app.metadataCache.getFileCache as jest.Mock).mockReturnValue({
					sections: [{ type }],
				});
				expect(await service.findUnparsedFrontmatter(note('a.md'))).toBeNull();
			}
			(app.metadataCache.getFileCache as jest.Mock).mockReturnValue({ sections: [] });
			expect(await service.findUnparsedFrontmatter(note('blank.md'))).toBeNull();
			expect((app.vault as any).cachedRead).not.toHaveBeenCalled();
		});

		test('reads a note whose first section may be a YAML block', async () => {
			for (const type of ['yaml', 'thematicBreak', 'code', 'something-new']) {
				(app.metadataCache.getFileCache as jest.Mock).mockReturnValue({
					sections: [{ type }],
				});
				((app.vault as any).cachedRead as jest.Mock).mockResolvedValue('---\nbad: [\n---');
				expect(await service.findUnparsedFrontmatter(note('a.md'))).toBe('invalid');
			}
			expect((app.vault as any).cachedRead).toHaveBeenCalledTimes(4);
		});

		test('checks at most 4 notes at once and keeps vault order', async () => {
			const files = Array.from({ length: 10 }, (_, i) => note(`n${i}.md`));
			(app.vault.getMarkdownFiles as jest.Mock).mockReturnValue(files);
			(app.metadataCache.getFileCache as jest.Mock).mockReturnValue({});
			((app.vault as any).cachedRead as jest.Mock).mockResolvedValue('---\nbad: [');
			let inFlight = 0;
			let peak = 0;
			plugin.checkService.getCheckResult.mockImplementation(async (p: string) => {
				inFlight++;
				peak = Math.max(peak, inFlight);
				await new Promise((r) => setTimeout(r, 5 - (p.length % 3)));
				inFlight--;
				return { ok: false, errors: [{ line: 1, message: p }] };
			});

			const results = await service.checkIntegrity();
			expect(peak).toBe(4);
			expect(results.map((r) => r.sourceFile.path)).toEqual(files.map((f) => f.path));
		});
	});

	describe('scanTextForBrokenLinks', () => {
		test('detects broken wikilinks and embeds in text', () => {
			const file = { path: 'source.md' } as TFile;
			const broken: any[] = [];
			(app.metadataCache.getFirstLinkpathDest as jest.Mock).mockReturnValue(null);

			service['scanTextForBrokenLinks'](
				'![[broken-embed]] and [[broken-link]]',
				file,
				broken,
				'Test',
			);

			// Note: scanTextForBrokenLinks currently ONLY reports embeds (isEmbed === true)
			expect(broken).toHaveLength(1);
			expect(broken[0].linkPath).toBe('broken-embed');
		});

		test('ignores valid links', () => {
			const file = { path: 'source.md' } as TFile;
			const broken: any[] = [];
			(app.metadataCache.getFirstLinkpathDest as jest.Mock).mockReturnValue({
				path: 'valid.png',
			});

			service['scanTextForBrokenLinks']('![[valid-embed]]', file, broken, 'Test');
			expect(broken).toHaveLength(0);
		});
	});
});
