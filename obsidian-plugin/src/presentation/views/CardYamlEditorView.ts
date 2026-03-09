import { ItemView, WorkspaceLeaf, MarkdownView, Menu, setIcon, Notice, parseYaml } from 'obsidian';
import { EditorView, lineNumbers, keymap } from '@codemirror/view';
import { EditorState, Annotation } from '@codemirror/state';
import { defaultKeymap, history, historyKeymap } from '@codemirror/commands';
import { yaml } from '@codemirror/lang-yaml';
import type AretePlugin from '@/main';
import { CardStatsModal } from '@/presentation/modals/CardStatsModal';
import { DependencyField } from '@/presentation/components/DependencyField';
import { CardRenderer } from '@/presentation/renderers/CardRenderer';

export const YAML_EDITOR_VIEW_TYPE = 'arete-yaml-editor';

interface CardData {
	[key: string]: any;
}

// Annotation to prevent infinite sync loops
const syncAnnotation = Annotation.define<boolean>();

// ─────────────────────────────────────────────────────────────
// Enums & Interfaces
// ─────────────────────────────────────────────────────────────

enum ViewMode {
	Source = 'source',
	Fields = 'fields',
	Preview = 'preview',
}

export class CardYamlEditorView extends ItemView {
	plugin: AretePlugin;
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
	private viewMode: ViewMode = ViewMode.Fields; // Default to Fields as requested "Card Edit Mode"
	private syncDebounceTimer: ReturnType<typeof setTimeout> | null = null;

	constructor(leaf: WorkspaceLeaf, plugin: AretePlugin) {
		super(leaf);
		this.plugin = plugin;
	}

	getViewType() {
		return 'arete-yaml-editor';
	}

	getDisplayText() {
		return 'Card Editor';
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
		this.editorContainer.style.flex = '1';
		this.editorContainer.style.overflow = 'hidden';

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

		this.registerEvent(
			this.app.vault.on('modify', (file) => {
				const activeFile = this.app.workspace.getActiveFile();
				if (activeFile && activeFile.path === file.path && !this.isUpdatingFromMain) {
					this.syncFromMain();
				}
			}),
		);

		// Keyboard navigation on index
		this.indexContainer?.addEventListener('keydown', (e) => this.handleKeyNavigation(e));
	}

	async onClose() {
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
			this.currentFilePath = null;
			return;
		}

		// Only reset index if file changed
		if (this.currentFilePath !== activeFile.path) {
			this.currentCardIndex = 0;
			this.currentFilePath = activeFile.path;
		}

		const cache = this.app.metadataCache.getFileCache(activeFile);
		if (cache?.frontmatter?.cards && Array.isArray(cache.frontmatter.cards)) {
			this.cards = cache.frontmatter.cards.map((c: any) => this.normalizeCard(c));
		} else {
			this.cards = [];
		}

		// Ensure currentCardIndex is valid
		if (this.currentCardIndex >= this.cards.length) {
			this.currentCardIndex = Math.max(0, this.cards.length - 1);
		}
	}

	private handleActiveFileChange() {
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

			const frontText = card['front'] || card['Front'] || '';
			if (frontText) {
				item.setAttribute(
					'title',
					frontText.substring(0, 50) + (frontText.length > 50 ? '...' : ''),
				);
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

		const addBtn = this.indexContainer.createDiv({ cls: 'arete-yaml-index-add' });
		setIcon(addBtn, 'plus');
		addBtn.addEventListener('click', () => this.addCard());
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
		if (el) el.addClass('drag-over');
	}

	private handleDragEnd() {
		const items = this.indexContainer?.querySelectorAll('.arete-yaml-index-item');
		items?.forEach((item) => {
			item.removeClass('is-dragging');
			item.removeClass('drag-over');
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
			item.setTitle('Delete Card')
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

				let badgeAdded = false;

				if (algo === 'fsrs' && stats.difficulty !== undefined) {
					if (stats.difficulty !== null) {
						// difficulty is already 1-10 scale from backend
						let diffCls = 'arete-stat-badge';
						if (stats.difficulty > 8) diffCls += ' mod-warning';
						else if (stats.difficulty > 5) diffCls += ' mod-orange';
						else diffCls += ' mod-success';

						statsContainer.createDiv({
							cls: diffCls,
							text: `D: ${stats.difficulty.toFixed(1)}`,
							attr: {
								title: `FSRS Difficulty: ${stats.difficulty.toFixed(1)}/10`,
							},
						});
					} else {
						statsContainer.createDiv({
							cls: 'arete-stat-badge mod-muted',
							text: `D: ?`,
							attr: { title: 'FSRS Difficulty not available' },
						});
					}
					badgeAdded = true;
				} else if (algo === 'sm2' || stats.ease !== undefined) {
					const ease = Math.round(stats.ease / 10);
					statsContainer.createDiv({
						cls: 'arete-stat-badge',
						text: `E: ${ease}%`,
						attr: { title: `SM-2 Ease: ${ease}%` },
					});
					badgeAdded = true;
				}

				if (stats.lapses > 0) {
					let lapseCls = 'arete-stat-badge';
					if (stats.lapses > 5) lapseCls += ' mod-warning';
					else lapseCls += ' mod-orange';

					statsContainer.createDiv({
						cls: lapseCls,
						text: `${stats.lapses}L`,
						attr: { title: `Lapses: ${stats.lapses}` },
					});
					badgeAdded = true;
				}

				if (stats.due) {
					const dueDate = new Date(stats.due * 1000);
					const now = new Date();
					const diffDays = Math.ceil(
						(dueDate.getTime() - now.getTime()) / (1000 * 60 * 60 * 24),
					);
					let dueText = '';
					if (diffDays < 0) dueText = `${Math.abs(diffDays)}d ago`;
					else if (diffDays === 0) dueText = 'Today';
					else dueText = `${diffDays}d`;

					statsContainer.createDiv({
						cls: 'arete-stat-badge mod-muted',
						text: dueText,
						attr: { title: `Due: ${dueDate.toLocaleDateString()}` },
					});
					badgeAdded = true;
				}

				if (!badgeAdded) {
					statsContainer.createDiv({
						cls: 'arete-stat-badge mod-muted',
						text: 'No Data',
					});
				}
			} else {
				leftGroup.createDiv({
					cls: 'arete-stat-badge mod-muted',
					text: 'No Data',
				});
			}
		} else if (activeFile) {
			leftGroup.createDiv({
				cls: 'arete-stat-badge mod-muted',
				text: 'Unsynced',
				attr: { title: 'Card not yet synced to Anki' },
			});
		}

		const centerGroup = this.toolbarContainer.createDiv({ cls: 'arete-toolbar-group' });

		const yamlBtn = centerGroup.createDiv({
			cls: 'arete-toolbar-btn',
			attr: { title: 'Go to YAML in Note' },
		});
		setIcon(yamlBtn, 'file-text');
		yamlBtn.addEventListener('click', () => this.scrollToCard(this.currentCardIndex));

		const ankiBtn = centerGroup.createDiv({
			cls: 'arete-toolbar-btn',
			attr: { title: 'Open in Anki' },
		});
		setIcon(ankiBtn, 'external-link');
		ankiBtn.addEventListener('click', () => this.openInAnki(this.currentCardIndex));

		// Stats Modal Button
		const statsBtn = centerGroup.createDiv({
			cls: 'arete-toolbar-btn',
			attr: { title: nid ? 'View Card Stats' : 'Card not synced (No NID)' },
		});
		setIcon(statsBtn, 'bar-chart-2');
		if (!nid) {
			statsBtn.style.opacity = '0.4';
			statsBtn.style.cursor = 'not-allowed';
		}

		statsBtn.addEventListener('click', () => {
			if (!nid) {
				new Notice('Card not synced to Anki yet (No NID).');
				return;
			}
			console.log('[Arete] Opening stats modal for NID:', nid);
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
					new CardStatsModal(this.app, cardStats).open();
				} else {
					new Notice('No stats data available for this specific card.');
				}
			}
		});

		// Graph View Button
		const graphBtn = centerGroup.createDiv({
			cls: 'arete-toolbar-btn',
			attr: { title: 'Open Local Graph' },
		});
		setIcon(graphBtn, 'share-2');
		graphBtn.addEventListener('click', () => {
			const current = this.cards[this.currentCardIndex];
			if (current && current.id) {
				this.plugin.activateLocalGraphView(current.id);
			} else {
				this.plugin.activateLocalGraphView();
			}
		});

		const rightGroup = this.toolbarContainer.createDiv({ cls: 'arete-toolbar-group' });

		const fieldBtn = rightGroup.createDiv({
			cls: 'arete-toolbar-btn' + (this.viewMode === ViewMode.Fields ? ' is-active' : ''),
			attr: { title: 'Card Edit Mode' },
		});
		setIcon(fieldBtn, 'edit-3');
		fieldBtn.addEventListener('click', () => this.setViewMode(ViewMode.Fields));

		const sourceBtn = rightGroup.createDiv({
			cls: 'arete-toolbar-btn' + (this.viewMode === ViewMode.Source ? ' is-active' : ''),
			attr: { title: 'Source Mode' },
		});
		setIcon(sourceBtn, 'code');
		sourceBtn.addEventListener('click', () => this.setViewMode(ViewMode.Source));

		const previewBtn = rightGroup.createDiv({
			cls: 'arete-toolbar-btn' + (this.viewMode === ViewMode.Preview ? ' is-active' : ''),
			attr: { title: 'Preview Mode' },
		});
		setIcon(previewBtn, 'eye');
		previewBtn.addEventListener('click', () => this.setViewMode(ViewMode.Preview));
	}

	private setViewMode(mode: ViewMode) {
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

		const modelName = card.model || 'Basic';
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
		const excludeFields = [
			'id',
			'model',
			'deps',
			'prerequisites',
			'related',
			'anki',
		];

		// Render generic fields
		Object.entries(card).forEach(([key, value]) => {
			if (excludeFields.includes(key) || key.startsWith('__')) return;

			const group = this.fieldEditorContainer?.createDiv({ cls: 'arete-field-group' });
			group?.createEl('label', { cls: 'arete-field-label', text: key });

			const input = group?.createEl('textarea', {
				cls: 'arete-field-input',
				text: String(value),
			}) as HTMLTextAreaElement;

			const autoResize = () => {
				if (input) {
					input.style.height = 'auto';
					input.style.height = `${input.scrollHeight}px`;
				}
			};
			setTimeout(autoResize, 0);

			input?.addEventListener('input', () => {
				card[key] = input.value;
				autoResize();
				this.debouncedSyncToMain();
			});
		});

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

	private async renderPreview() {
		if (!this.previewContainer) return;
		this.previewContainer.empty();

		const card = this.cards[this.currentCardIndex];
		if (!card) return;

		// Use shared renderer for consistency
		await CardRenderer.render(
			this.app,
			this.previewContainer,
			card,
			this.currentFilePath || '',
			this,
		);
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
				const isContentField = [
					'front',
					'back',
					'text',
					'extra',
					'Front',
					'Back',
					'Text',
					'Extra',
				].includes(key);

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
			return this.normalizeCard(raw);
		} catch (e) {
			console.error('[Arete] Failed to parse card YAML:', e);
			return {};
		}
	}

	/**
	 * Normalizes card keys to lowercase for internal consistency.
	 * Maps ID -> id, Model -> model, etc.
	 */
	private normalizeCard(card: any): CardData {
		const normalized: CardData = { ...card };

		// Map common uppercase keys to lowercase
		if (card.ID && !card.id) normalized.id = card.ID;
		if (card.Model && !card.model) normalized.model = card.Model;
		// Cleanup uppercase leftover if mapped
		if (card.ID) delete normalized['ID'];
		if (card.Model) delete normalized['Model'];

		return normalized;
	}

	private debouncedSyncToMain() {
		if (this.syncDebounceTimer) clearTimeout(this.syncDebounceTimer);
		this.syncDebounceTimer = setTimeout(() => this.syncToMain(), 300);
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
		} finally {
			setTimeout(() => {
				this.isUpdatingFromMain = false;
			}, 100);
		}
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
				new Notice('Failed to open Anki browser. Check if the server/CLI is accessible.');
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
