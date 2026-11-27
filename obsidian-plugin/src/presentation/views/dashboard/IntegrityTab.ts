import { App, ButtonComponent, ExtraButtonComponent, Notice, TFile, setTooltip } from 'obsidian';
import { BrokenReference, LinkCheckerService } from '@application/services/LinkCheckerService';

const INTEGRITY_TYPE_LABEL: Record<BrokenReference['type'], string> = {
	link: 'Broken link',
	image: 'Broken image',
	'invalid-yaml': 'Invalid YAML',
};

export interface IntegrityTabContext {
	app: App;
	linkCheckerService: LinkCheckerService;
	getBrokenRefs: () => BrokenReference[] | null;
	setBrokenRefs: (refs: BrokenReference[] | null) => void;
	isScanning: () => boolean;
	setScanning: (scanning: boolean) => void;
	isClosed?: () => boolean;
	rerender: () => void;
	renderSuccess: (container: HTMLElement, message: string) => void;
	goToIssue: (file: TFile, searchText: string, line?: number) => void;
}

function resolveYamlErrorLine(ref: BrokenReference): number {
	if (typeof ref.errorLine === 'number' && ref.errorLine > 0) {
		return ref.errorLine - 1;
	}
	const match = ref.errorMessage?.match(/\bLine\s+(\d+):/i);
	if (match) {
		const parsed = parseInt(match[1], 10);
		if (parsed > 0) return parsed - 1;
	}
	return 0;
}

export function renderIntegrityTab(ctx: IntegrityTabContext, container: HTMLElement): void {
	const actionArea = container.createDiv({ cls: 'arete-integrity-actions' });

	const desc = actionArea.createDiv({ cls: 'arete-integrity-desc' });
	const scans = desc.createEl('p');
	const scanParts: Array<[tag: 'span' | 'strong', text: string]> = [
		['span', 'Scans your synced files to find '],
		['strong', 'broken image embeds'],
		['span', ' and '],
		['strong', 'invalid YAML'],
		['span', '.'],
	];
	for (const [tag, text] of scanParts) scans.createEl(tag, { text });
	desc.createEl('p', {
		text: "Use this to ensure your Anki cards don't have broken media or syntax errors.",
	});

	const scanning = ctx.isScanning();
	const checkBtn = new ButtonComponent(actionArea)
		.setButtonText(scanning ? 'Scanning vault...' : 'Run integrity check')
		.setCta()
		.setDisabled(scanning);

	checkBtn.onClick(async () => {
		ctx.setScanning(true);
		ctx.rerender();
		try {
			const targetFiles = ctx.app.vault.getMarkdownFiles();
			const refs = await ctx.linkCheckerService.checkIntegrity(targetFiles);
			if (ctx.isClosed?.()) return;
			ctx.setBrokenRefs(refs);
			new Notice(`Integrity check complete. Found ${refs.length} issues.`);
		} catch (e) {
			console.error('[Arete] Integrity check failed:', e);
			if (!ctx.isClosed?.()) new Notice('Integrity check failed.');
		} finally {
			ctx.setScanning(false);
			if (!ctx.isClosed?.()) ctx.rerender();
		}
	});

	const brokenRefs = ctx.getBrokenRefs();
	if (!brokenRefs) return;

	if (brokenRefs.length === 0) {
		ctx.renderSuccess(container, 'No broken links or missing images found!');
		return;
	}

	const list = container.createDiv();
	brokenRefs.forEach((ref) => {
		const item = list.createDiv({ cls: 'arete-integrity-item' });

		const left = item.createDiv();
		const fileLink = left.createSpan({
			text: ref.sourceFile.basename,
			cls: 'arete-integrity-file arete-clickable',
		});
		fileLink.onclick = () => ctx.app.workspace.getLeaf().openFile(ref.sourceFile);

		left.createSpan({ text: ' → ' });
		const targetSpan = left.createSpan({
			text: ref.linkPath,
			cls: 'arete-integrity-target',
		});
		setTooltip(targetSpan, `Original text: "${ref.linkText}"`);

		const right = item.createDiv({ cls: 'arete-integrity-meta' });

		if (ref.type === 'invalid-yaml' && ref.errorMessage) {
			right.createSpan({
				text: ref.errorMessage,
				cls: 'arete-integrity-error',
			});
		} else if (
			ref.type !== 'invalid-yaml' &&
			ref.linkText !== `[[${ref.linkPath}]]` &&
			ref.linkText !== ref.linkPath
		) {
			right.createSpan({
				text: `"${ref.linkText}"`,
				cls: 'arete-integrity-text',
			});
		}

		const typeBadge = right.createSpan({
			text: INTEGRITY_TYPE_LABEL[ref.type],
			cls: 'arete-badge',
		});

		if (ref.type === 'invalid-yaml') {
			typeBadge.addClass('mod-error');
			setTooltip(
				typeBadge,
				ref.errorMessage ||
					'Obsidian failed to parse frontmatter. Check for syntax errors.',
			);
		}

		new ExtraButtonComponent(right)
			.setIcon('external-link')
			.setTooltip('Go to location')
			.onClick(() => {
				if (ref.type === 'invalid-yaml') {
					ctx.goToIssue(ref.sourceFile, ref.linkText, resolveYamlErrorLine(ref));
				} else {
					ctx.goToIssue(ref.sourceFile, ref.linkText);
				}
			});
	});
}
