import { ButtonComponent, DropdownComponent, Notice, Setting } from 'obsidian';
import { StudyQueueClient } from '@/domain/ports';

export interface QueueBuilderTabContext {
	client: StudyQueueClient;
	isCurrentRevision: () => boolean;
	openFile: (filePath: string) => void;
}

export function renderQueueBuilderTab(ctx: QueueBuilderTabContext, container: HTMLElement): void {
	container.empty();

	container.createEl('h3', { text: 'Queue builder' });
	container.createEl('p', {
		text: 'Build a study queue that includes prerequisite cards before your due cards.',
		cls: 'setting-item-description',
	});

	const controls = container.createDiv({ cls: 'arete-queue-controls' });

	let selectedDeck = '';
	let depthValue = '2';
	let maxCardsValue = '50';
	let deckDropdown: DropdownComponent | null = null;

	new Setting(controls).setName('Deck filter').addDropdown((dropdown) => {
		deckDropdown = dropdown;
		dropdown.addOption('', 'All decks');
		dropdown.setValue('');
		dropdown.onChange((value) => {
			selectedDeck = value;
		});
	});

	ctx.client
		.getDeckNames()
		.then((decks) => {
			if (!ctx.isCurrentRevision()) return;
			if (
				deckDropdown?.selectEl &&
				(deckDropdown.selectEl as { isConnected?: boolean }).isConnected === false
			) {
				return;
			}
			decks.forEach((d) => deckDropdown?.addOption(d, d));
		})
		.catch((e) => {
			console.error('[Arete] Failed to load deck names:', e);
		});

	new Setting(controls).setName('Prerequisite depth').addText((text) => {
		text.setValue(depthValue).onChange((value) => {
			depthValue = value;
		});
		if (text.inputEl) {
			text.inputEl.type = 'number';
			text.inputEl.min = '1';
			text.inputEl.max = '5';
			text.inputEl.addClass('arete-queue-field');
			text.inputEl.addClass('mod-number');
		}
	});

	new Setting(controls).setName('Max cards').addText((text) => {
		text.setValue(maxCardsValue).onChange((value) => {
			maxCardsValue = value;
		});
		if (text.inputEl) {
			text.inputEl.type = 'number';
			text.inputEl.min = '10';
			text.inputEl.max = '200';
			text.inputEl.addClass('arete-queue-field');
			text.inputEl.addClass('mod-number');
		}
	});

	const buildBtn = new ButtonComponent(controls).setButtonText('Build queue').setCta();
	buildBtn.buttonEl.addClass('arete-queue-build');

	const resultsArea = container.createDiv({ cls: 'arete-queue-results' });

	buildBtn.onClick(async () => {
		buildBtn.setButtonText('Building...').setDisabled(true);

		try {
			const result = await ctx.client.buildStudyQueue(
				selectedDeck || null,
				parseInt(depthValue, 10) || 2,
				parseInt(maxCardsValue, 10) || 50,
			);

			resultsArea.empty();

			const summary = resultsArea.createDiv({ cls: 'arete-queue-summary' });
			summary.createEl('strong', { text: result.deck });
			summary.createSpan({
				text: ` — ${result.dueCount} due, ${result.totalWithPrereqs} total`,
			});

			const sendBtn = new ButtonComponent(resultsArea).setButtonText('Send to Anki');
			sendBtn.buttonEl.addClass('arete-queue-send');
			sendBtn.onClick(async () => {
				sendBtn.setButtonText('Sending...').setDisabled(true);
				try {
					const ok = await ctx.client.createQueueDeck(result.queue.map((c) => c.id));
					new Notice(ok ? 'Queue sent to Anki.' : 'Failed to create deck.');
				} catch (e) {
					console.error('[Arete] Failed to send queue to Anki:', e);
					new Notice('Failed to create deck.');
				} finally {
					sendBtn.setButtonText('Send to Anki').setDisabled(false);
				}
			});

			const list = resultsArea.createDiv({ cls: 'arete-queue-list' });
			result.queue.forEach((card) => {
				const item = list.createDiv({ cls: 'arete-queue-item' });
				item.createSpan({ text: String(card.position), cls: 'arete-queue-pos' });

				if (card.isPrereq) {
					item.createSpan({ text: 'Prerequisite', cls: 'arete-badge mod-accent' });
				}

				const title = item.createSpan({
					text: card.title,
					cls: 'arete-queue-title arete-clickable',
				});
				title.onclick = () => ctx.openFile(card.file);
			});

			new Notice(`Queue built: ${result.totalWithPrereqs} cards`);
		} catch (e) {
			console.error('[Arete] Failed to build the queue:', e);
			new Notice('Failed to build queue.');
		} finally {
			buildBtn.setButtonText('Build queue').setDisabled(false);
		}
	});
}
