import { App, Component, MarkdownRenderer } from 'obsidian';

export class CardRenderer {
	static async render(
		app: App,
		container: HTMLElement,
		card: any,
		sourcePath: string,
		component: Component,
	) {
		container.empty();

		console.log('[Arete] CardRenderer rendering card:', card);

		// Logic adapted from CardYamlEditorView to ensure consistency
		// We iterate over all keys to support dynamic fields (Front/Back/Text/Extra/etc)

		for (const [key, value] of Object.entries(card)) {
			// Exclude structural/system fields from rendering
			if (['id', 'ID', 'model', 'Model', 'anki', 'deps'].includes(key)) {
				continue;
			}

			// Value handling:
			// CardYamlEditorView checked `typeof value === 'string'`.
			// We'll be slightly more robust but prioritize string rendering.
			if (value === null || value === undefined) continue;
			if (typeof value === 'object') continue; // Skip arrays/objects like deps/anki if not caught above

			console.log('[Arete] Rendering field:', key, value);
			const section = container.createDiv({ cls: 'arete-preview-section' });
			section.createDiv({ cls: 'arete-preview-label', text: key });
			const content = section.createDiv({ cls: 'arete-preview-content' });

			await MarkdownRenderer.render(
				app,
				String(value), // Ensure string
				content,
				sourcePath,
				component,
			);
		}

		if (container.children.length === 0) {
			console.warn('[Arete] CardRenderer: No fields rendered for card:', card);
			const errorDiv = container.createDiv({ cls: 'arete-preview-placeholder' });
			errorDiv.setText(`No visible fields found. Keys: ${Object.keys(card).join(', ')}`);
		}
	}
}
