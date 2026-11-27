import { isReservedCardKey } from '@/domain/cardKeys';
import { CardData, fitTextareas } from '@/presentation/views/yamlEditor/cardEditorHelpers';

export interface CardFieldEditorContext {
	card: CardData | undefined;
	modelName: string;
	onFieldChanged: () => void;
	renderDependencySection: (
		label: string,
		initialValues: string[],
		onChange: (newValues: string[]) => void,
	) => void;
}

export function renderCardFieldEditor(container: HTMLElement, ctx: CardFieldEditorContext): void {
	container.empty();

	const { card } = ctx;
	if (!card || typeof card !== 'object') return;

	container.createDiv({
		cls: 'arete-field-model-badge',
		text: ctx.modelName,
	});

	// Read existing dependencies without mutating the card object on view.
	const existingDeps =
		card.deps && typeof card.deps === 'object' && !Array.isArray(card.deps)
			? (card.deps as { requires?: string[]; related?: string[] })
			: null;
	const requires = Array.isArray(existingDeps?.requires) ? existingDeps.requires : [];
	const related = Array.isArray(existingDeps?.related) ? existingDeps.related : [];

	// Render generic scalar content fields
	const inputs: HTMLTextAreaElement[] = [];
	Object.entries(card).forEach(([key, value]) => {
		if (isReservedCardKey(key)) return;
		if (value !== null && typeof value === 'object') return;

		const group = container.createDiv({ cls: 'arete-field-group' });
		group.createEl('label', { cls: 'arete-field-label', text: key });

		const input = group.createEl('textarea', {
			cls: 'arete-field-input',
			text: value == null ? '' : String(value),
		}) as HTMLTextAreaElement;
		if (input) inputs.push(input);

		input?.addEventListener('input', () => {
			card[key] = input.value;
			fitTextareas([input]);
			ctx.onFieldChanged();
		});
	});
	window.setTimeout(() => fitTextareas(inputs), 0);

	const updateDeps = (depKey: 'requires' | 'related', newDeps: string[]) => {
		const current =
			card.deps && typeof card.deps === 'object' && !Array.isArray(card.deps)
				? (card.deps as Record<string, unknown>)
				: {};
		card.deps = { ...current, [depKey]: newDeps };
		ctx.onFieldChanged();
	};

	ctx.renderDependencySection('requires', requires, (newDeps) => {
		updateDeps('requires', newDeps);
	});

	ctx.renderDependencySection('related', related, (newDeps) => {
		updateDeps('related', newDeps);
	});
}
