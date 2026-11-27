import * as path from 'path';
import { App, FileSystemAdapter, TFile } from 'obsidian';
import { isReservedCardKey } from '@/domain/cardKeys';
import { FileChecker } from '@/domain/ports';

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

/** A YAML block the metadata cache could not parse: empty, or invalid. */
export type FrontmatterProblem = 'empty' | 'invalid';

/**
 * Section types whose block cannot begin with a `---` line. A note whose first section
 * is one of these has no YAML block, so the scan does not read it.
 */
const NON_YAML_FIRST_SECTIONS = new Set([
	'paragraph',
	'heading',
	'list',
	'blockquote',
	'callout',
	'table',
	'html',
	'footnoteDefinition',
]);

/** Notes checked at once by `checkIntegrity`; each check may spawn `arete vault check`. */
const CHECK_CONCURRENCY = 4;
/** Notes read at once by `findAllUnparsedFrontmatter`, which spawns nothing. */
const READ_CONCURRENCY = 16;

/** `fn` over `items` with at most `limit` calls in flight; results keep item order. */
async function mapBounded<T, R>(
	items: T[],
	limit: number,
	fn: (item: T) => Promise<R>,
): Promise<R[]> {
	const results = new Array<R>(items.length);
	let next = 0;
	const worker = async () => {
		while (next < items.length) {
			const i = next++;
			results[i] = await fn(items[i]);
		}
	};
	await Promise.all(Array.from({ length: Math.min(limit, items.length) }, worker));
	return results;
}

export class LinkCheckerService {
	app: App;
	checkService: FileChecker;

	constructor(app: App, checkService: FileChecker) {
		this.app = app;
		this.checkService = checkService;
	}

	/**
	 * Scans the provided files (or all markdown files if none provided) for broken links/embeds.
	 */
	async checkIntegrity(files?: TFile[]): Promise<BrokenReference[]> {
		const targetFiles = files || this.app.vault.getMarkdownFiles();

		const perFile = await mapBounded(targetFiles, CHECK_CONCURRENCY, async (file) => {
			// 1. Check Links & Embeds (Images Only)
			const brokenRefs = this.getBrokenReferences(file);

			// 2. Check Invalid Frontmatter (User Request)
			const invalidYaml = await this.getInvalidFrontmatter(file);
			return invalidYaml ? [...brokenRefs, invalidYaml] : brokenRefs;
		});

		return perFile.flat();
	}

	/** The notes among `files` (in their order) whose YAML block the cache could not parse. */
	async findAllUnparsedFrontmatter(files: TFile[]): Promise<TFile[]> {
		const problems = await mapBounded(files, READ_CONCURRENCY, (file) =>
			this.findUnparsedFrontmatter(file),
		);
		return files.filter((_, i) => problems[i] !== null);
	}

	/**
	 * Whether the note opens with a YAML block that the metadata cache could not parse.
	 * The note is read (cachedRead) only when its cached sections leave that possible.
	 */
	async findUnparsedFrontmatter(file: TFile): Promise<FrontmatterProblem | null> {
		const cache = this.app.metadataCache.getFileCache(file);

		// If cache parses frontmatter fine, we are good.
		if (cache?.frontmatter) return null;
		if (cache?.sections) {
			const first = cache.sections[0];
			if (!first || NON_YAML_FIRST_SECTIONS.has(first.type)) return null;
		}

		// Strip UTF-8 BOM only; leading blank lines before `---` make a thematic break, not YAML.
		const text = (await this.app.vault.cachedRead(file)).replace(/^\uFEFF/, '');
		if (!/^---[ \t]*\r?\n/.test(text)) return null;

		// Check for empty frontmatter explicitly (e.g. --- followed directly by ---, or whitespace)
		if (/^---[ \t]*\r?\n[ \t]*---(?=[ \t]*(?:\r?\n|$))/.test(text)) return 'empty';
		// Verify a closing `---` fence exists so a leading horizontal rule is not misreported.
		if (/\r?\n---[ \t]*(?:\r?\n|$)/.test(text)) return 'invalid';
		return null;
	}

	getBrokenReferences(file: TFile): BrokenReference[] {
		const cache = this.app.metadataCache.getFileCache(file);
		if (!cache) return [];

		const broken: BrokenReference[] = [];

		const frontmatter = cache.frontmatter;
		const hasYamlCards = frontmatter?.arete === true && Array.isArray(frontmatter.cards);

		if (hasYamlCards) {
			// 1. Deep Scan YAML Cards (User Request: "Only report inside the card list")
			const cards = frontmatter.cards;
			cards.forEach((card: any, idx: number) => {
				if (!card || typeof card !== 'object') return;
				for (const [key, content] of Object.entries(card)) {
					if (isReservedCardKey(key)) continue;
					if (typeof content === 'string') {
						this.scanTextForBrokenLinks(content, file, broken, `Card #${idx + 1}`);
					}
				}
			});
		} else {
			// 2. Standard Body Scan (Only if NOT a YAML card file)

			// Check Links
			// Check Embeds (Images, Transclusions)
			if (cache.embeds) {
				for (const embed of cache.embeds) {
					const targetPath = embed.link.split('#')[0];
					if (!targetPath) continue;
					const dest = this.app.metadataCache.getFirstLinkpathDest(targetPath, file.path);
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
						const lower = targetPath.toLowerCase();
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
			const linkPath = match[2]; // The path part (may include #heading or #^block)
			const targetPath = linkPath.split('#')[0];
			if (!targetPath) continue;
			const original = match[0];

			const dest = this.app.metadataCache.getFirstLinkpathDest(targetPath, file.path);

			if (!dest) {
				// Frontmatter context: report every broken embed, whatever its extension.
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
		const problem = await this.findUnparsedFrontmatter(file);

		if (problem === 'empty') {
			return {
				sourceFile: file,
				linkText: 'EMPTY YAML',
				linkPath: 'Frontmatter',
				type: 'invalid-yaml',
				errorMessage: 'Empty Frontmatter. Please add content or remove the block.',
				position: null,
			};
		}

		if (problem === 'invalid') {
			// Attempt to get detailed reason from CLI
			let reason = 'Obsidian failed to parse frontmatter.';
			try {
				let fullPath = file.path;
				const adapter = this.app.vault.adapter;
				if ('getBasePath' in adapter) {
					const basePath = (adapter as FileSystemAdapter).getBasePath();
					if (basePath) fullPath = path.join(basePath, file.path);
				}

				const checkRes = await this.checkService.getCheckResult(fullPath);
				if (!checkRes.ok && checkRes.errors && checkRes.errors.length > 0) {
					// Use the first error as the summary
					const firstErr = checkRes.errors[0];
					reason = `Line ${firstErr.line}: ${firstErr.message}`;
				}
			} catch (e: any) {
				console.error('[Arete] Failed to run detailed check', e);
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
