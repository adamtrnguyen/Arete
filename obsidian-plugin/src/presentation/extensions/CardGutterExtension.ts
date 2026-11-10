import { EditorView, gutter, GutterMarker, Decoration, DecorationSet } from '@codemirror/view';
import {
	RangeSet,
	RangeSetBuilder,
	StateField,
	StateEffect,
	Text,
	Transaction,
} from '@codemirror/state';
import { Notice, setIcon, setTooltip } from 'obsidian';
import type {
	AnkiCardStats,
	CardState,
	CardStateResult,
	CardStateSettings,
	MetricState,
} from '@/domain/stats';
import { cardState } from '@/domain/stats';
import { DEFAULT_SETTINGS } from '@/domain/settings';
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
		setIcon(marker, 'arrow-down');
		setTooltip(marker, 'Skip to end of frontmatter');

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

const CARD_STATE_CLASS: Record<CardState, string | null> = {
	muted: null,
	accent: null,
	success: 'mod-success',
	warning: 'mod-warning',
	error: 'mod-error',
};

/** A difficulty or lapse figure. `ok` shows green only for difficulty, as before. */
function statSpan(text: string, state: MetricState, okIsSuccess: boolean): HTMLElement {
	const span = document.createElement('span');
	span.className = 'arete-gutter-stat';
	span.textContent = text;
	if (state === 'warning' || state === 'error') span.classList.add(`mod-${state}`);
	else if (state === 'ok' && okIsSuccess) span.classList.add('mod-success');
	return span;
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
	state: CardState;
	diffState: MetricState;
	lapseState: MetricState;
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
		result: CardStateResult,
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
		this.state = result.state;
		this.diffState = algorithm === 'fsrs' ? result.metrics.difficulty : result.metrics.ease;
		this.lapseState = result.metrics.lapses;
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
			other.isSynced === this.isSynced &&
			other.state === this.state &&
			other.diffState === this.diffState &&
			other.lapseState === this.lapseState
		);
	}

	toDOM(view: EditorView) {
		const marker = document.createElement('div');
		marker.className = 'arete-gutter-marker';
		marker.dataset.cardIndex = String(this.index);

		if (this.isStart) marker.classList.add('is-start');
		if (this.isEnd) marker.classList.add('is-end');
		if (this.isSynced) marker.classList.add('is-synced');
		const stateClass = CARD_STATE_CLASS[this.state];
		if (stateClass) marker.classList.add(stateClass);

		// Text and tooltip come from the service; colour comes from the card state.
		const visuals = CardVisualsService.getGutterVisuals(
			this.stats,
			this.algorithm,
			this.isSynced,
		);
		if (visuals.tooltip) setTooltip(marker, visuals.tooltip);

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
				infoContainer.appendChild(statSpan(visuals.diffText, this.diffState, true));
			}

			if (visuals.lapseText) {
				infoContainer.appendChild(statSpan(visuals.lapseText, this.lapseState, false));
			}

			marker.appendChild(infoContainer);
		}

		// The bar draws in the marker's state colour (see gutter.css).
		const bar = document.createElement('div');
		bar.className = 'arete-gutter-bar';
		marker.appendChild(bar);

		// Update every line of this card in this editor. Another pane, even one showing a
		// different note, has markers with the same card index; it is left alone.
		const setCardState = (hover: boolean, active: boolean) => {
			view.dom
				.querySelectorAll<HTMLElement>(
					`.arete-gutter-marker[data-card-index="${this.index}"]`,
				)
				.forEach((el) => {
					el.toggleClass('is-hovered', hover);
					el.toggleClass('is-active', active);
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

			view.dom
				.querySelectorAll<HTMLElement>('.arete-gutter-marker.is-active')
				.forEach((el) => el.toggleClass('is-active', false));
			setCardState(true, true);
			this.onClick();
		});

		return marker;
	}
}

import { CardParserService, ParseResult } from '@/application/services/CardParserService';

function parseDoc(doc: Text): ParseResult {
	return (
		CardParserService.noCardsResult(doc.iterLines()) ??
		CardParserService.parseCards(doc.toString())
	);
}

/**
 * True when every change in `tr` starts after the closing `---` line of the
 * frontmatter that `result` was parsed from. `parseCards` reads only the lines up to
 * that one when the frontmatter is closed, so such an edit leaves its result, line
 * numbers included, unchanged.
 */
function editsAfterFrontmatter(result: ParseResult, tr: Transaction): boolean {
	if (result.frontmatterEndLine === null) return false;
	const closingLineEnd = tr.startState.doc.line(result.frontmatterEndLine + 1).to;
	let after = true;
	tr.changes.iterChangedRanges((fromA) => {
		if (fromA <= closingLineEnd) after = false;
	});
	return after;
}

/**
 * The `CardParserService.parseCards` result for the current document, shared by the
 * card gutter and the card YAML highlight. The value keeps its identity across a
 * transaction only when every line it describes is unchanged in text and position,
 * so readers may reuse what they built from it.
 */
export const cardRangesField = StateField.define<ParseResult>({
	create(state) {
		return parseDoc(state.doc);
	},
	update(result, tr) {
		if (!tr.docChanged || editsAfterFrontmatter(result, tr)) return result;
		return parseDoc(tr.state.doc);
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
	// The cut-offs the state colours use, read on every call so a settings change shows at
	// the next rebuild. The default is the settings default.
	getThresholds: () => Omit<CardStateSettings, 'stats_algorithm'> = () => DEFAULT_SETTINGS,
) {
	// CodeMirror calls `markers` on every view update (cursor move, scroll, focus,
	// keystroke). The marker set is rebuilt only when the parse result or a card's stats
	// object changed; otherwise the previous set is returned and CodeMirror skips the
	// gutter DOM. One entry per editor view, since the extension is shared by all editors.
	const cache = new WeakMap<
		EditorView,
		{
			result: ParseResult;
			stats: (AnkiCardStats | null)[];
			thresholds: CardStateSettings;
			markers: RangeSet<GutterMarker>;
		}
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
				const t = getThresholds();
				const cached = cache.get(view);
				if (
					cached &&
					cached.result === result &&
					cached.stats.length === cardStats.length &&
					cached.stats.every((s, i) => s === cardStats[i]) &&
					cached.thresholds.stats_lapse_threshold === t.stats_lapse_threshold &&
					cached.thresholds.stats_difficulty_threshold === t.stats_difficulty_threshold &&
					cached.thresholds.stats_ease_threshold === t.stats_ease_threshold
				) {
					return cached.markers;
				}
				const thresholds: CardStateSettings = {
					stats_algorithm: algorithm,
					stats_lapse_threshold: t.stats_lapse_threshold,
					stats_difficulty_threshold: t.stats_difficulty_threshold,
					stats_ease_threshold: t.stats_ease_threshold,
				};

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
					const isSynced = !!range.nid;
					const state = cardState(stats, isSynced, thresholds);
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
								isSynced,
								state,
								() => onCardClick(range.index),
							),
						);
					}
				});

				const markers = builder.finish();
				cache.set(view, { result, stats: cardStats, thresholds, markers });
				return markers;
			},
		}),
	];
}
