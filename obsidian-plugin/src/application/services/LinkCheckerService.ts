import { App, TFile } from 'obsidian';
import AretePlugin from '@/main';

export interface BrokenReference {
	sourceFile: TFile;
	linkText: string;
	linkPath: string; // The resolved path we tried to find
	type: 'link' | 'image' | 'invalid-yaml';
	errorMessage?: string; // Detailed error from CLI
	position: {
		start: { line: number; col: number; offset: number };
		end: { line: number; col: number; offset: number };
	} | null;
}

export class LinkCheckerService {
	app: App;
	plugin: AretePlugin;

	constructor(app: App, plugin: AretePlugin) {
		this.app = app;
		this.plugin = plugin;
	}

	/**
	 * Scans the provided files (or all markdown files if none provided) for broken links/embeds.
	 */
	async checkIntegrity(files?: TFile[]): Promise<BrokenReference[]> {
		const targetFiles = files || this.app.vault.getMarkdownFiles();
		const allBroken: BrokenReference[] = [];

		for (const file of targetFiles) {
			// 1. Check Links & Embeds (Images Only)
			const brokenRefs = this.getBrokenReferences(file);
			allBroken.push(...brokenRefs);

			// 2. Check Invalid Frontmatter (User Request)
			const invalidYaml = await this.getInvalidFrontmatter(file);
			if (invalidYaml) {
				allBroken.push(invalidYaml);
			}
		}

		return allBroken;
	}

	getBrokenReferences(file: TFile): BrokenReference[] {
		const cache = this.app.metadataCache.getFileCache(file);
		if (!cache) return [];

		const broken: BrokenReference[] = [];

		const hasYamlCards =
			cache.frontmatter && cache.frontmatter.cards && Array.isArray(cache.frontmatter.cards);

		if (hasYamlCards) {
			// 1. Deep Scan YAML Cards (User Request: "Only report inside the card list")
			const cards = cache.frontmatter.cards;
			cards.forEach((card: any, idx: number) => {
				if (!card) return; // Skip null cards
				// Fields to check: Front, Back, Text, Extra
				const fields = [
					card.Front,
					card.front,
					card.Back,
					card.back,
					card.Text,
					card.text,
					card.Extra,
					card.extra,
				];

				fields.forEach((content) => {
					if (typeof content === 'string') {
						this.scanTextForBrokenLinks(content, file, broken, `Card #${idx + 1}`);
					}
				});
			});
		} else {
			// 2. Standard Body Scan (Only if NOT a YAML card file)

			// Check Links
			// Check Embeds (Images, Transclusions)
			if (cache.embeds) {
				for (const embed of cache.embeds) {
					const dest = this.app.metadataCache.getFirstLinkpathDest(embed.link, file.path);
					if (!dest) {
						// STRICT CHECK: Only report if it has a known image extension
						const imageExtensions = [
							'.png',
							'.jpg',
							'.jpeg',
							'.gif',
							'.bmp',
							'.svg',
							'.webp',
						];
						const lower = embed.link.toLowerCase();
						const hasImageExt = imageExtensions.some((ext) => lower.endsWith(ext));

						if (hasImageExt) {
							broken.push({
								sourceFile: file,
								linkText: embed.original,
								linkPath: embed.link,
								type: 'image',
								position: embed.position,
							});
						}
					}
				}
			}
		}

		return broken;
	}

	private scanTextForBrokenLinks(
		text: string,
		file: TFile,
		brokenList: BrokenReference[],
		context: string,
	) {
		// Regex for [[wikilinks]] and ![[embeds]]
		const linkRegex = /(!?)\[\[([^|\]]+)(?:\|[^\]]+)?\]\]/g;
		let match;

		while ((match = linkRegex.exec(text)) !== null) {
			const isEmbed = match[1] === '!';
			const linkPath = match[2]; // The path part
			const original = match[0];

			const dest = this.app.metadataCache.getFirstLinkpathDest(linkPath, file.path);

			if (!dest) {
				// Frontmatter Context: Report ALL broken embeds, regardless of extension.
				// User wants "important embeds" in Cards to be flagged.
				if (isEmbed) {
					brokenList.push({
						sourceFile: file,
						linkText: `${context}: ${original}`, // Add context since we lack line numbers
						linkPath: linkPath,
						type: 'image',
						position: null,
					});
				}
			}
		}
	}

	async getInvalidFrontmatter(file: TFile): Promise<BrokenReference | null> {
		const cache = this.app.metadataCache.getFileCache(file);

		// If cache parses frontmatter fine, we are good.
		if (cache && cache.frontmatter) return null;

		// If no frontmatter in cache, check if file actually HAS valid-looking YAML start
		const content = await this.app.vault.read(file);
		const trimmed = content.trimStart();

		// Check for empty frontmatter explicitly (e.g. --- followed directly by ---, or just whitespace)
		if (/^---\s*\n\s*---/.test(trimmed)) {
			return {
				sourceFile: file,
				linkText: 'EMPTY YAML',
				linkPath: 'Frontmatter',
				type: 'invalid-yaml',
				errorMessage: 'Empty Frontmatter. Please add content or remove the block.',
				position: null,
			};
		}

		if (trimmed.startsWith('---\n') || trimmed.startsWith('---\r\n')) {
			// File has YAML block start, but cache didn't parse it -> Invalid!

			// Attempt to get detailed reason from CLI
			let reason = 'Obsidian failed to parse frontmatter.';
			try {
				let fullPath = file.path;
				const adapter = this.app.vault.adapter;
				if ('getBasePath' in adapter) {
					// eslint-disable-next-line @typescript-eslint/ban-ts-comment
					// @ts-ignore
					const basePath = adapter.getBasePath();
					fullPath = `${basePath}/${file.path}`;
				}

				const checkRes = await this.plugin.checkService.getCheckResult(fullPath);
				if (!checkRes.ok && checkRes.errors && checkRes.errors.length > 0) {
					// Use the first error as the summary
					const firstErr = checkRes.errors[0];
					reason = `Line ${firstErr.line}: ${firstErr.message}`;
				}
			} catch (e: any) {
				console.error('Failed to run detailed check', e);
				reason = `Check Failed: ${e.message || e}`;
			}

			return {
				sourceFile: file,
				linkText: 'INVALID YAML',
				linkPath: 'Frontmatter',
				type: 'invalid-yaml',
				errorMessage: reason,
				position: null,
			};
		}
		return null;
	}
}
