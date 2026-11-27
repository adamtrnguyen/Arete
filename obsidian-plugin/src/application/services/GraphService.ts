import { App, TFile, Notice } from 'obsidian';
import { AretePluginSettings } from '@/domain/settings';
import { ConceptStats } from '@/domain/stats';

export class GraphService {
	app: App;
	settings: AretePluginSettings;

	constructor(app: App, settings: AretePluginSettings) {
		this.app = app;
		this.settings = settings;
	}

	updateSettings(settings: AretePluginSettings) {
		this.settings = settings;
	}

	private normalizedPrefix(): string {
		return (this.settings.graph_tag_prefix ?? '').trim().replace(/^#/, '').replace(/\/+$/, '');
	}

	private matchesPrefix(tag: unknown, prefix: string): boolean {
		if (typeof tag !== 'string') return false;
		const normalized = tag.trim().replace(/^#/, '');
		return normalized === prefix || normalized.startsWith(`${prefix}/`);
	}

	private hasExactTag(rawTags: unknown, prefix: string, tagToAdd: string): boolean {
		if (rawTags == null) return false;
		const list = Array.isArray(rawTags) ? rawTags : [rawTags];
		const matches = list.filter((t) => this.matchesPrefix(t, prefix));
		return (
			matches.length === 1 &&
			typeof matches[0] === 'string' &&
			matches[0].trim().replace(/^#/, '') === tagToAdd
		);
	}

	async updateGraphTags(file: TFile, stats: ConceptStats) {
		if (!this.settings.graph_coloring_enabled) return;

		const prefix = this.normalizedPrefix();
		if (!prefix) return;

		const state = this.calculateRetentionState(stats);
		const tagToAdd = `${prefix}/${state}`;

		const cachedTags = this.app.metadataCache.getFileCache(file)?.frontmatter?.tags;
		if (cachedTags !== undefined && this.hasExactTag(cachedTags, prefix, tagToAdd)) {
			return;
		}

		await this.app.fileManager.processFrontMatter(file, (frontmatter) => {
			const rawTags = frontmatter['tags'];
			if (this.hasExactTag(rawTags, prefix, tagToAdd)) return;

			const tags: unknown[] =
				rawTags == null ? [] : Array.isArray(rawTags) ? rawTags : [rawTags];

			// 1. Remove existing retention tags
			const nextTags = tags.filter((t) => !this.matchesPrefix(t, prefix));

			// 2. Add new tag
			nextTags.push(tagToAdd);

			frontmatter['tags'] = nextTags;
		});
	}

	async clearGraphTags(file: TFile) {
		const prefix = this.normalizedPrefix();
		if (!prefix) return;

		await this.app.fileManager.processFrontMatter(file, (frontmatter) => {
			const rawTags = frontmatter['tags'];
			if (rawTags == null) return;

			const tags: unknown[] = Array.isArray(rawTags) ? rawTags : [rawTags];

			// Remove all retention tags
			const newTags = tags.filter((t) => !this.matchesPrefix(t, prefix));

			// Only update if changes were made
			if (newTags.length !== tags.length) {
				frontmatter['tags'] = newTags;
			}
		});
	}

	calculateRetentionState(stats: ConceptStats): 'high' | 'med' | 'low' {
		// Logic:
		// High: Healthy (>90% Retention / <10% Problematic)
		// Med: Warning (80-90% Retention / 10-20% Problematic)
		// Low: Critical (<80% Retention / >20% Problematic)

		// We use 'score' from StatsService which is (problematicCards / totalCards)
		// So score 0.1 means 10% problematic.

		const problematicRatio = stats.score;

		if (problematicRatio > 0.2) return 'low';
		if (problematicRatio > 0.1) return 'med';
		return 'high';
	}

	async clearAllTags() {
		const prefix = this.normalizedPrefix();
		if (!prefix) {
			new Notice('Graph tag prefix is empty.');
			return;
		}

		const files = this.app.vault.getMarkdownFiles().filter((file) => {
			const cache = this.app.metadataCache.getFileCache(file);
			if (!cache) return true;
			const rawTags = cache.frontmatter?.tags;
			if (rawTags == null) return false;
			const list = Array.isArray(rawTags) ? rawTags : [rawTags];
			return list.some((t) => this.matchesPrefix(t, prefix));
		});
		let count = 0;
		new Notice(`Clearing graph tags from ${files.length} files...`);

		for (const file of files) {
			await this.clearGraphTags(file);
			count++;
		}

		new Notice(`Cleared tags from ${count} files.`);
	}
}
