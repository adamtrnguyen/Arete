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
	rerender: () => void;
	renderSuccess: (container: HTMLElement, message: string) => void;
	goToIssue: (file: TFile, searchText: string) => void;
}

export function renderIntegrityTab(ctx: IntegrityTabContext, container: HTMLElement): void {
	const actionArea = container.createDiv({ cls: 'arete-integrity-actions' });

	const desc = actionArea.createDiv({ cls: 'arete-integrity-desc' });
	const scans = desc.createEl('p');
	scans.createSpan({ text: 'Scans your synced files to find ' });
	scans.createEl('strong', { text: 'broken image embeds' });
	scans.createSpan({ text: ' and ' });
	scans.createEl('strong', { text: 'invalid YAML' });
	scans.createSpan({ text: '.' });
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
			ctx.setBrokenRefs(refs);
			new Notice(`Integrity check complete. Found ${refs.length} issues.`);
		} catch (e) {
			console.error('[Arete] Integrity check failed:', e);
			new Notice('Integrity check failed.');
		} finally {
			ctx.setScanning(false);
			ctx.rerender();
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
			.onClick(() => ctx.goToIssue(ref.sourceFile, ref.linkText));
	});
}
