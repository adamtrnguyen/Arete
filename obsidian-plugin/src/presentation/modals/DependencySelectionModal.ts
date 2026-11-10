import {
	App,
	Modal,
	TFile,
	setIcon,
	MarkdownRenderer,
	Notice,
	SearchComponent,
	Component,
	Platform,
} from 'obsidian';
import { CardRenderer } from '@/presentation/renderers/CardRenderer';

interface SelectionItem {
	type: 'file' | 'card';
	label: string;
	description?: string;
	value: any; // TFile or Card Object
	id: string; // Unique ID for selection tracking
	// label and description lowercased once, for the per-keystroke filter
	labelLower: string;
	descLower: string;
}

/** Results shown at once; the filter stops at this many matches. */
const MAX_RESULTS = 20;
/** Quiet period before the preview of the selected item is rendered. */
const PREVIEW_DELAY_MS = 100;

function selectionItem(fields: Omit<SelectionItem, 'labelLower' | 'descLower'>): SelectionItem {
	return {
		...fields,
		labelLower: fields.label.toLowerCase(),
		descLower: (fields.description ?? '').toLowerCase(),
	};
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
	private previewTimer: ReturnType<typeof setTimeout> | null = null;
	// Incremented per preview; a render that finds it changed has been superseded.
	private previewRevision = 0;

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
		if (this.previewTimer) clearTimeout(this.previewTimer);
		this.previewRevision++;
		this._component.unload(); // Unload the component
	}

	private loadFiles() {
		this.mode = 'files';
		this.currentFile = null;
		// `Mod` in the scope below is Cmd on macOS and Ctrl elsewhere; name it the same way.
		const modifier = Platform.isMacOS ? 'Cmd' : 'Ctrl';
		this.searchInput.setPlaceholder(
			`Search files... (Enter to select, ${modifier}+Enter to browse cards)`,
		);

		const files = this.app.vault.getMarkdownFiles();
		this.items = files.map((f) =>
			selectionItem({
				type: 'file',
				label: f.basename,
				description: f.path,
				value: f, // TFile
				id: f.path,
			}),
		);

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

				return selectionItem({
					type: 'card',
					label: label,
					description: c.id,
					value: c, // Card Object
					id: c.id,
				});
			})
			.filter((i): i is SelectionItem => i !== null);

		// Back button
		this.items.unshift(
			selectionItem({
				type: 'file', // abusing type slightly for navigation
				label: '.. (back to files)',
				description: 'Go up one level',
				value: null,
				id: '__back__',
			}),
		);

		this.filterItems('');
	}

	private handleSearch(query: string) {
		this.filterItems(query);
	}

	private filterItems(query: string) {
		const q = query.toLowerCase();
		// The first MAX_RESULTS matches in item order (prevents rendering lag).
		const matches: SelectionItem[] = [];
		for (const i of this.items) {
			if (matches.length >= MAX_RESULTS) break;
			if (!q || i.labelLower.includes(q) || i.descLower.includes(q)) matches.push(i);
		}
		this.filteredItems = matches;

		this.selectedIndex = 0;
		this.renderResults();
		this.schedulePreview();
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
				this.schedulePreview();
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
		this.schedulePreview();
	}

	private ensureScroll() {
		const selectedEl = this.resultsContainer.children[this.selectedIndex] as HTMLElement;
		if (selectedEl) {
			selectedEl.scrollIntoView({ block: 'nearest' });
		}
	}

	/** Preview the selected item once selection has been still for PREVIEW_DELAY_MS. */
	private schedulePreview() {
		if (this.previewTimer) clearTimeout(this.previewTimer);
		this.previewTimer = setTimeout(() => {
			this.previewTimer = null;
			void this.renderPreview();
		}, PREVIEW_DELAY_MS);
	}

	private async renderPreview() {
		const revision = ++this.previewRevision;
		const item = this.filteredItems[this.selectedIndex];
		const current = () => revision === this.previewRevision;

		// Read before clearing, so a superseded preview never touches the pane.
		let fileContent = '';
		if (item?.type === 'file' && item.id !== '__back__') {
			fileContent = await this.app.vault.cachedRead(item.value as TFile);
			if (!current()) return;
		}

		this.previewContainer.empty();

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
			// Render first 2000 chars to avoid lags on huge files
			await MarkdownRenderer.render(
				this.app,
				fileContent.substring(0, 3000),
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

			// A file always drills down into its cards, with or without forceDrillDown.
			if (forceDrillDown) {
				this.loadCards(file);
			} else {
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
