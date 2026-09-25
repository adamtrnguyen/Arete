import {
	App,
	Modal,
	TFile,
	setIcon,
	MarkdownRenderer,
	Notice,
	SearchComponent,
	Component,
} from 'obsidian';
import { CardRenderer } from '@/presentation/renderers/CardRenderer';

interface SelectionItem {
	type: 'file' | 'card';
	label: string;
	description?: string;
	value: any; // TFile or Card Object
	id: string; // Unique ID for selection tracking
}

export class DependencySelectionModal extends Modal {
	private mode: 'files' | 'cards' = 'files';
	private items: SelectionItem[] = [];
	private filteredItems: SelectionItem[] = [];
	private selectedIndex = 0;

	private resolve: (result: string | null) => void;
	private currentFile: TFile | null = null; // Context for 'cards' mode

	// UI Elements
	private searchContainer: HTMLElement;
	private resultsContainer: HTMLElement;
	private previewContainer: HTMLElement;
	private searchInput: SearchComponent;

	private _component = new Component();

	constructor(app: App, onSubmit: (result: string | null) => void) {
		super(app);
		this.resolve = onSubmit;
	}

	onOpen() {
		const { contentEl } = this;
		this.modalEl.addClass('arete-dependency-modal'); // Apply to parent to control size
		contentEl.addClass('arete-dependency-modal-content'); // Apply to content for layout

		// Main Layout Grid
		const layout = contentEl.createDiv({ cls: 'arete-dep-modal-layout' });

		// Sidebar (Search + List)
		const sidebar = layout.createDiv({ cls: 'arete-dep-modal-sidebar' });
		this.searchContainer = sidebar.createDiv({ cls: 'arete-dep-modal-search' });
		this.resultsContainer = sidebar.createDiv({ cls: 'arete-dep-modal-results' });

		// Preview Pane
		this.previewContainer = layout.createDiv({ cls: 'arete-dep-modal-preview' });
		this.previewContainer.createDiv({
			cls: 'arete-preview-placeholder',
			text: 'Select an item to preview',
		});

		// Initialize Search
		this.searchInput = new SearchComponent(this.searchContainer);
		this.searchInput.setPlaceholder('Search dependencies...');
		this.searchInput.onChange((query) => this.handleSearch(query));
		// Global key listener for nav
		this.scope.register([], 'ArrowDown', (e) => {
			e.preventDefault();
			this.moveSelection(1);
		});
		this.scope.register([], 'ArrowUp', (e) => {
			e.preventDefault();
			this.moveSelection(-1);
		});
		this.scope.register([], 'Enter', (e) => {
			e.preventDefault();
			this.selectCurrent();
		});
		this.scope.register(['Mod'], 'Enter', (e) => {
			// Drill down shortcut? or just normal enter logic
			e.preventDefault();
			this.selectCurrent(true); // Force drill down if applicable
		});

		// Initial Load
		this.loadFiles();
	}

	onClose() {
		const { contentEl } = this;
		contentEl.empty();
		this._component.unload(); // Unload the component
	}

	private loadFiles() {
		this.mode = 'files';
		this.currentFile = null;
		this.searchInput.setPlaceholder(
			'Search files... (Enter to select, Ctrl+Enter to browse cards)',
		);

		const files = this.app.vault.getMarkdownFiles();
		this.items = files.map((f) => ({
			type: 'file',
			label: f.basename,
			description: f.path,
			value: f, // TFile
			id: f.path,
		}));

		this.filterItems('');
	}

	private loadCards(file: TFile) {
		this.mode = 'cards';
		this.currentFile = file;
		this.searchInput.setValue(''); // Reset search for card context
		this.searchInput.setPlaceholder(`Searching cards in ${file.basename}...`);

		const cache = this.app.metadataCache.getFileCache(file);
		const cards = cache?.frontmatter?.cards || [];

		if (!Array.isArray(cards) || cards.length === 0) {
			new Notice(`No cards found in ${file.basename}`);
			this.loadFiles(); // Revert
			return;
		}

		this.items = cards
			.map((c: any) => {
				if (!c.id) return null;
				// Clean up Front text for label (remove markdown for readability)
				const rawFront = c.Front || c.Text || 'Untitled Card';
				// Simple strip of images/links for list view
				const label = rawFront.replace(/!\[\[.*?\]\]/g, '[Image]').substring(0, 60);

				return {
					type: 'card',
					label: label,
					description: c.id,
					value: c, // Card Object
					id: c.id,
				} as SelectionItem;
			})
			.filter((i): i is SelectionItem => i !== null);

		// Back button
		this.items.unshift({
			type: 'file', // abusing type slightly for navigation
			label: '.. (Back to Files)',
			description: 'Go up one level',
			value: null,
			id: '__back__',
		});

		this.filterItems('');
	}

	private handleSearch(query: string) {
		this.filterItems(query);
	}

	private filterItems(query: string) {
		const q = query.toLowerCase();
		if (!q) {
			this.filteredItems = [...this.items];
		} else {
			this.filteredItems = this.items.filter(
				(i) =>
					i.label.toLowerCase().includes(q) ||
					(i.description && i.description.toLowerCase().includes(q)),
			);
		}
		// Optimization: Limit to top 20 as requested (and prevent rendering lag)
		this.filteredItems = this.filteredItems.slice(0, 20);

		this.selectedIndex = 0;
		this.renderResults();
		this.renderPreview();
	}

	private renderResults() {
		this.resultsContainer.empty();

		this.filteredItems.forEach((item, idx) => {
			const el = this.resultsContainer.createDiv({
				cls: 'arete-result-item' + (idx === this.selectedIndex ? ' is-selected' : ''),
			});

			const icon = el.createSpan({ cls: 'arete-result-icon' });
			setIcon(icon, item.type === 'card' ? 'id-card' : 'file-text');
			if (item.id === '__back__') setIcon(icon, 'corner-left-up');

			const content = el.createDiv({ cls: 'arete-result-content' });
			content.createDiv({ cls: 'arete-result-label', text: item.label });
			if (item.description) {
				content.createDiv({ cls: 'arete-result-desc', text: item.description });
			}

			// Mouse interactions
			el.addEventListener('mouseenter', () => {
				this.selectedIndex = idx;
				this.updateSelectionVisuals();
				this.renderPreview();
			});
			el.addEventListener('click', () => {
				this.selectCurrent();
			});
		});

		this.ensureScroll();
	}

	private updateSelectionVisuals() {
		const resultEls = this.resultsContainer.children;
		for (let i = 0; i < resultEls.length; i++) {
			if (i === this.selectedIndex) resultEls[i].addClass('is-selected');
			else resultEls[i].removeClass('is-selected');
		}
	}

	private moveSelection(delta: number) {
		const len = this.filteredItems.length;
		if (len === 0) return;

		this.selectedIndex = (this.selectedIndex + delta + len) % len;
		this.updateSelectionVisuals();
		this.ensureScroll();
		this.renderPreview();
	}

	private ensureScroll() {
		const selectedEl = this.resultsContainer.children[this.selectedIndex] as HTMLElement;
		if (selectedEl) {
			selectedEl.scrollIntoView({ block: 'nearest' });
		}
	}

	private async renderPreview() {
		this.previewContainer.empty();
		const item = this.filteredItems[this.selectedIndex];

		if (!item) {
			this.previewContainer.createDiv({
				cls: 'arete-preview-placeholder',
				text: 'No selection',
			});
			return;
		}

		if (item.id === '__back__') {
			this.previewContainer.createDiv({
				cls: 'arete-preview-placeholder',
				text: 'Go back to file list',
			});
			return;
		}

		// Loading indicator
		const contentEl = this.previewContainer.createDiv({ cls: 'arete-preview-content' });

		if (item.type === 'file') {
			const file = item.value as TFile;
			const content = await this.app.vault.read(file);
			// Render first 2000 chars to avoid lags on huge files
			await MarkdownRenderer.render(
				this.app,
				content.substring(0, 3000),
				contentEl,
				file.path,
				this._component,
			);
		} else if (item.type === 'card') {
			const card = item.value; // Card Object
			const cardContainer = contentEl.createDiv({ cls: 'arete-card-preview' });

			await CardRenderer.render(
				this.app,
				cardContainer,
				card,
				this.currentFile?.path || '',
				this._component,
			);
		}
	}

	private selectCurrent(forceDrillDown = false) {
		const item = this.filteredItems[this.selectedIndex];
		if (!item) return;

		if (item.id === '__back__') {
			this.loadFiles();
			return;
		}

		if (this.mode === 'files') {
			const file = item.value as TFile;

			// Logic: If modifier key or specific action, drill down?
			// For now: Always allow drill down? Or make it a choice?
			// "Yazi" style usually enters directories on Enter.
			// Let's check: if user wants to link the FILE, they can... how?
			// Maybe: Enter -> Select File. Ctrl+Enter -> Drill Down to cards?
			// Or simplified: Just Enter to Drill Down. User must have "Select this File" option?

			// User request: "preview card window if we want to find a specific card"
			// But dependencies CAN be files OR cards.

			if (forceDrillDown) {
				this.loadCards(file);
			} else {
				// Default action for file: Should it be select or drill?
				// Context of dependency: "requires: [arete_ID]" or "requires: [File]"
				// Given we want to find cards, Enter -> Drill Down seems safer default for "Yazi like browsing"
				// But we need a way to select the file itself if that's the intent.
				// Let's try: Enter = Drill Down. Shift+Enter = Select File.
				this.loadCards(file);
			}
		} else {
			// Card mode
			if (item.type === 'card') {
				this.resolve(item.id);
				this.close();
			}
		}
	}
}
