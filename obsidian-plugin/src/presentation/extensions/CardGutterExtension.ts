import { EditorView, gutter, GutterMarker, Decoration, DecorationSet } from '@codemirror/view';
import { RangeSet, RangeSetBuilder, StateField, StateEffect } from '@codemirror/state';
import { Notice } from 'obsidian';
import type { AnkiCardStats } from '@/domain/stats';
import { CardVisualsService } from '@/application/services/CardVisualsService';

// Re-use interfaces from service
// Effect to trigger card highlight
export const highlightCardEffect = StateEffect.define<{ cardIndex: number } | null>();

// Line decoration for highlighting
const highlightDecoration = Decoration.line({ class: 'arete-card-highlight' });

// Skip to bottom button marker
class SkipToBottomMarker extends GutterMarker {
	targetLine: number;
	view: EditorView;

	constructor(targetLine: number, view: EditorView) {
		super();
		this.targetLine = targetLine;
		this.view = view;
	}

	// CodeMirror keeps the existing DOM when this returns true.
	eq(other: GutterMarker): boolean {
		return (
			other instanceof SkipToBottomMarker &&
			other.targetLine === this.targetLine &&
			other.view === this.view
		);
	}

	toDOM() {
		const marker = document.createElement('div');
		marker.className = 'arete-skip-button';
		marker.innerHTML = '↓';
		marker.title = 'Skip to end of frontmatter';

		marker.addEventListener('click', (e) => {
			e.preventDefault();
			e.stopPropagation();
			// Clear any card highlight
			this.view.dispatch({
				effects: highlightCardEffect.of(null),
			});
			// Scroll so the frontmatter end line is at the top
			const line = this.view.state.doc.line(this.targetLine + 1);
			this.view.dispatch({
				effects: EditorView.scrollIntoView(line.from, { y: 'start' }),
			});
		});

		return marker;
	}
}

// Custom gutter marker for cards
class CardGutterMarker extends GutterMarker {
	index: number;
	isStart: boolean;
	isEnd: boolean;
	lineIndex: number;
	totalLines: number;
	stats: AnkiCardStats | null;
	algorithm: 'fsrs' | 'sm2';
	isSynced: boolean;
	onClick: () => void;

	constructor(
		index: number,
		isStart: boolean,
		isEnd: boolean,
		lineIndex: number,
		totalLines: number,
		stats: AnkiCardStats | null,
		algorithm: 'fsrs' | 'sm2',
		isSynced: boolean,
		onClick: () => void,
	) {
		super();
		this.index = index;
		this.isStart = isStart;
		this.isEnd = isEnd;
		this.lineIndex = lineIndex;
		this.totalLines = totalLines;
		this.stats = stats;
		this.algorithm = algorithm;
		this.isSynced = isSynced;
		this.onClick = onClick;
	}

	// CodeMirror keeps the existing DOM (and its listeners) when this returns true, so
	// every data field is compared. `stats` is compared by reference: the stats cache
	// replaces a card's object on refresh and never mutates it. `onClick` is left out
	// because it only forwards `index` to the gutter's click callback.
	eq(other: GutterMarker): boolean {
		return (
			other instanceof CardGutterMarker &&
			other.index === this.index &&
			other.isStart === this.isStart &&
			other.isEnd === this.isEnd &&
			other.lineIndex === this.lineIndex &&
			other.totalLines === this.totalLines &&
			other.stats === this.stats &&
			other.algorithm === this.algorithm &&
			other.isSynced === this.isSynced
		);
	}

	toDOM() {
		const marker = document.createElement('div');
		marker.className = 'arete-gutter-marker';
		marker.dataset.cardIndex = String(this.index);
		marker.style.position = 'relative';
		marker.style.height = '100%';
		marker.style.width = '100%';
		marker.style.minWidth = '20px';

		if (this.isStart) marker.classList.add('arete-gutter-start');
		if (this.isEnd) marker.classList.add('arete-gutter-end');
		if (this.isSynced) marker.classList.add('arete-gutter-synced');

		// 1. Resolve Visuals from Service (UI Separation)
		const visuals = CardVisualsService.getGutterVisuals(
			this.stats,
			this.algorithm,
			this.isSynced,
		);
		marker.title = visuals.tooltip;

		if (this.isStart) {
			const infoContainer = document.createElement('div');
			infoContainer.className = 'arete-gutter-info-container';

			// Index Badge
			const badge = document.createElement('span');
			badge.className = 'arete-gutter-badge';
			badge.textContent = String(this.index + 1);
			infoContainer.appendChild(badge);

			// Stats (Difficulty / Lapses)
			if (visuals.diffText) {
				const diffSpan = document.createElement('span');
				diffSpan.textContent = visuals.diffText;
				diffSpan.style.color = visuals.diffColor;
				infoContainer.appendChild(diffSpan);
			}

			if (visuals.lapseText) {
				const lapseSpan = document.createElement('span');
				lapseSpan.textContent = visuals.lapseText;
				lapseSpan.style.color = visuals.lapseColor;
				infoContainer.appendChild(lapseSpan);
			}

			marker.appendChild(infoContainer);
		}

		// 2. Add the colored health bar
		const bar = document.createElement('div');
		bar.className = 'arete-gutter-bar';

		if (this.isEnd) {
			bar.style.background = `linear-gradient(to bottom, ${visuals.barColor}, transparent)`;
		} else {
			bar.style.backgroundColor = visuals.barColor;
		}

		marker.appendChild(bar);

		// Helper to update all segments of this card (Sync/Hover logic)
		const setCardState = (hover: boolean, active: boolean) => {
			document
				.querySelectorAll(`.arete-gutter-marker[data-card-index="${this.index}"]`)
				.forEach((el) => {
					const b = el.querySelector('.arete-gutter-bar') as HTMLElement;
					if (hover) el.classList.add('arete-gutter-hover');
					else el.classList.remove('arete-gutter-hover');

					if (active) el.classList.add('arete-gutter-active');
					else el.classList.remove('arete-gutter-active');

					const isActive = el.classList.contains('arete-gutter-active');
					const isHover = el.classList.contains('arete-gutter-hover');

					if (isActive || isHover) {
						if (b) {
							b.style.boxShadow = `0 0 10px ${visuals.shadowColor}`;
						}
					} else {
						if (b) {
							b.style.boxShadow = 'none';
						}
					}
				});
		};

		marker.addEventListener('mouseenter', () => setCardState(true, false));
		marker.addEventListener('mouseleave', () => setCardState(false, false));

		marker.addEventListener('click', (e) => {
			e.preventDefault();
			e.stopPropagation();

			// Debug Notice
			const msg = this.stats
				? `Card ${this.index + 1}: NID ${this.stats.noteId}, Diff: ${this.stats.difficulty}`
				: `Card ${this.index + 1}: No stats found.`;
			new Notice(msg);

			document.querySelectorAll('.arete-gutter-marker.arete-gutter-active').forEach((el) => {
				el.classList.remove('arete-gutter-active');
				const b = el.querySelector('.arete-gutter-bar') as HTMLElement;
				if (b) {
					b.style.width = '3px';
					b.style.boxShadow = 'none';
				}
			});
			setCardState(true, true);
			this.onClick();
		});

		return marker;
	}
}

import { CardParserService, ParseResult } from '@/application/services/CardParserService';

// State field to track card ranges
const cardRangesField = StateField.define<ParseResult>({
	create(state) {
		return CardParserService.parseCards(state.doc.toString());
	},
	update(result, tr) {
		if (tr.docChanged) {
			return CardParserService.parseCards(tr.state.doc.toString());
		}
		return result;
	},
});

// State field to track highlighted card decorations
const cardHighlightField = StateField.define<DecorationSet>({
	create() {
		return Decoration.none;
	},
	update(decorations, tr) {
		// Check for highlight effect
		for (const e of tr.effects) {
			if (e.is(highlightCardEffect)) {
				if (e.value === null) {
					// Clear highlight
					return Decoration.none;
				}

				const cardIndex = e.value.cardIndex;
				const result = tr.state.field(cardRangesField);
				const range = result.ranges.find((r) => r.index === cardIndex);

				if (range) {
					const builder = new RangeSetBuilder<Decoration>();
					for (let lineNum = range.startLine; lineNum <= range.endLine; lineNum++) {
						if (lineNum >= tr.state.doc.lines) break;
						const line = tr.state.doc.line(lineNum + 1);
						builder.add(line.from, line.from, highlightDecoration);
					}
					return builder.finish();
				}
				return Decoration.none;
			}
		}
		return decorations;
	},
	provide: (f) => EditorView.decorations.from(f),
});

// Create the gutter extension
export function createCardGutter(
	onCardClick: (cardIndex: number) => void,
	getCardStats: (
		nid: number | null,
		cid: number | null,
		view: EditorView,
	) => AnkiCardStats | null = () => null,
	algorithm: 'fsrs' | 'sm2' = 'fsrs',
) {
	// CodeMirror calls `markers` on every view update (cursor move, scroll, focus,
	// keystroke). The marker set is rebuilt only when the parse result or a card's stats
	// object changed; otherwise the previous set is returned and CodeMirror skips the
	// gutter DOM. One entry per editor view, since the extension is shared by all editors.
	const cache = new WeakMap<
		EditorView,
		{ result: ParseResult; stats: (AnkiCardStats | null)[]; markers: RangeSet<GutterMarker> }
	>();

	return [
		cardRangesField,
		cardHighlightField,
		gutter({
			class: 'arete-card-gutter',
			markers: (view) => {
				const result = view.state.field(cardRangesField);
				// Stats are looked up once per card, and on every call, so a stats refresh
				// shows at the next view update as it would without the cache.
				const cardStats = result.ranges.map((range) =>
					range.nid || range.cid ? getCardStats(range.nid, range.cid, view) : null,
				);
				const cached = cache.get(view);
				if (
					cached &&
					cached.result === result &&
					cached.stats.length === cardStats.length &&
					cached.stats.every((s, i) => s === cardStats[i])
				) {
					return cached.markers;
				}

				const builder = new RangeSetBuilder<GutterMarker>();

				if (result.hasCards && result.frontmatterEndLine !== null) {
					const firstLine = view.state.doc.line(1);
					builder.add(
						firstLine.from,
						firstLine.from,
						new SkipToBottomMarker(result.frontmatterEndLine, view),
					);
				}

				result.ranges.forEach((range, rangeIndex) => {
					const stats = cardStats[rangeIndex];
					for (let lineNum = range.startLine; lineNum <= range.endLine; lineNum++) {
						if (lineNum >= view.state.doc.lines) break;

						const line = view.state.doc.line(lineNum + 1); // 1-indexed
						const isStart = lineNum === range.startLine;
						const isEnd = lineNum === range.endLine;
						const totalLines = range.endLine - range.startLine + 1;
						const lineIndex = lineNum - range.startLine;

						builder.add(
							line.from,
							line.from,
							new CardGutterMarker(
								range.index,
								isStart,
								isEnd,
								lineIndex,
								totalLines,
								stats,
								algorithm,
								!!range.nid,
								() => onCardClick(range.index),
							),
						);
					}
				});

				const markers = builder.finish();
				cache.set(view, { result, stats: cardStats, markers });
				return markers;
			},
		}),
	];
}
