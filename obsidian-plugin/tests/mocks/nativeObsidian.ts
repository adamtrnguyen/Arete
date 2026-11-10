/**
 * Obsidian components the shared `obsidian` mock (tests/test-setup.ts) lacks, installed on
 * that mock by `installNativeObsidian()` and recorded for assertions.
 */
import { createMockElement } from '../test-setup';

export interface RecordedButton {
	container: any;
	buttonEl: any;
	text?: string;
	cta: boolean;
	warning: boolean;
	disabled: boolean;
	tooltip?: string;
	onClick?: (evt?: unknown) => unknown;
}

export interface RecordedExtraButton {
	container: any;
	extraSettingsEl: any;
	icon?: string;
	tooltip?: string;
	onClick?: () => unknown;
}

export interface RecordedSetting {
	container: any;
	name?: string;
	heading: boolean;
}

export interface NativeRecord {
	buttons: RecordedButton[];
	extraButtons: RecordedExtraButton[];
	settings: RecordedSetting[];
	tooltips: { el: any; text: string }[];
	icons: { el: any; icon: string }[];
	titles: { modal: any; title: string }[];
}

export function installNativeObsidian(): NativeRecord {
	const obsidian = jest.requireMock('obsidian');
	const rec: NativeRecord = {
		buttons: [],
		extraButtons: [],
		settings: [],
		tooltips: [],
		icons: [],
		titles: [],
	};

	obsidian.ButtonComponent = class {
		private r: RecordedButton;
		buttonEl: any;
		constructor(container: any) {
			this.buttonEl = createMockElement('button');
			this.r = {
				container,
				buttonEl: this.buttonEl,
				cta: false,
				warning: false,
				disabled: false,
			};
			rec.buttons.push(this.r);
		}
		setButtonText(text: string) {
			this.r.text = text;
			return this;
		}
		setCta() {
			this.r.cta = true;
			return this;
		}
		setWarning() {
			this.r.warning = true;
			return this;
		}
		setDisabled(disabled: boolean) {
			this.r.disabled = disabled;
			return this;
		}
		setTooltip(text: string) {
			this.r.tooltip = text;
			return this;
		}
		onClick(cb: (evt?: unknown) => unknown) {
			this.r.onClick = cb;
			return this;
		}
	};

	obsidian.ExtraButtonComponent = class {
		private r: RecordedExtraButton;
		extraSettingsEl: any;
		constructor(container: any) {
			this.extraSettingsEl = createMockElement('div');
			this.r = { container, extraSettingsEl: this.extraSettingsEl };
			rec.extraButtons.push(this.r);
		}
		setIcon(icon: string) {
			this.r.icon = icon;
			return this;
		}
		setTooltip(text: string) {
			this.r.tooltip = text;
			return this;
		}
		setDisabled() {
			return this;
		}
		onClick(cb: () => unknown) {
			this.r.onClick = cb;
			return this;
		}
	};

	obsidian.Setting.mockImplementation((container: any) => {
		const r: RecordedSetting = { container, heading: false };
		rec.settings.push(r);
		const setting: any = {
			setName: (name: string) => {
				r.name = name;
				return setting;
			},
			setHeading: () => {
				r.heading = true;
				return setting;
			},
		};
		return setting;
	});

	obsidian.setTooltip = jest.fn((el: any, text: string) => {
		rec.tooltips.push({ el, text });
	});
	obsidian.setIcon = jest.fn((el: any, icon: string) => {
		rec.icons.push({ el, icon });
	});
	obsidian.Modal.prototype.setTitle = function (this: any, title: string) {
		rec.titles.push({ modal: this, title });
		return this;
	};

	return rec;
}

export interface Walked {
	el: any;
	tag: string;
	text?: string;
	classes: string[];
}

/** Every element built under `root` through createDiv / createEl / createSpan, depth first. */
export function walk(root: any): Walked[] {
	const out: Walked[] = [];
	const visit = (el: any) => {
		for (const fn of ['createDiv', 'createEl', 'createSpan'] as const) {
			const fnMock = el[fn]?.mock;
			if (!fnMock) continue;
			fnMock.results.forEach((res: any, i: number) => {
				const args = fnMock.calls[i];
				const opts = (fn === 'createEl' ? args[1] : args[0]) ?? {};
				const child = res.value;
				const added = (child.addClass?.mock?.calls ?? []).flat();
				const cls = typeof opts.cls === 'string' ? opts.cls.split(' ') : (opts.cls ?? []);
				out.push({
					el: child,
					tag: fn === 'createEl' ? args[0] : fn === 'createDiv' ? 'div' : 'span',
					text: opts.text,
					classes: [...cls, ...added],
				});
				visit(child);
			});
		}
	};
	visit(root);
	return out;
}

export const texts = (root: any): string[] =>
	walk(root)
		.map((w) => w.text)
		.filter((t): t is string => !!t);

/** Elements under `root` that carry any inline style. */
export const inlineStyled = (root: any): Walked[] =>
	walk(root).filter((w) => Object.keys(w.el.style ?? {}).length > 0);
