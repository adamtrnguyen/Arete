/**
 * Card Stats Modal - Displays detailed FSRS metrics for a single card.
 *
 * This modal is shared between DashboardView and CardYamlEditorView.
 */

import { App, Modal, setIcon } from 'obsidian';
import type { AnkiCardStats } from '@/domain/stats';
import { difficultyOutOfTen } from '@/domain/stats';

export class CardStatsModal extends Modal {
	card: AnkiCardStats;

	constructor(app: App, card: AnkiCardStats) {
		super(app);
		this.card = card;
	}

	onOpen() {
		const { contentEl, modalEl } = this;
		contentEl.empty();
		modalEl.addClass('arete-stats-modal');
		contentEl.addClass('arete-stats-modal-content');

		contentEl.createEl('h2', { text: 'Card Memory Insights' }).style.marginBottom = '1rem';

		const c = this.card;

		// Main 2-column layout: Stats on left, Curve on right
		const mainLayout = contentEl.createDiv({ cls: 'arete-stats-layout' });
		mainLayout.style.display = 'flex';
		mainLayout.style.gap = '1rem';
		mainLayout.style.alignItems = 'stretch';

		// Left panel: Stats
		const leftPanel = mainLayout.createDiv({ cls: 'arete-stats-panel' });
		leftPanel.style.flex = '1';
		leftPanel.style.minWidth = '280px';
		leftPanel.style.background = 'var(--background-secondary)';
		leftPanel.style.borderRadius = '8px';
		leftPanel.style.padding = '1rem';

		// Right panel: Forgetting Curve
		const rightPanel = mainLayout.createDiv({ cls: 'arete-curve-panel' });
		rightPanel.style.minWidth = '300px';
		rightPanel.style.background = 'var(--background-secondary)';
		rightPanel.style.borderRadius = '8px';
		rightPanel.style.padding = '1rem';

		// --- Section 1: Memory State ---
		// difficulty is stored 0..1; difficultyOutOfTen renders it the way Anki shows it
		this.renderSection(leftPanel, 'Memory State', [
			{
				label: 'Difficulty (1-10)',
				value: c.difficulty != null ? difficultyOutOfTen(c.difficulty).toFixed(1) : '-',
				color:
					c.difficulty != null && c.difficulty > 0.7 ? 'var(--color-orange)' : undefined,
			},
			{
				label: 'Stability',
				value: c.stability != null ? `${c.stability.toFixed(1)} days` : '-',
				color: c.stability != null && c.stability < 7 ? 'var(--color-orange)' : undefined,
			},
			{
				label: 'Retrievability',
				value: c.retrievability != null ? `${(c.retrievability * 100).toFixed(1)}%` : '-',
				color:
					c.retrievability != null && c.retrievability < 0.85
						? 'var(--color-red)'
						: undefined,
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
			placeholder.style.display = 'flex';
			placeholder.style.flexDirection = 'column';
			placeholder.style.alignItems = 'center';
			placeholder.style.justifyContent = 'center';
			placeholder.style.height = '150px';
			placeholder.style.color = 'var(--text-muted)';
			placeholder.style.fontSize = '0.85em';
			placeholder.createDiv({ text: 'Forgetting Curve' }).style.fontWeight = 'bold';
			placeholder.createDiv({ text: 'Not enough review history' }).style.marginTop = '0.5rem';
		}

		// --- Section 2: Learning Dynamics (in left panel) ---
		this.renderSection(leftPanel, 'Learning Dynamics', [
			{
				label: 'Interval Growth',
				value: c.intervalGrowth != null ? `${c.intervalGrowth.toFixed(2)}x` : 'N/A',
				color:
					c.intervalGrowth != null && c.intervalGrowth < 1.2
						? 'var(--color-orange)'
						: undefined,
			},
			{
				label: 'Press Fatigue (Hard%)',
				value: c.pressFatigue != null ? `${(c.pressFatigue * 100).toFixed(0)}%` : 'N/A',
				color:
					c.pressFatigue != null && c.pressFatigue > 0.3 ? 'var(--color-red)' : undefined,
			},
			{
				label: 'Schedule Adherence',
				value:
					c.scheduleAdherence != null
						? `${(c.scheduleAdherence * 100).toFixed(1)}%`
						: 'N/A',
			},
			{
				label: 'Days Overdue',
				value: c.daysOverdue != null ? `${c.daysOverdue}d` : '-',
				color: c.daysOverdue != null && c.daysOverdue > 7 ? 'var(--color-red)' : undefined,
			},
		]);

		// --- Section 3: Review History (in left panel) ---
		this.renderSection(leftPanel, 'Review History', [
			{
				label: 'Total Lapses',
				value: c.lapses != null ? c.lapses : '0',
				color: c.lapses != null && c.lapses > 5 ? 'var(--color-red)' : undefined,
			},
			{
				label: 'Lapse Rate',
				value: c.lapseRate != null ? `${(c.lapseRate * 100).toFixed(1)}%` : '-',
			},
			{ label: 'Total Reps', value: c.reps != null ? c.reps : '0' },
			{
				label: 'Avg Time',
				value: c.averageTime ? `${(c.averageTime / 1000).toFixed(1)}s` : '-',
			},
		]);

		// Answer Distribution (in left panel)
		if (c.answerDistribution) {
			const distHeader = leftPanel.createEl('h3', { text: 'Rating Distribution' });
			distHeader.style.margin = '1rem 0 0.5rem 0';
			distHeader.style.fontSize = '0.85em';
			distHeader.style.textTransform = 'uppercase';
			distHeader.style.letterSpacing = '1px';
			distHeader.style.color = 'var(--text-accent)';

			const distTable = leftPanel.createDiv({ cls: 'arete-modal-dist' });
			distTable.style.display = 'flex';
			distTable.style.gap = '0.3rem';
			distTable.style.marginBottom = '0.5rem';

			const ratings = [
				{ label: 'Again', key: 1, color: 'var(--color-red)' },
				{ label: 'Hard', key: 2, color: 'var(--color-orange)' },
				{ label: 'Good', key: 3, color: 'var(--color-green)' },
				{ label: 'Easy', key: 4, color: 'var(--color-blue)' },
			];

			ratings.forEach((r) => {
				const box = distTable.createDiv();
				box.style.flex = '1';
				box.style.padding = '6px';
				box.style.background = 'var(--background-primary)';
				box.style.borderRadius = '4px';
				box.style.textAlign = 'center';
				box.createDiv({ text: r.label }).style.fontSize = '0.7em';
				box.createDiv({
					text: (c.answerDistribution![r.key] || 0).toString(),
				}).style.fontWeight = 'bold';
			});
		}

		// Flags section
		if (c.isOverlearning) {
			const alert = contentEl.createDiv({ cls: 'arete-alert' });
			alert.style.background = 'rgba(var(--color-yellow-rgb), 0.1)';
			alert.style.padding = '10px';
			alert.style.borderRadius = '5px';
			alert.style.marginTop = '1rem';
			alert.style.border = '1px solid var(--color-yellow)';
			const title = alert.createDiv({ cls: 'arete-alert-title' });
			setIcon(title.createSpan(), 'zap');
			title.createSpan({ text: ' Overlearning Detected' }).style.fontWeight = 'bold';
			alert.createDiv({
				text: 'This card is being reviewed significantly before its FSRS-recommended due date with high retrievability. Consider increasing your desired retention or following the schedule more strictly.',
				cls: 'arete-alert-body',
			}).style.fontSize = '0.85em';
		}

		// Footer
		const footer = contentEl.createDiv();
		footer.style.marginTop = '2rem';
		footer.style.textAlign = 'right';
		const closeBtn = footer.createEl('button', { text: 'Close' });
		closeBtn.onclick = () => this.close();
	}

	onClose() {
		const { contentEl } = this;
		contentEl.empty();
	}

	private renderSection(
		container: HTMLElement,
		title: string,
		stats: { label: string; value: string | number; color?: string }[],
	) {
		const section = container.createDiv({ cls: 'arete-modal-section' });
		section.style.marginBottom = '1.2rem';

		const h3 = section.createEl('h3', { text: title });
		h3.style.margin = '0 0 0.6rem 0';
		h3.style.fontSize = '0.9em';
		h3.style.textTransform = 'uppercase';
		h3.style.letterSpacing = '1px';
		h3.style.color = 'var(--text-accent)';
		h3.style.borderBottom = '1px solid var(--background-modifier-border)';
		h3.style.paddingBottom = '4px';

		const grid = section.createDiv();
		grid.style.display = 'grid';
		grid.style.gridTemplateColumns = '1fr 1fr';
		grid.style.gap = '0.8rem 1.5rem';

		stats.forEach((s) => {
			const sub = grid.createDiv();
			const labelEl = sub.createDiv({ text: s.label });
			labelEl.style.fontSize = '0.75em';
			labelEl.style.color = 'var(--text-muted)';

			const valEl = sub.createDiv({ text: s.value.toString() });
			valEl.style.fontSize = '1.1em';
			valEl.style.fontWeight = '600';
			if (s.color) valEl.style.color = s.color;
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
		wrapper.style.height = '100%';
		wrapper.style.display = 'flex';
		wrapper.style.flexDirection = 'column';

		const header = wrapper.createEl('h3', { text: 'Forgetting Curve' });
		header.style.margin = '0 0 0.6rem 0';
		header.style.fontSize = '0.9em';
		header.style.textTransform = 'uppercase';
		header.style.letterSpacing = '1px';
		header.style.color = 'var(--text-accent)';
		header.style.borderBottom = '1px solid var(--background-modifier-border)';
		header.style.paddingBottom = '4px';

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
		const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
		svg.setAttribute('width', String(width));
		svg.setAttribute('height', String(height));
		svg.style.display = 'block';
		svg.style.margin = '0 auto';

		// Background
		const bg = document.createElementNS('http://www.w3.org/2000/svg', 'rect');
		bg.setAttribute('x', String(padding.left));
		bg.setAttribute('y', String(padding.top));
		bg.setAttribute('width', String(graphWidth));
		bg.setAttribute('height', String(graphHeight));
		bg.setAttribute('fill', 'var(--background-secondary)');
		bg.setAttribute('rx', '4');
		svg.appendChild(bg);

		// Target retention line
		const targetY = padding.top + (1 - targetR) * graphHeight;
		const targetLine = document.createElementNS('http://www.w3.org/2000/svg', 'line');
		targetLine.setAttribute('x1', String(padding.left));
		targetLine.setAttribute('y1', String(targetY));
		targetLine.setAttribute('x2', String(padding.left + graphWidth));
		targetLine.setAttribute('y2', String(targetY));
		targetLine.setAttribute('stroke', 'var(--color-green)');
		targetLine.setAttribute('stroke-dasharray', '4,4');
		targetLine.setAttribute('stroke-width', '1');
		svg.appendChild(targetLine);

		// Optimal interval vertical line
		if (optimalX <= padding.left + graphWidth) {
			const optLine = document.createElementNS('http://www.w3.org/2000/svg', 'line');
			optLine.setAttribute('x1', String(optimalX));
			optLine.setAttribute('y1', String(padding.top));
			optLine.setAttribute('x2', String(optimalX));
			optLine.setAttribute('y2', String(padding.top + graphHeight));
			optLine.setAttribute('stroke', 'var(--color-blue)');
			optLine.setAttribute('stroke-dasharray', '2,2');
			optLine.setAttribute('stroke-width', '1');
			svg.appendChild(optLine);
		}

		// Forgetting curve
		const curve = document.createElementNS('http://www.w3.org/2000/svg', 'polyline');
		curve.setAttribute('points', points.join(' '));
		curve.setAttribute('fill', 'none');
		curve.setAttribute('stroke', 'var(--color-orange)');
		curve.setAttribute('stroke-width', '2');
		svg.appendChild(curve);

		// Current retrievability point - calculate elapsed days from R
		// Inverse of R(t) = (1 + t/(9*S))^(-1) -> t = 9*S * (R^(-1) - 1)
		const elapsedDays =
			currentR > 0 && currentR < 1 ? 9 * stability * (Math.pow(currentR, -1) - 1) : 0;
		const currentX = padding.left + Math.min(elapsedDays / maxDays, 1) * graphWidth;
		const currentY = padding.top + (1 - currentR) * graphHeight;
		const currentPoint = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
		currentPoint.setAttribute('cx', String(currentX));
		currentPoint.setAttribute('cy', String(currentY));
		currentPoint.setAttribute('r', '6');
		currentPoint.setAttribute(
			'fill',
			currentR >= targetR ? 'var(--color-green)' : 'var(--color-red)',
		);
		currentPoint.setAttribute('stroke', 'white');
		currentPoint.setAttribute('stroke-width', '2');
		svg.appendChild(currentPoint);

		// X-axis labels
		const labels = [0, Math.round(maxDays / 2), Math.round(maxDays)];
		labels.forEach((d) => {
			const lx = padding.left + (d / maxDays) * graphWidth;
			const text = document.createElementNS('http://www.w3.org/2000/svg', 'text');
			text.setAttribute('x', String(lx));
			text.setAttribute('y', String(height - 5));
			text.setAttribute('text-anchor', 'middle');
			text.setAttribute('font-size', '9');
			text.setAttribute('fill', 'var(--text-muted)');
			text.textContent = `${d}d`;
			svg.appendChild(text);
		});

		// Y-axis labels
		const yLabels = [100, 50, 0];
		yLabels.forEach((p) => {
			const ly = padding.top + (1 - p / 100) * graphHeight;
			const text = document.createElementNS('http://www.w3.org/2000/svg', 'text');
			text.setAttribute('x', String(padding.left - 5));
			text.setAttribute('y', String(ly + 3));
			text.setAttribute('text-anchor', 'end');
			text.setAttribute('font-size', '9');
			text.setAttribute('fill', 'var(--text-muted)');
			text.textContent = `${p}%`;
			svg.appendChild(text);
		});

		wrapper.appendChild(svg);

		// Legend
		const legend = wrapper.createDiv();
		legend.style.display = 'flex';
		legend.style.justifyContent = 'center';
		legend.style.gap = '1rem';
		legend.style.fontSize = '0.7em';
		legend.style.color = 'var(--text-muted)';
		legend.style.marginTop = '0.3rem';

		legend.createSpan({ text: `Optimal: ${optimalInterval.toFixed(1)}d` }).style.color =
			'var(--color-blue)';
		legend.createSpan({ text: `Target: ${(targetR * 100).toFixed(0)}%` }).style.color =
			'var(--color-green)';
	}
}
