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
	parseYaml,
	TFile,
} from 'obsidian';
import { difficultyOutOfTen, dueEpochSeconds, metricStates } from '@/domain/stats';
import { flagClass } from '@/presentation/cardStateClass';
import { EditorView, lineNumbers, keymap } from '@codemirror/view';
import { EditorState, Annotation } from '@codemirror/state';
import { defaultKeymap, history, historyKeymap } from '@codemirror/commands';
import { yaml } from '@codemirror/lang-yaml';
import { CardStatsModal } from '@/presentation/modals/CardStatsModal';
import { DependencyField } from '@/presentation/components/DependencyField';
import { CardRenderer } from '@/presentation/renderers/CardRenderer';
import { renderCardPreviewFrame } from '@/presentation/renderers/CardPreviewFrame';
import { AretePluginSettings } from '@/domain/settings';
import { StatsService } from '@application/services/StatsService';
import { TemplateRenderer } from '@application/services/TemplateRenderer';
import { AreteClient } from '@infrastructure/arete/AreteClient';
import { debugLog } from '@/domain/debugLog';

/** What this view needs from the plugin (main.ts satisfies it; no import of main). */
export interface YamlEditorHost {
	settings: AretePluginSettings;
	statsService: StatsService;
	templateRenderer: TemplateRenderer;
	areteClient: AreteClient;
	activateLocalGraphView(cardId?: string): Promise<void>;
	highlightCardLines(cardIndex: number): void;
}

export const YAML_EDITOR_VIEW_TYPE = 'arete-yaml-editor';

/** Quiet period after an edit before the card is written back to the note. */
const SYNC_DEBOUNCE_MS = 300;

interface CardData {
	[key: string]: any;
}

// Annotation to prevent infinite sync loops
const syncAnnotation = Annotation.define<boolean>();

/** Fit textareas to their content: every write, then every read, so layout runs once. */
function fitTextareas(inputs: HTMLTextAreaElement[]) {
	for (const input of inputs) input.style.height = 'auto';
	const heights = inputs.map((input) => input.scrollHeight);
	inputs.forEach((input, i) => (input.style.height = `${heights[i]}px`));
}

// ─────────────────────────────────────────────────────────────
// Enums & Interfaces
// ─────────────────────────────────────────────────────────────

enum ViewMode {
	Source = 'source',
	Fields = 'fields',
	Preview = 'preview',
}

export class CardYamlEditorView extends ItemView {
	plugin: YamlEditorHost;
	private editorView: EditorView | null = null;
	private indexContainer: HTMLElement | null = null;
	private editorContainer: HTMLElement | null = null;
	private fieldEditorContainer: HTMLElement | null = null;
	private previewContainer: HTMLElement | null = null;
	private toolbarContainer: HTMLElement | null = null;

	private currentCardIndex = 0;
	private cards: CardData[] = [];

	private currentFilePath: string | null = null;
	private isUpdatingFromMain = false;
	private ownWriteMtime: number | null = null;
	private viewMode: ViewMode = ViewMode.Fields; // Default to Fields as requested "Card Edit Mode"
	private previewSide: 'Front' | 'Back' = 'Front';
	private fileModel = 'Basic';
	private previewRevision = 0;
	private previewComponent: Component | null = null;
	private syncDebounceTimer: ReturnType<typeof setTimeout> | null = null;

	/**
	 * Keys that describe a card rather than fill it. Everything else is an Anki
	 * field and gets handed to the template renderer.
	 */
	private static readonly RESERVED_CARD_KEYS = new Set([
		'id',
		'ID',
		'model',
		'Model',
		'deck',
		'Deck',
		'tags',
		'Tags',
		'anki',
		'Anki',
		'deps',
		'Deps',
		'markdown',
		'cid',
		'nid',
		'__line__',
	]);

	constructor(leaf: WorkspaceLeaf, plugin: YamlEditorHost) {
		super(leaf);
		this.plugin = plugin;
	}

	getViewType() {
		return 'arete-yaml-editor';
	}

	getDisplayText() {
		return 'Card editor';
	}

	getIcon() {
		return 'file-code';
	}

	async onOpen() {
		const container = this.containerEl.children[1] as HTMLElement;
		container.empty();
		container.addClass('arete-yaml-editor-container');

		// Create split layout
		this.indexContainer = container.createDiv({ cls: 'arete-yaml-index' });

		const rightPanel = container.createDiv({ cls: 'arete-yaml-editor-panel' });

		// Toolbar
		this.toolbarContainer = rightPanel.createDiv({ cls: 'arete-editor-toolbar' });

		// Containers for different modes

		// 1. Source Mode (CodeMirror)
		this.editorContainer = rightPanel.createDiv({ cls: 'arete-yaml-editor-wrapper' });

		// 2. Field Mode (Inputs)
		this.fieldEditorContainer = rightPanel.createDiv({ cls: 'arete-field-editor' });
		this.fieldEditorContainer.hide();

		// 3. Preview Mode (Rendered)
		this.previewContainer = rightPanel.createDiv({ cls: 'arete-preview-container' });
		this.previewContainer.hide();

		// Initial render
		await this.loadCards();
		this.renderIndex();
		this.renderToolbar();
		this.createEditor();
		this.setViewMode(this.viewMode);

		// Register events for sync
		this.registerEvent(
			this.app.workspace.on('active-leaf-change', () => {
				this.handleActiveFileChange();
			}),
		);

		this.registerEvent(this.app.metadataCache.on('changed', (file) => this.onFileParsed(file)));

		// Keyboard navigation on index
		this.indexContainer?.addEventListener('keydown', (e) => this.handleKeyNavigation(e));
	}

	async onClose() {
		this.clearPreview();
		if (this.editorView) {
			this.editorView.destroy();
			this.editorView = null;
		}
		if (this.syncDebounceTimer) {
			clearTimeout(this.syncDebounceTimer);
		}
	}

	private async loadCards() {
		const activeFile = this.app.workspace.getActiveFile();
		if (!activeFile) {
			this.cards = [];
			this.fileModel = 'Basic';
			this.currentFilePath = null;
			return;
		}

		// Only reset index if file changed
		if (this.currentFilePath !== activeFile.path) {
			this.currentCardIndex = 0;
			this.currentFilePath = activeFile.path;
		}

		const cache = this.app.metadataCache.getFileCache(activeFile);
		this.fileModel = cache?.frontmatter?.model || 'Basic';
		if (cache?.frontmatter?.cards && Array.isArray(cache.frontmatter.cards)) {
			this.cards = cache.frontmatter.cards as CardData[];
		} else {
			this.cards = [];
		}

		// Ensure currentCardIndex is valid
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
			if (cards !== undefined && cards === this.cards) return;
		}
		this.loadCards().then(() => {
			this.renderIndex();
			this.refreshActiveView();
		});
	}

	private refreshActiveView() {
		this.renderToolbar();
		if (this.viewMode === ViewMode.Source) {
			this.updateEditorContent();
		} else if (this.viewMode === ViewMode.Fields) {
			this.renderFieldEditor();
		} else if (this.viewMode === ViewMode.Preview) {
			this.renderPreview();
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

			const frontText = card['Front'] || card['Text'] || '';
			if (frontText) {
				setTooltip(item, frontText.substring(0, 50) + (frontText.length > 50 ? '...' : ''));
			}

			item.addEventListener('click', () => {
				this.selectCard(index, true);
			});

			item.addEventListener('contextmenu', (e) => this.showCardContextMenu(e, index));
			item.addEventListener('dragstart', (e) => this.handleDragStart(e, index));
			item.addEventListener('dragover', (e) => this.handleDragOver(e));
			item.addEventListener('drop', (e) => this.handleDrop(e, index));
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
		this.toolbarContainer.empty();

		const leftGroup = this.toolbarContainer.createDiv({ cls: 'arete-toolbar-group' });

		const activeFile = this.app.workspace.getActiveFile();
		const card = this.cards[this.currentCardIndex];
		const nid = card?.['anki']?.['nid'];

		if (activeFile && nid) {
			const cache = this.plugin.statsService.getCache().concepts[activeFile.path];
			if (cache && cache.cardStats && cache.cardStats[nid]) {
				const stats = cache.cardStats[nid];
				const statsContainer = leftGroup.createDiv({ cls: 'arete-toolbar-stats' });
				const algo = this.plugin.settings.stats_algorithm;
				// The same cut-offs as the gutter, dashboard and card stats modal.
				const states = metricStates(stats, this.plugin.settings);

				let badgeAdded = false;

				if (algo === 'fsrs' && stats.difficulty !== undefined) {
					if (stats.difficulty !== null) {
						// difficulty is stored 0..1; scale only to display it
						this.addBadge(
							statsContainer,
							`arete-stat-badge ${flagClass(states.difficulty) ?? 'mod-success'}`,
							`D: ${difficultyOutOfTen(stats.difficulty).toFixed(1)}`,
							`FSRS difficulty: ${difficultyOutOfTen(stats.difficulty).toFixed(1)}/10`,
						);
					} else {
						this.addBadge(
							statsContainer,
							'arete-stat-badge mod-muted',
							'D: ?',
							'FSRS difficulty not available',
						);
					}
					badgeAdded = true;
				} else if (algo === 'sm2' || stats.ease !== undefined) {
					const ease = Math.round(stats.ease / 10);
					this.addBadge(
						statsContainer,
						'arete-stat-badge',
						`E: ${ease}%`,
						`SM-2 ease: ${ease}%`,
					);
					badgeAdded = true;
				}

				if (stats.lapses > 0) {
					const lapseFlag = flagClass(states.lapses);
					this.addBadge(
						statsContainer,
						lapseFlag ? `arete-stat-badge ${lapseFlag}` : 'arete-stat-badge',
						`${stats.lapses}L`,
						`Lapses: ${stats.lapses}`,
					);
					badgeAdded = true;
				}

				const dueEpoch = dueEpochSeconds(stats.due);
				if (dueEpoch !== null) {
					const dueDate = new Date(dueEpoch * 1000);
					const now = new Date();
					const diffDays = Math.ceil(
						(dueDate.getTime() - now.getTime()) / (1000 * 60 * 60 * 24),
					);
					let dueText = '';
					if (diffDays < 0) dueText = `${Math.abs(diffDays)}d ago`;
					else if (diffDays === 0) dueText = 'Today';
					else dueText = `${diffDays}d`;

					this.addBadge(
						statsContainer,
						'arete-stat-badge mod-muted',
						dueText,
						`Due: ${dueDate.toLocaleDateString()}`,
					);
					badgeAdded = true;
				}

				if (!badgeAdded) {
					statsContainer.createDiv({
						cls: 'arete-stat-badge mod-muted',
						text: 'No data',
					});
				}
			} else {
				leftGroup.createDiv({
					cls: 'arete-stat-badge mod-muted',
					text: 'No data',
				});
			}
		} else if (activeFile) {
			this.addBadge(
				leftGroup,
				'arete-stat-badge mod-muted',
				'Unsynced',
				'Card not yet synced to Anki',
			);
		}

		const centerGroup = this.toolbarContainer.createDiv({ cls: 'arete-toolbar-group' });

		this.addToolbarButton(centerGroup, 'file-text', 'Go to YAML in note', () =>
			this.scrollToCard(this.currentCardIndex),
		);
		this.addToolbarButton(centerGroup, 'external-link', 'Open in Anki', () =>
			this.openInAnki(this.currentCardIndex),
		);
		this.addToolbarButton(
			centerGroup,
			'bar-chart-2',
			nid ? 'View card stats' : 'Card not synced (no NID)',
			() => {
				if (!nid) {
					new Notice('Card not synced to Anki yet (no NID).');
					return;
				}
				debugLog('[Arete] Opening stats modal for NID:', nid);
				const activeFile = this.app.workspace.getActiveFile();
				if (activeFile && nid) {
					const cache = this.plugin.statsService.getCache();
					const conceptStats = cache.concepts[activeFile.path];
					if (!conceptStats) {
						new Notice('No stats synced for this file yet.');
						return;
					}

					const cardStats = conceptStats.cardStats?.[nid];

					if (cardStats) {
						new CardStatsModal(this.app, cardStats, this.plugin.settings).open();
					} else {
						new Notice('No stats data available for this specific card.');
					}
				}
			},
			{ disabled: !nid },
		);
		this.addToolbarButton(centerGroup, 'share-2', 'Open local graph', () => {
			const current = this.cards[this.currentCardIndex];
			if (current && current.id) {
				this.plugin.activateLocalGraphView(current.id);
			} else {
				this.plugin.activateLocalGraphView();
			}
		});

		const rightGroup = this.toolbarContainer.createDiv({ cls: 'arete-toolbar-group' });

		const modes: Array<[ViewMode, string, string]> = [
			[ViewMode.Fields, 'edit-3', 'Card edit mode'],
			[ViewMode.Source, 'code', 'Source mode'],
			[ViewMode.Preview, 'eye', 'Preview mode'],
		];
		for (const [mode, icon, tooltip] of modes) {
			this.addToolbarButton(rightGroup, icon, tooltip, () => this.setViewMode(mode), {
				active: this.viewMode === mode,
			});
		}
	}

	/** An icon button in the card toolbar: Obsidian's extra-button, with a native tooltip. */
	private addToolbarButton(
		group: HTMLElement,
		icon: string,
		tooltip: string,
		onClick: () => void,
		state: { active?: boolean; disabled?: boolean } = {},
	) {
		const button = new ExtraButtonComponent(group)
			.setIcon(icon)
			.setTooltip(tooltip)
			.onClick(onClick);
		button.extraSettingsEl.addClass('arete-toolbar-btn');
		button.extraSettingsEl.toggleClass('is-active', !!state.active);
		button.extraSettingsEl.toggleClass('is-disabled', !!state.disabled);
	}

	/** A small stat badge; its explanation is a native tooltip. */
	private addBadge(parent: HTMLElement, cls: string, text: string, tooltip?: string) {
		const badge = parent.createDiv({ cls, text });
		if (tooltip) setTooltip(badge, tooltip);
	}

	private setViewMode(mode: ViewMode) {
		if (mode !== ViewMode.Preview) this.clearPreview();
		this.viewMode = mode;
		this.renderToolbar();

		this.editorContainer?.hide();
		this.fieldEditorContainer?.hide();
		this.previewContainer?.hide();

		if (mode === ViewMode.Source) {
			this.editorContainer?.show();
			this.updateEditorContent();
		} else if (mode === ViewMode.Fields) {
			this.fieldEditorContainer?.show();
			this.renderFieldEditor();
		} else if (mode === ViewMode.Preview) {
			this.previewContainer?.show();
			this.renderPreview();
		}
	}

	private renderFieldEditor() {
		if (!this.fieldEditorContainer) return;
		this.fieldEditorContainer.empty();

		const card = this.cards[this.currentCardIndex];
		if (!card) return;

		const modelName = this.getCardModel(card);
		this.fieldEditorContainer.createDiv({
			cls: 'arete-field-model-badge',
			text: modelName,
		});

		// Ensure deps structure exists
		if (!card.deps || typeof card.deps !== 'object') {
			card.deps = {};
		}
		const deps = card.deps as any;
		if (!deps.requires) deps.requires = [];
		if (!deps.related) deps.related = [];

		// Standard fields to exclude from generic loop
		const excludeFields = ['id', 'model', 'deps', 'prerequisites', 'related', 'anki'];

		// Render generic fields
		const inputs: HTMLTextAreaElement[] = [];
		Object.entries(card).forEach(([key, value]) => {
			if (excludeFields.includes(key) || key.startsWith('__')) return;

			const group = this.fieldEditorContainer?.createDiv({ cls: 'arete-field-group' });
			group?.createEl('label', { cls: 'arete-field-label', text: key });

			const input = group?.createEl('textarea', {
				cls: 'arete-field-input',
				text: String(value),
			}) as HTMLTextAreaElement;
			if (input) inputs.push(input);

			input?.addEventListener('input', () => {
				card[key] = input.value;
				fitTextareas([input]);
				this.debouncedSyncToMain();
			});
		});
		setTimeout(() => fitTextareas(inputs), 0);

		// Render Dependency Fields
		this.renderDependencySection('requires', deps.requires || [], (newDeps) => {
			deps.requires = newDeps;
			this.debouncedSyncToMain();
		});

		this.renderDependencySection('related', deps.related || [], (newDeps) => {
			deps.related = newDeps;
			this.debouncedSyncToMain();
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
				this.extractCardFields(card),
				{ sourcePath, component },
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
				this.renderPreview();
			});
		});
	}

	/** Card keys minus structural keys, cast to the string map the renderer wants. */
	private extractCardFields(card: CardData): Record<string, string> {
		const fields: Record<string, string> = {};
		for (const [key, value] of Object.entries(card)) {
			if (CardYamlEditorView.RESERVED_CARD_KEYS.has(key)) continue;
			if (value === null || value === undefined) continue;
			if (typeof value === 'object') continue;
			fields[key] = String(value);
		}
		return fields;
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
		const card = this.cards[index];
		return Object.entries(card)
			.map(([key, value]) => {
				// Ensure deps is ALWAYS treated as a structure, never a raw string
				if (key === 'deps') {
					if (typeof value === 'object' && value !== null) {
						const lines = [`deps:`];
						const deps = value as any;

						if (Array.isArray(deps.requires) && deps.requires.length > 0) {
							lines.push(`  requires:`);
							deps.requires.forEach((req: string) => lines.push(`    - ${req}`));
						}
						if (Array.isArray(deps.related) && deps.related.length > 0) {
							lines.push(`  related:`);
							deps.related.forEach((rel: string) => lines.push(`    - ${rel}`));
						}
						if (lines.length === 1) return `deps: {}`; // Empty object
						return lines.join('\n');
					}
					// If deps is anything else (string, null, etc.), force reset to empty dict
					return `deps: {}`;
				}

				if (key === 'anki') {
					if (typeof value === 'object' && value !== null) {
						const ankiBlock = value as any;
						const lines = [`anki:`];
						if (ankiBlock.nid) lines.push(`  nid: ${ankiBlock.nid}`);
						if (ankiBlock.cid) lines.push(`  cid: ${ankiBlock.cid}`);
						// If empty anki block, just return anki: {}
						if (lines.length === 1) return `anki: {}`;
						return lines.join('\n');
					}
					return `anki: {}`;
				}

				// Use |- block scalar for safer string handling (quotes, etc.)
				const isContentField = ['Front', 'Back', 'Text', 'Back Extra'].includes(key);

				if (typeof value === 'string') {
					// Check for special characters that often need quoting or indicate math/LaTeX
					// matching the Python dumper triggers for consistency.
					const needsFormatting = /[\\${}^_~'":#[\]\t\n]/.test(value);

					if (needsFormatting || isContentField) {
						if (!value) return `${key}: ''`;
						// Strip trailing newline for |- style
						const cleanValue = value.replace(/\n$/, '');
						return `${key}: |-\n  ${cleanValue.replace(/\n/g, '\n  ')}`;
					}
				}
				return `${key}: ${value}`;
			})
			.join('\n');
	}

	private parseYamlToCard(yamlStr: string): CardData {
		try {
			const raw = parseYaml(yamlStr) || {};
			return raw as CardData;
		} catch (e) {
			console.error('[Arete] Failed to parse card YAML:', e);
			return {};
		}
	}

	private debouncedSyncToMain() {
		if (this.syncDebounceTimer) clearTimeout(this.syncDebounceTimer);
		this.syncDebounceTimer = setTimeout(() => this.syncToMain(), SYNC_DEBOUNCE_MS);
	}

	private async syncToMain() {
		if (!this.editorView) return;
		const activeFile = this.app.workspace.getActiveFile();
		if (!activeFile) return;

		let yamlContent = '';
		if (this.viewMode === ViewMode.Source) {
			yamlContent = this.editorView.state.doc.toString();
		} else {
			yamlContent = this.extractCardYaml(this.currentCardIndex);
		}
		const updatedCard = this.parseYamlToCard(yamlContent);

		this.isUpdatingFromMain = true;
		try {
			await this.app.fileManager.processFrontMatter(activeFile, (frontmatter) => {
				if (frontmatter.cards && frontmatter.cards[this.currentCardIndex]) {
					frontmatter.cards[this.currentCardIndex] = updatedCard;
				}
			});
			this.ownWriteMtime = activeFile.stat?.mtime ?? null;
		} finally {
			this.isUpdatingFromMain = false;
		}
	}

	/**
	 * Reload after the active file is re-parsed. `vault.on('modify')` fires before the
	 * metadata cache updates, so reloading on it would show an external edit one edit late.
	 * Skips only the echo of this view's own write, identified by its mtime.
	 */
	onFileParsed(file: TFile) {
		const activeFile = this.app.workspace.getActiveFile();
		if (!activeFile || activeFile.path !== file.path) return;
		if (this.isUpdatingFromMain || file.stat?.mtime === this.ownWriteMtime) return;
		this.syncFromMain();
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
			const content = editor.getValue();
			const lines = content.split('\n');

			let cardCount = 0;
			let targetLine = -1;

			for (let i = 0; i < lines.length; i++) {
				if (lines[i].includes('cards:')) {
					for (let j = i + 1; j < lines.length; j++) {
						if (lines[j].trim().startsWith('-')) {
							if (cardCount === index) {
								targetLine = j;
								break;
							}
							cardCount++;
						}
					}
					break;
				}
			}

			if (targetLine !== -1) {
				editor.setCursor({ line: targetLine, ch: 0 });
				editor.scrollIntoView(
					{ from: { line: targetLine, ch: 0 }, to: { line: targetLine, ch: 0 } },
					true,
				);
			}
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
			} catch (e) {
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
		await this.loadCards();
		this.focusCard(cardIndex);
	}

	focusCard(cardIndex: number) {
		if (cardIndex >= 0 && cardIndex < this.cards.length) {
			this.currentCardIndex = cardIndex;
			this.renderIndex();
			this.refreshActiveView();
		}
	}

	async addCard() {
		const activeFile = this.app.workspace.getActiveFile();
		if (!activeFile) return;

		// Insert at current position + 1
		const insertIndex = this.currentCardIndex + 1;

		await this.app.fileManager.processFrontMatter(activeFile, (frontmatter) => {
			if (!frontmatter.cards) frontmatter.cards = [];

			// New card with model: Basic
			const newCard = {
				model: 'Basic',
				Front: '',
				Back: '',
			};

			// Insert at position after current card
			frontmatter.cards.splice(insertIndex, 0, newCard);
		});

		await this.loadCards();
		this.currentCardIndex = insertIndex;
		this.renderIndex();
		this.refreshActiveView();
	}

	async deleteCard(index: number) {
		const activeFile = this.app.workspace.getActiveFile();
		if (!activeFile) return;

		await this.app.fileManager.processFrontMatter(activeFile, (frontmatter) => {
			if (frontmatter.cards) {
				frontmatter.cards.splice(index, 1);
			}
		});

		await this.loadCards();
		if (this.currentCardIndex >= this.cards.length) {
			this.currentCardIndex = Math.max(0, this.cards.length - 1);
		}
		this.renderIndex();
		this.refreshActiveView();
	}

	async reorderCards(fromIndex: number, toIndex: number) {
		const activeFile = this.app.workspace.getActiveFile();
		if (!activeFile) return;

		await this.app.fileManager.processFrontMatter(activeFile, (frontmatter) => {
			if (frontmatter.cards) {
				const [removed] = frontmatter.cards.splice(fromIndex, 1);
				frontmatter.cards.splice(toIndex, 0, removed);
			}
		});

		if (this.currentCardIndex === fromIndex) {
			this.currentCardIndex = toIndex;
		} else if (fromIndex < this.currentCardIndex && toIndex >= this.currentCardIndex) {
			this.currentCardIndex--;
		} else if (fromIndex > this.currentCardIndex && toIndex <= this.currentCardIndex) {
			this.currentCardIndex++;
		}

		await this.loadCards();
		this.renderIndex();
		this.refreshActiveView();
	}
}
