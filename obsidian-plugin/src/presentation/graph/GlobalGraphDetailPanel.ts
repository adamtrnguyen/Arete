import { ButtonComponent } from 'obsidian';
import * as d3 from 'd3';
import { CardNode, GlobalGraphResult } from '@/domain/graph/types';

export interface FileGraphNode extends d3.SimulationNodeDatum {
	id: string; // filePath
	basename: string;
	cardCount: number;
	cardIds: string[];
	radius: number;
	type: 'file';
}

export interface CardGraphNode extends d3.SimulationNodeDatum {
	id: string; // card arete ID
	title: string;
	filePath: string;
	lineNumber: number;
	radius: number;
	fileHue: number;
	type: 'card';
}

export interface GlobalGraphDetailCallbacks {
	onSelectCardFromFile: (card: CardNode) => void;
	onSelectCard: (card: CardNode) => void;
	onOpenFileAtLine: (filePath: string, lineNumber: number) => Promise<void>;
}

export function renderDetailEmptyPanel(panel: HTMLElement): void {
	panel.empty();
	const placeholder = panel.createDiv({ cls: 'arete-global-graph-detail-empty' });
	placeholder.setText('Click a node to see details');
}

export function renderFileDetailPanel(
	panel: HTMLElement,
	node: FileGraphNode,
	graphData: GlobalGraphResult,
	cardById: Map<string, CardNode>,
	callbacks: GlobalGraphDetailCallbacks,
): void {
	panel.empty();

	const fileData = graphData.files.find((f) => f.path === node.id);
	const cardIds = fileData?.cardIds || node.cardIds;
	const cardCount = fileData?.cardCount || node.cardCount;

	const section = panel.createDiv({ cls: 'arete-global-graph-detail-section' });
	section.createEl('h3', { text: node.basename || node.id });

	const stats = section.createDiv({ cls: 'arete-global-graph-detail-stats' });
	stats.createEl('div', { text: `Cards: ${cardCount}` });
	stats.createEl('div', { text: `Path: ${node.id}` });

	const cardToFile = new Map<string, string>();
	for (const card of graphData.cards) {
		cardToFile.set(card.id, card.filePath);
	}
	let inbound = 0;
	let outbound = 0;
	for (const e of graphData.requiresEdges) {
		const fromFile = cardToFile.get(e.fromId);
		const toFile = cardToFile.get(e.toId);
		if (toFile === node.id && fromFile !== node.id) inbound++;
		if (fromFile === node.id && toFile !== node.id) outbound++;
	}
	stats.createEl('div', { text: `Inbound deps: ${inbound}` });
	stats.createEl('div', { text: `Outbound deps: ${outbound}` });

	if (cardIds.length > 0) {
		const listSection = section.createDiv();
		listSection.createEl('h4', { text: 'Cards' });
		const list = listSection.createEl('ul', { cls: 'arete-global-graph-detail-list' });
		for (const cid of cardIds) {
			const card = cardById.get(cid);
			const li = list.createEl('li');
			const link = li.createEl('a', {
				text: card?.title || cid,
				href: '#',
			});
			link.addEventListener('click', (e) => {
				e.preventDefault();
				if (card) callbacks.onSelectCardFromFile(card);
			});
		}
	}

	new ButtonComponent(section)
		.setButtonText('Open file')
		.setClass('arete-global-graph-detail-btn')
		.onClick(async () => {
			await callbacks.onOpenFileAtLine(node.id, 1);
		});
}

function renderCardRelationList(
	section: HTMLElement,
	heading: string,
	cards: CardNode[],
	onSelectCard: (card: CardNode) => void,
): void {
	if (cards.length === 0) return;
	const group = section.createDiv();
	group.createEl('h4', { text: `${heading} (${cards.length})` });
	const list = group.createEl('ul', { cls: 'arete-global-graph-detail-list' });
	for (const card of cards) {
		const li = list.createEl('li');
		const link = li.createEl('a', { text: card.title || card.id, href: '#' });
		link.addEventListener('click', (e) => {
			e.preventDefault();
			onSelectCard(card);
		});
	}
}

export function renderCardDetailPanel(
	panel: HTMLElement,
	node: CardGraphNode,
	graphData: GlobalGraphResult,
	cardById: Map<string, CardNode>,
	showRelated: boolean,
	callbacks: GlobalGraphDetailCallbacks,
): void {
	panel.empty();

	const section = panel.createDiv({ cls: 'arete-global-graph-detail-section' });
	section.createEl('h3', { text: node.title || node.id });

	const info = section.createDiv({ cls: 'arete-global-graph-detail-stats' });
	info.createEl('div', { text: `ID: ${node.id}` });

	const fileLink = info.createEl('div');
	fileLink.createSpan({ text: 'File: ' });
	const fLink = fileLink.createEl('a', {
		text: node.filePath,
		href: '#',
	});
	fLink.addEventListener('click', async (e) => {
		e.preventDefault();
		await callbacks.onOpenFileAtLine(node.filePath, 1);
	});

	info.createEl('div', { text: `Line: ${node.lineNumber}` });

	const prereqs = graphData.requiresEdges
		.filter((e) => e.fromId === node.id)
		.map((e) => cardById.get(e.toId))
		.filter((c): c is CardNode => Boolean(c));
	renderCardRelationList(section, 'Prerequisites', prereqs, callbacks.onSelectCard);

	const dependents = graphData.requiresEdges
		.filter((e) => e.toId === node.id)
		.map((e) => cardById.get(e.fromId))
		.filter((c): c is CardNode => Boolean(c));
	renderCardRelationList(section, 'Dependents', dependents, callbacks.onSelectCard);

	if (showRelated) {
		const related = graphData.relatedEdges
			.filter((e) => e.fromId === node.id || e.toId === node.id)
			.map((e) => cardById.get(e.fromId === node.id ? e.toId : e.fromId))
			.filter((c): c is CardNode => Boolean(c));
		renderCardRelationList(section, 'Related', related, callbacks.onSelectCard);
	}

	new ButtonComponent(section)
		.setButtonText('Open in editor')
		.setClass('arete-global-graph-detail-btn')
		.onClick(async () => {
			await callbacks.onOpenFileAtLine(node.filePath, node.lineNumber);
		});
}
