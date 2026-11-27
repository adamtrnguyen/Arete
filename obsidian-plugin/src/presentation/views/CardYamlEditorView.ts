import {
	Component,
	ExtraButtonComponent,
	ItemView,
	WorkspaceLeaf,
	MarkdownView,
	Menu,
	setIcon,
	setTooltip,
	Notice,
	TFile,
} from 'obsidian';
import { EditorView, lineNumbers, keymap } from '@codemirror/view';
import { EditorState, Annotation } from '@codemirror/state';
import { defaultKeymap, history, historyKeymap } from '@codemirror/commands';
import { yaml } from '@codemirror/lang-yaml';
import { AnkiBrowser } from '@/domain/ports';
import { AretePluginSettings } from '@/domain/settings';
import { StatsService } from '@application/services/StatsService';
import { TemplateRenderer } from '@application/services/TemplateRenderer';
import { DependencyField } from '@/presentation/components/DependencyField';
import { CardRenderer } from '@/presentation/renderers/CardRenderer';
import { renderCardPreviewFrame } from '@/presentation/renderers/CardPreviewFrame';
import { cardRangesField } from '@/presentation/extensions/CardGutterExtension';
import {
	CardData,
	CardEditorViewMode,
	cloneValue,
	extractCardFields,
	parseCardYaml,
	serializeCardYaml,
} from '@/presentation/views/yamlEditor/cardEditorHelpers';
import { renderCardToolbar } from '@/presentation/views/yamlEditor/CardToolbar';
import { renderCardFieldEditor } from '@/presentation/views/yamlEditor/CardFieldEditor';

/** What this view needs from the plugin (main.ts satisfies it; no import of main). */
export interface YamlEditorHost {
	settings: AretePluginSettings;
	statsService: StatsService;
	templateRenderer: TemplateRenderer;
	areteClient: AnkiBrowser;
	activateLocalGraphView(cardId?: string): Promise<void>;
	highlightCardLines(cardIndex: number): void;
}

export const YAML_EDITOR_VIEW_TYPE = 'arete-yaml-editor';

/** Quiet period after an edit before the card is written back to the note. */
const SYNC_DEBOUNCE_MS = 300;

interface PendingSync {
	file: TFile;
	cardIndex: number;
	cardId?: string;
	mode: CardEditorViewMode;
	sourceText?: string;
	cardSnapshot?: CardData;
}

// Annotation to prevent infinite sync loops
const syncAnnotation = Annotation.define<boolean>();

export class CardYamlEditorView extends ItemView {
	plugin: YamlEditorHost;
	private editorView: EditorView | null = null;
	private indexContainer: HTMLElement | null = null;
	private parseErrorEl: HTMLElement | null = null;
	private editorContainer: HTMLElement | null = null;
	private fieldEditorContainer: HTMLElement | null = null;
	private previewContainer: HTMLElement | null = null;
	private toolbarContainer: HTMLElement | null = null;

	private currentCardIndex = 0;
	private cards: CardData[] = [];
	private loadedSourceCards: unknown[] | null = null;

	private currentFilePath: string | null = null;
	private inFlightWrites = 0;
	private ownWrite: { path: string; mtime: number } | null = null;
	private hasParseError = false;
	private viewMode: CardEditorViewMode = CardEditorViewMode.Fields;
	private previewSide: 'Front' | 'Back' = 'Front';
	private fileModel = 'Basic';
	private previewRevision = 0;
	private previewComponent: Component | null = null;
	private syncDebounceTimer: number | null = null;
	private pendingSync: PendingSync | null = null;

	constructor(leaf: WorkspaceLeaf, plugin: YamlEditorHost) {
		super(leaf);
		this.plugin = plugin;
	}

	getViewType() {
		return YAML_EDITOR_VIEW_TYPE;
	}

	getDisplayText() {
		return 'Card editor';
	}

	getIcon() {
		return 'file-code';
	}

	async onOpen() {
		const container = this.contentEl;
		container.empty();
		container.addClass('arete-yaml-editor-container');

		this.indexContainer = container.createDiv({ cls: 'arete-yaml-index' });

		const rightPanel = container.createDiv({ cls: 'arete-yaml-editor-panel' });
		this.toolbarContainer = rightPanel.createDiv({ cls: 'arete-editor-toolbar' });

		this.parseErrorEl = rightPanel.createDiv({ cls: 'arete-yaml-parse-error' });
		this.parseErrorEl.hide();

		this.editorContainer = rightPanel.createDiv({ cls: 'arete-yaml-editor-wrapper' });

		this.fieldEditorContainer = rightPanel.createDiv({ cls: 'arete-field-editor' });
		this.fieldEditorContainer.hide();

		this.previewContainer = rightPanel.createDiv({ cls: 'arete-preview-container' });
		this.previewContainer.hide();

		await this.loadCards();
		this.renderIndex();
		this.renderToolbar();
		this.createEditor();
		this.setViewMode(this.viewMode);

		this.registerEvent(
			this.app.workspace.on('active-leaf-change', () => {
				this.handleActiveFileChange();
			}),
		);
		this.registerEvent(this.app.metadataCache.on('changed', (file) => this.onFileParsed(file)));

		this.indexContainer?.addEventListener('keydown', (e) => this.handleKeyNavigation(e));
	}

	async onClose() {
		await this.flushPendingSync();
		this.clearPreview();
		if (this.editorView) {
			this.editorView.destroy();
			this.editorView = null;
		}
	}

	private setParseError(message: string | null) {
		this.hasParseError = Boolean(message);
		if (!this.parseErrorEl) return;
		if (message) {
			this.parseErrorEl.setText(message);
			this.parseErrorEl.show();
		} else {
			this.parseErrorEl.setText('');
			this.parseErrorEl.hide();
		}
	}

	private async loadCards() {
		this.setParseError(null);
		const activeFile = this.app.workspace.getActiveFile();
		if (!activeFile) {
			this.cancelPendingSync();
			this.cards = [];
			this.loadedSourceCards = null;
			this.fileModel = 'Basic';
			this.currentFilePath = null;
			this.ownWrite = null;
			return;
		}

		if (this.currentFilePath !== activeFile.path) {
			this.currentCardIndex = 0;
			this.currentFilePath = activeFile.path;
			this.ownWrite = null;
		}
		if (this.pendingSync && this.pendingSync.file.path !== activeFile.path) {
			this.cancelPendingSync();
		}

		const cache = this.app.metadataCache.getFileCache(activeFile);
		this.fileModel = cache?.frontmatter?.model || 'Basic';
		if (cache?.frontmatter?.cards && Array.isArray(cache.frontmatter.cards)) {
			this.loadedSourceCards = cache.frontmatter.cards;
			this.cards = cache.frontmatter.cards.map((c: CardData) => cloneValue(c));
		} else {
			this.loadedSourceCards = null;
			this.cards = [];
		}

		if (this.currentCardIndex >= this.cards.length) {
			this.currentCardIndex = Math.max(0, this.cards.length - 1);
		}
	}

	private handleActiveFileChange() {
		// Same note and the same parsed cards (focus moved, e.g. into this view): keep the
		// rendered fields, and the focus in them. A re-parse replaces the cards array.
		const activeFile = this.app.workspace.getActiveFile();
		if (activeFile && activeFile.path === this.currentFilePath) {
			const cache = this.app.metadataCache.getFileCache(activeFile);
			const cards = cache?.frontmatter?.cards;
			if (cards !== undefined && cards === this.loadedSourceCards) return;
		}
		const reload = () => {
			void this.loadCards().then(() => {
				this.renderIndex();
				this.refreshActiveView();
			});
		};
		if (this.pendingSync || this.syncDebounceTimer) {
			void this.flushPendingSync().then(reload);
		} else {
			reload();
		}
	}

	private refreshActiveView() {
		this.renderToolbar();
		if (this.viewMode === CardEditorViewMode.Source) {
			this.updateEditorContent();
		} else if (this.viewMode === CardEditorViewMode.Fields) {
			this.renderFieldEditor();
		} else if (this.viewMode === CardEditorViewMode.Preview) {
			void this.renderPreview();
		}
	}

	private renderIndex() {
		if (!this.indexContainer) return;
		this.indexContainer.empty();

		const header = this.indexContainer.createDiv({ cls: 'arete-yaml-index-header' });
		header.createSpan({ text: `${this.cards.length}`, cls: 'arete-yaml-index-count' });

		const listContainer = this.indexContainer.createDiv({ cls: 'arete-yaml-index-list' });
		listContainer.setAttribute('tabindex', '0');

		this.cards.forEach((card, index) => {
			const item = listContainer.createDiv({
				cls: 'arete-yaml-index-item',
				attr: {
					'data-index': String(index),
					draggable: 'true',
				},
			});

			const hasWarning = this.getCardWarning(index);
			if (hasWarning) {
				item.addClass('has-warning');
				const warningIcon = item.createSpan({ cls: 'arete-yaml-index-warning' });
				setIcon(warningIcon, 'alert-triangle');
			}

			item.createSpan({ text: `${index + 1}`, cls: 'arete-yaml-index-number' });

			if (index === this.currentCardIndex) {
				item.addClass('is-active');
			}

			const rawFront = card?.['Front'] ?? card?.['Text'] ?? '';
			const frontText = typeof rawFront === 'string' ? rawFront : String(rawFront);
			if (frontText) {
				setTooltip(item, frontText.substring(0, 50) + (frontText.length > 50 ? '...' : ''));
			}

			item.addEventListener('click', () => {
				this.selectCard(index, true);
			});

			item.addEventListener('contextmenu', (e) => this.showCardContextMenu(e, index));
			item.addEventListener('dragstart', (e) => this.handleDragStart(e, index));
			item.addEventListener('dragover', (e) => this.handleDragOver(e));
			item.addEventListener('drop', (e) => {
				void this.handleDrop(e, index);
			});
			item.addEventListener('dragend', () => this.handleDragEnd());
		});

		new ExtraButtonComponent(this.indexContainer)
			.setIcon('plus')
			.setTooltip('Add card')
			.onClick(() => this.addCard())
			.extraSettingsEl.addClass('arete-yaml-index-add');
	}

	private handleKeyNavigation(e: KeyboardEvent) {
		if (e.key === 'ArrowDown') {
			e.preventDefault();
			this.selectCard(Math.min(this.cards.length - 1, this.currentCardIndex + 1), true);
		} else if (e.key === 'ArrowUp') {
			e.preventDefault();
			this.selectCard(Math.max(0, this.currentCardIndex - 1), true);
		}
	}

	private handleDragStart(e: DragEvent, index: number) {
		if (e.dataTransfer) {
			e.dataTransfer.setData('text/plain', String(index));
			e.dataTransfer.effectAllowed = 'move';
		}
		const el = e.target as HTMLElement;
		el.addClass('is-dragging');
	}

	private handleDragOver(e: DragEvent) {
		e.preventDefault();
		if (e.dataTransfer) {
			e.dataTransfer.dropEffect = 'move';
		}
		const el = (e.target as HTMLElement).closest('.arete-yaml-index-item');
		if (el) el.addClass('is-drag-over');
	}

	private handleDragEnd() {
		const items = this.indexContainer?.querySelectorAll('.arete-yaml-index-item');
		items?.forEach((item) => {
			item.removeClass('is-dragging');
			item.removeClass('is-drag-over');
		});
	}

	private async handleDrop(e: DragEvent, toIndex: number) {
		e.preventDefault();
		const fromIndex = Number(e.dataTransfer?.getData('text/plain'));
		if (isNaN(fromIndex) || fromIndex === toIndex) return;

		await this.reorderCards(fromIndex, toIndex);
	}

	private showCardContextMenu(e: MouseEvent, index: number) {
		const menu = new Menu();
		menu.addItem((item) => {
			item.setTitle('Delete card')
				.setIcon('trash')
				.setWarning(true)
				.onClick(() => this.deleteCard(index));
		});
		menu.showAtMouseEvent(e);
	}

	renderToolbar() {
		if (!this.toolbarContainer) return;
		renderCardToolbar(this.toolbarContainer, {
			app: this.app,
			activeFile: this.app.workspace.getActiveFile(),
			card: this.cards[this.currentCardIndex],
			viewMode: this.viewMode,
			settings: this.plugin.settings,
			statsService: this.plugin.statsService,
			onScrollToCard: () => void this.scrollToCard(this.currentCardIndex),
			onOpenInAnki: () => void this.openInAnki(this.currentCardIndex),
			onOpenLocalGraph: () => {
				const current = this.cards[this.currentCardIndex];
				if (current && current.id) {
					void this.plugin.activateLocalGraphView(current.id);
				} else {
					void this.plugin.activateLocalGraphView();
				}
			},
			onSelectMode: (mode) => this.setViewMode(mode),
		});
	}

	private setViewMode(mode: CardEditorViewMode) {
		if (this.hasInvalidSourceYaml()) return;
		void this.flushPendingSync();
		this.setParseError(null);
		if (mode !== CardEditorViewMode.Preview) this.clearPreview();
		this.viewMode = mode;
		this.renderToolbar();

		this.editorContainer?.hide();
		this.fieldEditorContainer?.hide();
		this.previewContainer?.hide();

		if (mode === CardEditorViewMode.Source) {
			this.editorContainer?.show();
			this.updateEditorContent();
		} else if (mode === CardEditorViewMode.Fields) {
			this.fieldEditorContainer?.show();
			this.renderFieldEditor();
		} else if (mode === CardEditorViewMode.Preview) {
			this.previewContainer?.show();
			void this.renderPreview();
		}
	}

	private renderFieldEditor() {
		if (!this.fieldEditorContainer) return;
		const card = this.cards[this.currentCardIndex];
		renderCardFieldEditor(this.fieldEditorContainer, {
			card,
			modelName: card && typeof card === 'object' ? this.getCardModel(card) : this.fileModel,
			onFieldChanged: () => this.debouncedSyncToMain(),
			renderDependencySection: (label, initialValues, onChange) =>
				this.renderDependencySection(label, initialValues, onChange),
		});
	}

	private renderDependencySection(
		label: string,
		initialValues: string[],
		onChange: (newValues: string[]) => void,
	) {
		if (!this.fieldEditorContainer) return;

		const group = this.fieldEditorContainer.createDiv({ cls: 'arete-field-group' });
		group.createEl('label', { cls: 'arete-field-label', text: label });

		const container = group.createDiv({ cls: 'arete-field-dep-container' });
		new DependencyField(container, this.app, initialValues, onChange);
	}

	private getCardModel(card: CardData): string {
		return card.model || this.fileModel;
	}

	private clearPreview() {
		this.previewRevision++;
		if (this.previewComponent) this.removeChild(this.previewComponent);
		this.previewComponent = null;
		this.previewContainer?.empty();
	}

	private async renderPreview() {
		if (!this.previewContainer) return;
		this.clearPreview();
		const revision = this.previewRevision;
		const card = this.cards[this.currentCardIndex];
		if (!card) return;

		const modelName = this.getCardModel(card);
		const sourcePath = this.currentFilePath || '';
		const component = this.addChild(new Component());
		this.previewComponent = component;
		let rendered: { html: string; css: string } | null = null;
		let failure = '';
		try {
			rendered = await this.plugin.templateRenderer.render(
				modelName,
				this.previewSide,
				extractCardFields(card),
				{
					sourcePath,
					component,
					doc: this.previewContainer.ownerDocument ?? undefined,
				},
			);
		} catch (error) {
			console.error('[Arete] Preview failed', error);
			failure =
				error instanceof Error ? error.message.split('\n')[0].slice(0, 300) : String(error);
		}
		// A slower model fetch must not overwrite a newer card/side selection.
		if (revision !== this.previewRevision) {
			component.unload();
			return;
		}
		if (!rendered) {
			this.previewContainer.createDiv({
				cls: 'arete-preview-message',
				text: failure
					? `Could not load card preview: ${failure}`
					: 'Template preview unavailable; showing fields.',
			});
			const fallback = this.previewContainer.createDiv();
			await CardRenderer.render(this.app, fallback, card, sourcePath, component);
			if (revision !== this.previewRevision) component.unload();
			return;
		}

		this.renderPreviewToggle(modelName);
		renderCardPreviewFrame(this.app, this.previewContainer, rendered, sourcePath, component);
	}

	private renderPreviewToggle(modelName: string) {
		if (!this.previewContainer) return;

		const bar = this.previewContainer.createDiv({ cls: 'arete-preview-toggle' });
		bar.createSpan({ cls: 'arete-preview-model', text: modelName });

		const group = bar.createDiv({ cls: 'arete-preview-side-group' });
		(['Front', 'Back'] as const).forEach((side) => {
			const btn = group.createDiv({
				cls: 'arete-preview-side-btn' + (this.previewSide === side ? ' is-active' : ''),
				text: side,
			});
			btn.addEventListener('click', () => {
				if (this.previewSide === side) return;
				this.previewSide = side;
				void this.renderPreview();
			});
		});
	}

	private createEditor() {
		if (!this.editorContainer) return;
		this.editorContainer.empty();

		const content = this.extractCardYaml(this.currentCardIndex);

		const startState = EditorState.create({
			doc: content,
			extensions: [
				yaml(),
				lineNumbers(),
				history(),
				keymap.of([...defaultKeymap, ...historyKeymap]),
				EditorView.lineWrapping,
				EditorView.updateListener.of((update) => {
					if (update.docChanged) {
						const isSync = update.transactions.some((tr) =>
							tr.annotation(syncAnnotation),
						);
						if (!isSync) {
							this.debouncedSyncToMain();
						}
					}
				}),
				EditorView.theme({
					'&': { height: '100%' },
					'.cm-scroller': { overflow: 'auto' },
				}),
			],
		});

		this.editorView = new EditorView({
			state: startState,
			parent: this.editorContainer,
		});
	}

	private updateEditorContent() {
		if (!this.editorView) return;
		const content = this.extractCardYaml(this.currentCardIndex);
		this.editorView.dispatch({
			changes: { from: 0, to: this.editorView.state.doc.length, insert: content },
			annotations: syncAnnotation.of(true),
		});
	}

	private extractCardYaml(index: number): string {
		if (index < 0 || index >= this.cards.length) return '# No card selected\n';
		return serializeCardYaml(this.cards[index]);
	}

	private recordOwnWrite(file: TFile) {
		this.ownWrite =
			file.stat?.mtime !== undefined ? { path: file.path, mtime: file.stat.mtime } : null;
	}

	private cancelPendingSync() {
		if (this.syncDebounceTimer) {
			window.clearTimeout(this.syncDebounceTimer);
			this.syncDebounceTimer = null;
		}
		this.pendingSync = null;
	}

	/**
	 * Synchronous guard for mode/card switches: if the Source editor holds an invalid
	 * draft (either debounced or already flagged), show the inline error and block leaving.
	 */
	private hasInvalidSourceYaml(): boolean {
		if (this.viewMode !== CardEditorViewMode.Source || !this.editorView) return false;
		const hasCurrentPendingSource =
			this.pendingSync?.mode === CardEditorViewMode.Source &&
			this.pendingSync.file.path === this.currentFilePath;
		if (!this.hasParseError && !hasCurrentPendingSource) {
			return false;
		}
		const sourceText = hasCurrentPendingSource
			? (this.pendingSync?.sourceText ?? '')
			: this.editorView.state.doc.toString();
		if (parseCardYaml(sourceText) === null) {
			this.setParseError('Invalid YAML: fix syntax errors to save changes.');
			return true;
		}
		return false;
	}

	/**
	 * Preserve sync-written `id` and `anki` metadata from live frontmatter so a debounced
	 * card edit never erases IDs that `arete sync` wrote concurrently.
	 */
	private mergeSyncMetadata(existing: CardData, updated: CardData): CardData {
		const merged = cloneValue(updated);
		if (existing && typeof existing === 'object' && !Array.isArray(existing)) {
			if (existing.id !== undefined && merged.id === undefined) {
				merged.id = cloneValue(existing.id);
			}
			if (
				existing.anki &&
				typeof existing.anki === 'object' &&
				!Array.isArray(existing.anki)
			) {
				if (merged.anki === undefined) {
					merged.anki = cloneValue(existing.anki);
				} else if (
					merged.anki &&
					typeof merged.anki === 'object' &&
					!Array.isArray(merged.anki)
				) {
					merged.anki = { ...cloneValue(existing.anki), ...merged.anki };
				}
			}
		}
		return merged;
	}

	private capturePendingSync(): PendingSync | null {
		const activeFile = this.app.workspace.getActiveFile();
		if (!activeFile) return null;
		const cardIndex = this.currentCardIndex;
		const mode = this.viewMode;
		const existingCard = this.cards[cardIndex];
		const cardId =
			existingCard && typeof existingCard === 'object' && typeof existingCard.id === 'string'
				? existingCard.id
				: undefined;
		if (mode === CardEditorViewMode.Source) {
			if (!this.editorView) return null;
			return {
				file: activeFile,
				cardIndex,
				cardId,
				mode,
				sourceText: this.editorView.state.doc.toString(),
			};
		}
		if (!existingCard || typeof existingCard !== 'object') return null;
		return {
			file: activeFile,
			cardIndex,
			cardId,
			mode,
			cardSnapshot: cloneValue(existingCard),
		};
	}

	private debouncedSyncToMain() {
		if (this.syncDebounceTimer) window.clearTimeout(this.syncDebounceTimer);
		this.pendingSync = this.capturePendingSync();
		this.syncDebounceTimer = window.setTimeout(() => {
			this.syncDebounceTimer = null;
			const payload = this.pendingSync;
			this.pendingSync = null;
			void (async () => {
				try {
					await this.syncToMain(payload ?? undefined);
				} catch (e) {
					console.error('[Arete] Failed to save card edit:', e);
					new Notice('Failed to save card changes.');
				}
			})();
		}, SYNC_DEBOUNCE_MS);
	}

	private async flushPendingSync(): Promise<boolean> {
		if (this.syncDebounceTimer) {
			window.clearTimeout(this.syncDebounceTimer);
			this.syncDebounceTimer = null;
		}
		const payload = this.pendingSync;
		this.pendingSync = null;
		if (!payload) {
			return !this.hasInvalidSourceYaml();
		}
		if (payload.mode === CardEditorViewMode.Source) {
			if (parseCardYaml(payload.sourceText ?? '') === null) {
				this.pendingSync = payload;
				this.setParseError('Invalid YAML: fix syntax errors to save changes.');
				return false;
			}
		}
		try {
			return await this.syncToMain(payload);
		} catch (e) {
			console.error('[Arete] Failed to save pending card edit:', e);
			new Notice('Failed to save card changes.');
			return false;
		}
	}

	private async syncToMain(payload?: PendingSync): Promise<boolean> {
		const target = payload ?? this.capturePendingSync();
		if (!target) return true;

		let updatedCard: CardData | null;
		if (target.mode === CardEditorViewMode.Source) {
			updatedCard = parseCardYaml(target.sourceText ?? '');
			if (!updatedCard) {
				this.setParseError('Invalid YAML: fix syntax errors to save changes.');
				return false;
			}
			this.setParseError(null);
			if (
				this.currentFilePath === target.file.path &&
				target.cardIndex >= 0 &&
				target.cardIndex < this.cards.length
			) {
				this.cards[target.cardIndex] = cloneValue(updatedCard);
			}
		} else {
			if (!target.cardSnapshot) return true;
			this.setParseError(null);
			updatedCard = cloneValue(target.cardSnapshot);
		}

		const written: { card: CardData | null; index: number } = { card: null, index: -1 };

		this.inFlightWrites++;
		try {
			await this.app.fileManager.processFrontMatter(target.file, (frontmatter) => {
				if (!Array.isArray(frontmatter.cards)) return;
				let writeIndex = target.cardIndex;
				if (target.cardId && frontmatter.cards[writeIndex]?.id !== target.cardId) {
					const found = frontmatter.cards.findIndex(
						(c: CardData) => c && typeof c === 'object' && c.id === target.cardId,
					);
					if (found === -1) return;
					writeIndex = found;
				}
				const existing = frontmatter.cards[writeIndex];
				if (!existing) return;
				const merged = this.mergeSyncMetadata(existing, updatedCard);
				frontmatter.cards[writeIndex] = merged;
				written.card = cloneValue(merged);
				written.index = writeIndex;
			});
			this.recordOwnWrite(target.file);
		} finally {
			this.inFlightWrites--;
		}

		// Reflect any externally written id/anki back into our local card copy without
		// clobbering newer field keystrokes typed while the write was in flight.
		if (
			written.card &&
			written.index >= 0 &&
			this.currentFilePath === target.file.path &&
			written.index < this.cards.length
		) {
			const local = this.cards[written.index];
			if (local && typeof local === 'object') {
				if (written.card.id !== undefined && local.id === undefined) {
					local.id = cloneValue(written.card.id);
				}
				if (written.card.anki && typeof written.card.anki === 'object') {
					if (local.anki === undefined) {
						local.anki = cloneValue(written.card.anki);
					} else if (
						local.anki &&
						typeof local.anki === 'object' &&
						!Array.isArray(local.anki)
					) {
						local.anki = { ...cloneValue(written.card.anki), ...local.anki };
					}
				}
			}
		}
		return true;
	}

	/**
	 * Reload after the active file is re-parsed. `vault.on('modify')` fires before the
	 * metadata cache updates, so reloading on it would show an external edit one edit late.
	 * Skips only the echo of this view's own write, identified by its path and mtime.
	 */
	onFileParsed(file: TFile) {
		const activeFile = this.app.workspace.getActiveFile();
		if (!activeFile || activeFile.path !== file.path) return;
		if (this.inFlightWrites > 0) return;
		if (
			this.ownWrite !== null &&
			this.ownWrite.path === file.path &&
			file.stat?.mtime === this.ownWrite.mtime
		) {
			this.ownWrite = null;
			return;
		}
		this.ownWrite = null;
		if (this.pendingSync?.file.path === file.path) {
			this.cancelPendingSync();
		}
		void this.syncFromMain();
	}

	private async syncFromMain() {
		await this.loadCards();
		this.renderIndex();
		this.refreshActiveView();
	}

	private async scrollToCard(index: number) {
		const leaf = this.app.workspace.getMostRecentLeaf();
		if (leaf && leaf.view instanceof MarkdownView) {
			const editor = leaf.view.editor;
			const cm = (editor as unknown as { cm?: EditorView }).cm;
			const range = cm?.state
				?.field(cardRangesField, false)
				?.ranges.find((r) => r.index === index);
			if (!range) return;

			const pos = { line: range.startLine, ch: 0 };
			editor.setCursor(pos);
			editor.scrollIntoView({ from: pos, to: pos }, true);
		}
	}

	private async openInAnki(index: number) {
		const card = this.cards[index];
		const nid = card?.['anki']?.['nid'];
		if (nid) {
			try {
				const success = await this.plugin.areteClient.browse(`nid:${nid}`);
				if (!success) {
					new Notice('Failed to open Anki browser.');
				}
			} catch {
				new Notice('Could not open the Anki browser, check that Anki is running.');
			}
		} else {
			new Notice('Card is not yet synced to Anki (no NID found).');
		}
	}

	private getCardWarning(index: number): boolean {
		const activeFile = this.app.workspace.getActiveFile();
		if (!activeFile) return false;
		const stats = this.plugin.statsService.getCache().concepts[activeFile.path];
		if (!stats || !stats.problematicCards) return false;

		const card = this.cards[index];
		const nid = card?.['anki']?.['nid'];
		if (!nid) return false;

		return stats.problematicCards.some((c) => c.noteId === nid);
	}

	selectCard(index: number, requestFocus = false) {
		if (index < 0 || index >= this.cards.length) return;
		if (this.hasInvalidSourceYaml()) return;
		void this.flushPendingSync();
		this.setParseError(null);

		if (this.indexContainer) {
			const prevActive = this.indexContainer.querySelector(
				`.arete-yaml-index-item[data-index="${this.currentCardIndex}"]`,
			);
			if (prevActive) prevActive.removeClass('is-active');

			const newActive = this.indexContainer.querySelector(
				`.arete-yaml-index-item[data-index="${index}"]`,
			);
			if (newActive) {
				newActive.addClass('is-active');
				if (requestFocus) {
					const list = this.indexContainer.querySelector(
						'.arete-yaml-index-list',
					) as HTMLElement;
					if (list) list.focus();
					newActive.scrollIntoView({ block: 'nearest' });
				}
			}
		}

		this.currentCardIndex = index;
		this.refreshActiveView();
		this.plugin.highlightCardLines(index);
	}

	/** Jump to a card after loading the active file; a file switch resets the position. */
	async showCardIndex(cardIndex: number) {
		const activeFile = this.app.workspace.getActiveFile();
		const sameFile = activeFile !== null && activeFile.path === this.currentFilePath;
		const flushed = await this.flushPendingSync();
		if (!flushed && sameFile) return;
		await this.loadCards();
		this.focusCard(cardIndex);
	}

	focusCard(cardIndex: number) {
		if (cardIndex >= 0 && cardIndex < this.cards.length) {
			if (this.hasInvalidSourceYaml()) return;
			void this.flushPendingSync();
			this.setParseError(null);
			this.currentCardIndex = cardIndex;
			this.renderIndex();
			this.refreshActiveView();
		}
	}

	async addCard() {
		const activeFile = this.app.workspace.getActiveFile();
		if (!activeFile) return;

		if (!(await this.flushPendingSync())) return;

		const insertIndex = this.cards.length === 0 ? 0 : this.currentCardIndex + 1;
		let updatedCards: CardData[] | null = null;

		this.inFlightWrites++;
		try {
			await this.app.fileManager.processFrontMatter(activeFile, (frontmatter) => {
				if (!Array.isArray(frontmatter.cards)) frontmatter.cards = [];

				const newCard = {
					model: 'Basic',
					Front: '',
					Back: '',
				};

				frontmatter.cards.splice(insertIndex, 0, newCard);
				updatedCards = frontmatter.cards.map((c: CardData) => cloneValue(c));
			});
			this.recordOwnWrite(activeFile);
		} catch (e) {
			console.error('[Arete] Failed to add card:', e);
			new Notice('Failed to add card.');
			return;
		} finally {
			this.inFlightWrites--;
		}

		if (updatedCards) {
			this.cards = updatedCards;
			this.currentCardIndex = Math.min(insertIndex, this.cards.length - 1);
		} else {
			await this.loadCards();
			this.currentCardIndex = Math.min(insertIndex, Math.max(0, this.cards.length - 1));
		}
		this.renderIndex();
		this.refreshActiveView();
	}

	async deleteCard(index: number) {
		const activeFile = this.app.workspace.getActiveFile();
		if (!activeFile) return;

		if (!(await this.flushPendingSync())) return;

		let updatedCards: CardData[] | null = null;
		this.inFlightWrites++;
		try {
			await this.app.fileManager.processFrontMatter(activeFile, (frontmatter) => {
				if (Array.isArray(frontmatter.cards)) {
					frontmatter.cards.splice(index, 1);
					updatedCards = frontmatter.cards.map((c: CardData) => cloneValue(c));
				}
			});
			this.recordOwnWrite(activeFile);
		} catch (e) {
			console.error('[Arete] Failed to delete card:', e);
			new Notice('Failed to delete card.');
			return;
		} finally {
			this.inFlightWrites--;
		}

		if (updatedCards) {
			this.cards = updatedCards;
		} else {
			await this.loadCards();
		}
		if (this.currentCardIndex >= this.cards.length) {
			this.currentCardIndex = Math.max(0, this.cards.length - 1);
		}
		this.renderIndex();
		this.refreshActiveView();
	}

	async reorderCards(fromIndex: number, toIndex: number) {
		const activeFile = this.app.workspace.getActiveFile();
		if (!activeFile) return;

		if (!(await this.flushPendingSync())) return;

		let updatedCards: CardData[] | null = null;
		this.inFlightWrites++;
		try {
			await this.app.fileManager.processFrontMatter(activeFile, (frontmatter) => {
				if (Array.isArray(frontmatter.cards)) {
					const [removed] = frontmatter.cards.splice(fromIndex, 1);
					frontmatter.cards.splice(toIndex, 0, removed);
					updatedCards = frontmatter.cards.map((c: CardData) => cloneValue(c));
				}
			});
			this.recordOwnWrite(activeFile);
		} catch (e) {
			console.error('[Arete] Failed to reorder cards:', e);
			new Notice('Failed to reorder cards.');
			return;
		} finally {
			this.inFlightWrites--;
		}

		if (this.currentCardIndex === fromIndex) {
			this.currentCardIndex = toIndex;
		} else if (fromIndex < this.currentCardIndex && toIndex >= this.currentCardIndex) {
			this.currentCardIndex--;
		} else if (fromIndex > this.currentCardIndex && toIndex <= this.currentCardIndex) {
			this.currentCardIndex++;
		}

		if (updatedCards) {
			this.cards = updatedCards;
		} else {
			await this.loadCards();
		}
		this.renderIndex();
		this.refreshActiveView();
	}
}
