import { createMockElement } from '../test-setup';

export interface MockDomElement {
	tag: string;
	opts: any;
	style: Record<string, string>;
	attrs: Record<string, string>;
	classes: Set<string>;
	children: MockDomElement[];
	textContent: string;
	value: string;
	title?: string;
	disabled?: boolean;
	onclick?: () => any;
	[k: string]: any;
}

/**
 * Shared Obsidian-shaped mock element that tracks children, classes, attributes,
 * textContent, and supports simple `.cls` querySelector lookups.
 */
export function createDomElement(
	tag = 'div',
	opts: any = {},
	childDefaults: Record<string, any> = {},
): MockDomElement {
	const mergedOpts = { ...childDefaults, ...opts };
	const base = createMockElement(tag, mergedOpts);
	const children: MockDomElement[] = [];
	const classes = new Set<string>();
	const attrs: Record<string, string> = {};

	const el: MockDomElement = Object.assign(base, {
		tag,
		opts: mergedOpts,
		style: mergedOpts.style ?? {},
		attrs,
		classes,
		children,
		textContent: mergedOpts.text ?? '',
		value: mergedOpts.value ?? '',
		empty: jest.fn(() => {
			children.length = 0;
			return el;
		}),
		toggleClass: jest.fn((cls: string, on: boolean) => {
			if (on) classes.add(cls);
			else classes.delete(cls);
			return el;
		}),
		setAttribute: jest.fn((k: string, v: string) => {
			attrs[k] = v;
		}),
		appendChild: jest.fn((child: MockDomElement) => {
			children.push(child);
			return child;
		}),
		createDiv: jest.fn((o?: any) => {
			const c = createDomElement('div', o ?? {}, childDefaults);
			children.push(c);
			return c;
		}),
		createSpan: jest.fn((o?: any) => {
			const c = createDomElement('span', o ?? {}, childDefaults);
			children.push(c);
			return c;
		}),
		createEl: jest.fn((t: string, o?: any) => {
			const c = createDomElement(t, o ?? {}, childDefaults);
			children.push(c);
			return c;
		}),
		querySelector: jest.fn((sel: string) => {
			const cls = sel.replace(/^\./, '');
			return children.find((c) => c.opts?.cls === cls || c.classes.has(cls));
		}),
	});
	return el;
}

/** A DOM mock element with a non-zero default box (400x300) inherited by its children. */
export function createSizedDomElement(tag = 'div', opts: any = {}): MockDomElement {
	return createDomElement(tag, opts, { clientWidth: 400, clientHeight: 300 });
}
