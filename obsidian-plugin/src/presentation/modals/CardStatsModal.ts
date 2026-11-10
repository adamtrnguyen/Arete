/**
 * Card Stats Modal - Displays detailed FSRS metrics for a single card.
 *
 * This modal is shared between DashboardView and CardYamlEditorView.
 */

import { App, ButtonComponent, Modal, Setting, setIcon } from 'obsidian';
import type { AnkiCardStats, CardStateSettings, MetricState } from '@/domain/stats';
import { difficultyOutOfTen, metricStates } from '@/domain/stats';
import { DEFAULT_SETTINGS } from '@/domain/settings';
import { flagClass } from '@/presentation/cardStateClass';

const SVG_NS = 'http://www.w3.org/2000/svg';

/** An SVG element with its class and attributes; styling lives in dashboard.css. */
function svgEl(
	tag: string,
	cls: string | null,
	attrs: Record<string, string | number> = {},
): SVGElement {
	const el = document.createElementNS(SVG_NS, tag);
	if (cls) el.setAttribute('class', cls);
	for (const [name, value] of Object.entries(attrs)) el.setAttribute(name, String(value));
	return el;
}

export class CardStatsModal extends Modal {
	card: AnkiCardStats;
	/** The cut-offs behind the state colours; the settings default when the caller has none. */
	settings: CardStateSettings;

	constructor(app: App, card: AnkiCardStats, settings: CardStateSettings = DEFAULT_SETTINGS) {
		super(app);
		this.card = card;
		this.settings = settings;
	}

	onOpen() {
		const { contentEl, modalEl } = this;
		contentEl.empty();
		modalEl.addClass('arete-stats-modal');
		contentEl.addClass('arete-stats-modal-content');
		this.setTitle('Card memory insights');

		const c = this.card;
		const states = metricStates(c, this.settings);

		// Main 2-column layout: Stats on left, Curve on right
		const mainLayout = contentEl.createDiv({ cls: 'arete-stats-layout' });

		// Left panel: Stats
		const leftPanel = mainLayout.createDiv({ cls: 'arete-stats-panel' });

		// Right panel: Forgetting Curve
		const rightPanel = mainLayout.createDiv({ cls: 'arete-curve-panel' });

		// --- Section 1: Memory State ---
		// difficulty is stored 0..1; difficultyOutOfTen renders it the way Anki shows it
		this.renderSection(leftPanel, 'Memory state', [
			{
				label: 'Difficulty (1-10)',
				value: c.difficulty != null ? difficultyOutOfTen(c.difficulty).toFixed(1) : '-',
				state: states.difficulty,
			},
			{
				label: 'Stability',
				value: c.stability != null ? `${c.stability.toFixed(1)} days` : '-',
				state: states.stability,
			},
			{
				label: 'Retrievability',
				value: c.retrievability != null ? `${(c.retrievability * 100).toFixed(1)}%` : '-',
				state: states.retrievability,
			},
		]);

		// --- Forgetting Curve Visualization (in right panel) ---
		if (c.stability != null && c.stability > 0.1) {
			this.renderForgettingCurve(
				rightPanel,
				c.stability,
				c.retrievability ?? 1,
				c.desiredRetention ?? 0.9,
			);
		} else {
			// No meaningful stability yet - show placeholder
			const placeholder = rightPanel.createDiv({ cls: 'arete-curve-placeholder' });
			placeholder.createDiv({
				text: 'Forgetting curve',
				cls: 'arete-curve-placeholder-title',
			});
			placeholder.createDiv({ text: 'Not enough review history' });
		}

		// --- Section 2: Learning Dynamics (in left panel) ---
		this.renderSection(leftPanel, 'Learning dynamics', [
			{
				label: 'Interval growth',
				value: c.intervalGrowth != null ? `${c.intervalGrowth.toFixed(2)}x` : 'N/A',
				state: states.intervalGrowth,
			},
			{
				label: 'Press fatigue (hard %)',
				value: c.pressFatigue != null ? `${(c.pressFatigue * 100).toFixed(0)}%` : 'N/A',
				state: states.pressFatigue,
			},
			{
				label: 'Schedule adherence',
				value:
					c.scheduleAdherence != null
						? `${(c.scheduleAdherence * 100).toFixed(1)}%`
						: 'N/A',
			},
			{
				label: 'Days overdue',
				value: c.daysOverdue != null ? `${c.daysOverdue}d` : '-',
				state: states.daysOverdue,
			},
		]);

		// --- Section 3: Review History (in left panel) ---
		this.renderSection(leftPanel, 'Review history', [
			{
				label: 'Total lapses',
				value: c.lapses != null ? c.lapses : '0',
				state: states.lapses,
			},
			{
				label: 'Lapse rate',
				value: c.lapseRate != null ? `${(c.lapseRate * 100).toFixed(1)}%` : '-',
			},
			{ label: 'Total reps', value: c.reps != null ? c.reps : '0' },
			{
				label: 'Average time',
				value: c.averageTime ? `${(c.averageTime / 1000).toFixed(1)}s` : '-',
			},
		]);

		// Answer Distribution (in left panel)
		if (c.answerDistribution) {
			const distribution = c.answerDistribution;
			const distSection = leftPanel.createDiv({ cls: 'arete-modal-section' });
			new Setting(distSection).setName('Rating distribution').setHeading();

			const distTable = distSection.createDiv({ cls: 'arete-modal-dist' });
			const ratings = [
				{ label: 'Again', key: 1 },
				{ label: 'Hard', key: 2 },
				{ label: 'Good', key: 3 },
				{ label: 'Easy', key: 4 },
			];

			ratings.forEach((r) => {
				const box = distTable.createDiv({ cls: 'arete-modal-dist-box' });
				box.createDiv({ text: r.label, cls: 'arete-modal-dist-label' });
				box.createDiv({
					text: (distribution[r.key] || 0).toString(),
					cls: 'arete-modal-dist-count',
				});
			});
		}

		// Flags section
		if (c.isOverlearning) {
			const alert = contentEl.createDiv({ cls: 'arete-alert' });
			const title = alert.createDiv({ cls: 'arete-alert-title' });
			setIcon(title.createSpan(), 'zap');
			title.createSpan({ text: 'Overlearning detected' });
			alert.createDiv({
				text: 'This card is being reviewed significantly before its FSRS-recommended due date with high retrievability. Consider increasing your desired retention or following the schedule more strictly.',
				cls: 'arete-alert-body',
			});
		}

		// Footer
		const footer = contentEl.createDiv({ cls: 'modal-button-container' });
		new ButtonComponent(footer).setButtonText('Close').onClick(() => this.close());
	}

	onClose() {
		const { contentEl } = this;
		contentEl.empty();
	}

	private renderSection(
		container: HTMLElement,
		title: string,
		stats: { label: string; value: string | number; state?: MetricState }[],
	) {
		const section = container.createDiv({ cls: 'arete-modal-section' });
		new Setting(section).setName(title).setHeading();

		const grid = section.createDiv({ cls: 'arete-modal-grid' });

		stats.forEach((s) => {
			const sub = grid.createDiv();
			sub.createDiv({ text: s.label, cls: 'arete-modal-label' });

			const valEl = sub.createDiv({ text: s.value.toString(), cls: 'arete-modal-value' });
			const flag = s.state ? flagClass(s.state) : null;
			if (flag) valEl.addClass(flag);
		});
	}

	/**
	 * Render a forgetting curve visualization using SVG
	 * Formula: R(t) = (1 + t/(9*S))^(-1) where S is stability in days
	 */
	private renderForgettingCurve(
		container: HTMLElement,
		stability: number,
		currentR: number,
		targetR: number,
	) {
		const wrapper = container.createDiv({ cls: 'arete-forgetting-curve' });

		new Setting(wrapper).setName('Forgetting curve').setHeading();

		// SVG dimensions - larger to fill panel
		const width = 320;
		const height = 220;
		const padding = { top: 15, right: 25, bottom: 35, left: 45 };
		const graphWidth = width - padding.left - padding.right;
		const graphHeight = height - padding.top - padding.bottom;

		// Time scale: show 3x stability or minimum 7 days
		const maxDays = Math.max(stability * 3, 7);

		// FSRS retrievability formula
		const getR = (t: number, s: number) => Math.pow(1 + t / (9 * s), -1);

		// Generate curve points
		const points: string[] = [];
		for (let i = 0; i <= 50; i++) {
			const t = (i / 50) * maxDays;
			const r = getR(t, stability);
			const x = padding.left + (t / maxDays) * graphWidth;
			const y = padding.top + (1 - r) * graphHeight;
			points.push(`${x},${y}`);
		}

		// Find where R drops to target retention
		const optimalInterval = 9 * stability * (Math.pow(targetR, -1) - 1);
		const optimalX = padding.left + (optimalInterval / maxDays) * graphWidth;

		// Create SVG
		const svg = svgEl('svg', 'arete-curve-svg', { width, height });

		// Background
		svg.appendChild(
			svgEl('rect', 'arete-curve-bg', {
				x: padding.left,
				y: padding.top,
				width: graphWidth,
				height: graphHeight,
				rx: 4,
			}),
		);

		// Target retention line
		const targetY = padding.top + (1 - targetR) * graphHeight;
		svg.appendChild(
			svgEl('line', 'arete-curve-target', {
				x1: padding.left,
				y1: targetY,
				x2: padding.left + graphWidth,
				y2: targetY,
			}),
		);

		// Optimal interval vertical line
		if (optimalX <= padding.left + graphWidth) {
			svg.appendChild(
				svgEl('line', 'arete-curve-optimal', {
					x1: optimalX,
					y1: padding.top,
					x2: optimalX,
					y2: padding.top + graphHeight,
				}),
			);
		}

		// Forgetting curve
		svg.appendChild(svgEl('polyline', 'arete-curve-line', { points: points.join(' ') }));

		// Current retrievability point - calculate elapsed days from R
		// Inverse of R(t) = (1 + t/(9*S))^(-1) -> t = 9*S * (R^(-1) - 1)
		const elapsedDays =
			currentR > 0 && currentR < 1 ? 9 * stability * (Math.pow(currentR, -1) - 1) : 0;
		const currentX = padding.left + Math.min(elapsedDays / maxDays, 1) * graphWidth;
		const currentY = padding.top + (1 - currentR) * graphHeight;
		svg.appendChild(
			svgEl(
				'circle',
				currentR >= targetR
					? 'arete-curve-point mod-success'
					: 'arete-curve-point mod-warning',
				{ cx: currentX, cy: currentY, r: 6 },
			),
		);

		// X-axis labels
		const labels = [0, Math.round(maxDays / 2), Math.round(maxDays)];
		labels.forEach((d) => {
			const lx = padding.left + (d / maxDays) * graphWidth;
			const text = svgEl('text', 'arete-curve-label', {
				x: lx,
				y: height - 5,
				'text-anchor': 'middle',
			});
			text.textContent = `${d}d`;
			svg.appendChild(text);
		});

		// Y-axis labels
		const yLabels = [100, 50, 0];
		yLabels.forEach((p) => {
			const ly = padding.top + (1 - p / 100) * graphHeight;
			const text = svgEl('text', 'arete-curve-label', {
				x: padding.left - 5,
				y: ly + 3,
				'text-anchor': 'end',
			});
			text.textContent = `${p}%`;
			svg.appendChild(text);
		});

		wrapper.appendChild(svg);

		// Legend
		const legend = wrapper.createDiv({ cls: 'arete-curve-legend' });
		legend.createSpan({
			text: `Optimal: ${optimalInterval.toFixed(1)}d`,
			cls: 'arete-curve-legend-optimal',
		});
		legend.createSpan({
			text: `Target: ${(targetR * 100).toFixed(0)}%`,
			cls: 'arete-curve-legend-target',
		});
	}
}
