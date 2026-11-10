import { App, ButtonComponent, ExtraButtonComponent, setIcon } from 'obsidian';
import { DependencySelectionModal } from '@/presentation/modals/DependencySelectionModal';

export interface Dependency {
	type: 'file' | 'card';
	value: string; // basename or card ID
	label?: string; // Display text
}

export class DependencyField {
	private container: HTMLElement;
	private app: App;
	private dependencies: Dependency[] = [];
	private onChange: (deps: string[]) => void;

	private listEl: HTMLElement;

	constructor(
		container: HTMLElement,
		app: App,
		initialValues: string[],
		onChange: (deps: string[]) => void,
	) {
		this.container = container;
		this.app = app;
		this.onChange = onChange;

		// Parse initial values
		this.dependencies = initialValues.map((val) => ({
			type: val.startsWith('arete_') ? 'card' : 'file',
			value: val,
			label: val, // We'll resolve labels later
		}));

		this.render();
	}

	private render() {
		this.container.addClass('arete-dependency-field');

		// Selected items list
		this.listEl = this.container.createDiv({ cls: 'arete-dep-list' });
		this.renderList();

		// Input wrapper
		const inputWrapper = this.container.createDiv({ cls: 'arete-dep-input-wrapper' });

		const addBtn = new ButtonComponent(inputWrapper)
			.setIcon('plus')
			.setButtonText('Add dependency')
			.onClick(() => {
				new DependencySelectionModal(this.app, (result) => {
					if (result) {
						// Detect type based on prefix
						const type = result.startsWith('arete_') ? 'card' : 'file';
						this.addDependency(type, result);
					}
				}).open();
			});
		addBtn.buttonEl.addClass('arete-dep-add-btn');
	}

	private renderList() {
		this.listEl.empty();
		this.dependencies.forEach((dep, index) => {
			const chip = this.listEl.createDiv({ cls: 'arete-dep-chip' });
			if (dep.type === 'card') chip.addClass('is-card');

			// Icon
			const iconSpan = chip.createSpan({ cls: 'arete-dep-icon' });
			setIcon(iconSpan, dep.type === 'card' ? 'id-card' : 'file-text');

			// Text
			chip.createSpan({ text: dep.label || dep.value, cls: 'arete-dep-text' });

			// Remove
			new ExtraButtonComponent(chip)
				.setIcon('x')
				.setTooltip('Remove dependency')
				.onClick(() => {
					this.dependencies.splice(index, 1);
					this.update();
				})
				.extraSettingsEl.addClass('arete-dep-remove');
		});
	}

	private addDependency(type: 'file' | 'card', value: string) {
		if (this.dependencies.find((d) => d.value === value)) return;

		this.dependencies.push({ type, value, label: value });
		this.update();
	}

	private update() {
		this.renderList();
		this.onChange(this.dependencies.map((d) => d.value));
	}
}
