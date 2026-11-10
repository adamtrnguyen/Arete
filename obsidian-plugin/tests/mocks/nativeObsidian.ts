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
	readonly buttons: RecordedButton[];
	readonly extraButtons: RecordedExtraButton[];
	readonly settings: RecordedSetting[];
	readonly tooltips: { el: any; text: string }[];
	readonly icons: { el: any; icon: string }[];
}

const last = (fn: any): any[] | undefined => fn.mock.calls[fn.mock.calls.length - 1];

/**
 * Resets the shared `obsidian` mock (tests/test-setup.ts) and returns a live view of what
 * the code under test built through it: buttons, extra buttons, Setting rows, tooltips
 * and icons.
 */
export function installNativeObsidian(): NativeRecord {
	const o = jest.requireMock('obsidian');
	o.ButtonComponent.instances.length = 0;
	o.ExtraButtonComponent.instances.length = 0;
	o.setTooltip.mockClear();
	o.setIcon.mockClear();
	o.Setting.mockClear();

	return {
		get buttons() {
			return o.ButtonComponent.instances.map((b: any) => ({
				container: b.containerEl,
				buttonEl: b.buttonEl,
				text: last(b.setButtonText)?.[0],
				cta: b.setCta.mock.calls.length > 0,
				warning: b.setWarning.mock.calls.length > 0,
				disabled: last(b.setDisabled)?.[0] ?? false,
				tooltip: last(b.setTooltip)?.[0],
				onClick: b.clickHandler,
			}));
		},
		get extraButtons() {
			return o.ExtraButtonComponent.instances.map((b: any) => ({
				container: b.containerEl,
				extraSettingsEl: b.extraSettingsEl,
				icon: b.icon,
				tooltip: b.tooltip,
				onClick: b.clickHandler,
			}));
		},
		get settings() {
			return o.Setting.mock.calls.map((call: any[], i: number) => {
				const s = o.Setting.mock.results[i].value;
				return {
					container: call[0],
					name: last(s.setName)?.[0],
					heading: s.setHeading.mock.calls.length > 0,
				};
			});
		},
		get tooltips() {
			return o.setTooltip.mock.calls.map(([el, text]: any[]) => ({ el, text }));
		},
		get icons() {
			return o.setIcon.mock.calls.map(([el, icon]: any[]) => ({ el, icon }));
		},
	};
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
