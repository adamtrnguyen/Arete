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

	async updateGraphTags(file: TFile, stats: ConceptStats) {
		if (!this.settings.graph_coloring_enabled) return;

		const state = this.calculateRetentionState(stats);
		const tagToAdd = `${this.settings.graph_tag_prefix}/${state}`;
		const prefix = this.settings.graph_tag_prefix;

		await this.app.fileManager.processFrontMatter(file, (frontmatter) => {
			let tags = frontmatter['tags'] || [];
			if (!Array.isArray(tags)) {
				// Handle single string tag
				tags = [tags];
			}

			// 1. Remove existing retention tags
			tags = tags.filter((t: string) => !t.startsWith(prefix));

			// 2. Add new tag
			tags.push(tagToAdd);

			frontmatter['tags'] = tags;
		});
	}

	async clearGraphTags(file: TFile) {
		const prefix = this.settings.graph_tag_prefix;

		await this.app.fileManager.processFrontMatter(file, (frontmatter) => {
			let tags = frontmatter['tags'];
			if (!tags) return;

			if (!Array.isArray(tags)) {
				tags = [tags];
			}

			// Remove all retention tags
			const newTags = tags.filter((t: string) => !t.startsWith(prefix));

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

		// However, we also want to consider FSRS difficulty if available
		// But 'score' is already heavily weighted by "Is Problematic".
		// Let's stick to the 'score' metric for simplicity as it encapsulates lapses, ease, and difficulty.

		const problematicRatio = stats.score;

		if (problematicRatio > 0.2) return 'low';
		if (problematicRatio > 0.1) return 'med';
		return 'high';
	}

	async clearAllTags() {
		const files = this.app.vault.getMarkdownFiles();
		let count = 0;
		new Notice(`Arete: Clearing graph tags from ${files.length} files...`);

		for (const file of files) {
			await this.clearGraphTags(file);
			count++;
		}

		new Notice(`Arete: Cleared tags from ${count} files.`);
	}
}
