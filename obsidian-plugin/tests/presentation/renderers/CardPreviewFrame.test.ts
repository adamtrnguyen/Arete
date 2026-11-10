import '../../test-setup';
import { Component } from 'obsidian';
import { renderCardPreviewFrame } from '@/presentation/renderers/CardPreviewFrame';

/** The parts of a DOM the frame code touches, as plain objects. */
function makeDom(opts: { mathStyles?: any[] } = {}) {
	const created: any[] = [];
	const frameListeners: Record<string, any> = {};
	const docListeners: Record<string, (e: any) => void> = {};
	const observers: any[] = [];
	const open = jest.fn();

	const el = (tag: string): any => ({
		tag,
		attrs: {} as Record<string, string>,
		textContent: '',
		setAttribute(k: string, v: string) {
			this.attrs[k] = v;
		},
		getAttribute(k: string) {
			return this.attrs[k] ?? null;
		},
	});

	const images: any[] = [];
	const head = { children: [] as any[], appendChild: (c: any) => head.children.push(c) };
	const body: any = { innerHTML: '' };
	const root = { getBoundingClientRect: jest.fn(() => ({ height: 120.2 })) };
	const innerDoc: any = {
		head,
		body,
		images,
		documentElement: root,
		fonts: { ready: Promise.resolve() },
		createElement: (t: string) => el(t),
		addEventListener: (n: string, cb: any) => (docListeners[n] = cb),
		removeEventListener: jest.fn(),
		getElementById: jest.fn(),
	};

	const frame: any = {
		...el('iframe'),
		style: {} as Record<string, string>,
		contentDocument: innerDoc,
		addEventListener: (n: string, cb: any) => (frameListeners[n] = cb),
		removeEventListener: jest.fn(),
		remove: jest.fn(),
	};
	frame.setAttribute = (k: string, v: string) => (frame.attrs[k] = v);

	const hostDocument: any = {
		baseURI: 'app://obsidian.md/index.html',
		createElement: jest.fn(() => frame),
		querySelectorAll: jest.fn(() => opts.mathStyles ?? []),
		defaultView: { open },
	};
	class FakeResizeObserver {
		cb: () => void;
		observe = jest.fn();
		disconnect = jest.fn();
		constructor(cb: () => void) {
			this.cb = cb;
			observers.push(this);
		}
	}
	(globalThis as any).ResizeObserver = FakeResizeObserver;

	const container: any = {
		ownerDocument: hostDocument,
		appendChild: jest.fn(),
	};
	return {
		container,
		frame,
		innerDoc,
		hostDocument,
		frameListeners,
		docListeners,
		observers,
		open,
		created,
		images,
		head,
		body,
		root,
	};
}

describe('renderCardPreviewFrame', () => {
	let app: any;
	let component: Component;

	beforeEach(() => {
		app = {
			metadataCache: { getFirstLinkpathDest: jest.fn() },
			vault: { getResourcePath: jest.fn((f: { path: string }) => `app://res/${f.path}`) },
			workspace: { openLinkText: jest.fn() },
		};
		component = new Component();
	});

	const render = (dom: ReturnType<typeof makeDom>, html = '<p>hi</p>', css = '.card{x:1}') =>
		renderCardPreviewFrame(app, dom.container, { html, css }, 'Note.md', component);

	it('adds a sandboxed iframe to the container and returns it', () => {
		const dom = makeDom();
		const frame: any = render(dom);

		expect(frame).toBe(dom.frame);
		expect(dom.container.appendChild).toHaveBeenCalledWith(dom.frame);
		expect(dom.hostDocument.createElement).toHaveBeenCalledWith('iframe');
		expect(frame.className).toBe('arete-card-frame');
		expect(frame.title).toBe('Anki card preview');
		// scripts in card templates must not run
		expect(frame.attrs['sandbox']).toBe('allow-same-origin');
		expect(frame.srcdoc).toContain('<body class="card">');
	});

	describe('once the frame has loaded', () => {
		it('writes the card html and css into the frame, with a base URL', () => {
			const dom = makeDom();
			render(dom, '<b>front</b>', '.card{color:red}');

			dom.frameListeners['load']();

			expect(dom.body.innerHTML).toBe('<b>front</b>');
			const base = dom.head.children.find((c) => c.tag === 'base');
			expect(base.href).toBe('app://obsidian.md/index.html');
			const style = dom.head.children.filter((c) => c.tag === 'style').pop();
			expect(style.textContent).toBe('.card{color:red}');
		});

		it('copies MathJax rules from the CSSOM, or the text when there is no sheet', () => {
			const dom = makeDom({
				mathStyles: [
					{ sheet: { cssRules: [{ cssText: '.a{x:1}' }, { cssText: '.b{y:2}' }] } },
					{ sheet: null, textContent: '.plain{z:3}' },
				],
			});
			render(dom, '', '.css{}');

			dom.frameListeners['load']();

			const styles = dom.head.children
				.filter((c) => c.tag === 'style')
				.map((c) => c.textContent);
			expect(styles).toEqual(['.a{x:1}\n.b{y:2}', '.plain{z:3}', '.css{}']);
			expect(dom.hostDocument.querySelectorAll).toHaveBeenCalledWith('style[id^="MJX-"]');
		});

		it('resolves relative image links to vault resources, decoding the name', () => {
			const dom = makeDom();
			const file = { path: 'media/my pic.png' };
			app.metadataCache.getFirstLinkpathDest.mockImplementation((link: string) =>
				link === 'my pic.png' ? file : null,
			);
			const img = { src: '', getAttribute: () => 'my%20pic.png' };
			dom.images.push(img);
			render(dom);

			dom.frameListeners['load']();

			expect(app.metadataCache.getFirstLinkpathDest).toHaveBeenCalledWith(
				'my pic.png',
				'Note.md',
			);
			expect(img.src).toBe('app://res/media/my pic.png');
		});

		it('leaves absolute, data, protocol-relative, anchor and empty image sources alone', () => {
			const dom = makeDom();
			const srcs = [
				'https://x.org/a.png',
				'data:image/png;base64,AAAA',
				'//cdn/a.png',
				'#frag',
				'app://obsidian.md/a.png',
				'',
			];
			const imgs = srcs.map((s) => ({ src: s, getAttribute: () => s }));
			dom.images.push(...imgs);
			render(dom);

			dom.frameListeners['load']();

			expect(app.metadataCache.getFirstLinkpathDest).not.toHaveBeenCalled();
			expect(imgs.map((i) => i.src)).toEqual(srcs);
		});

		it('keeps an unresolved image source and tolerates a literal percent in the name', () => {
			const dom = makeDom();
			const img = { src: '100%.png', getAttribute: () => '100%.png' };
			dom.images.push(img);
			app.metadataCache.getFirstLinkpathDest.mockReturnValue(null);
			render(dom);

			dom.frameListeners['load']();

			expect(app.metadataCache.getFirstLinkpathDest).toHaveBeenCalledWith(
				'100%.png',
				'Note.md',
			);
			expect(img.src).toBe('100%.png');
		});

		it('sizes the frame to the rendered document, rounded up, and again on resize', () => {
			const dom = makeDom();
			const frame: any = render(dom);

			dom.frameListeners['load']();
			expect(frame.style.height).toBe('121px');
			expect(dom.observers[0].observe).toHaveBeenCalledWith(dom.root);

			dom.root.getBoundingClientRect.mockReturnValue({ height: 300 });
			dom.observers[0].cb();
			expect(frame.style.height).toBe('300px');
		});

		it('does nothing when the frame has no document', () => {
			const dom = makeDom();
			dom.frame.contentDocument = null;
			render(dom);
			expect(() => dom.frameListeners['load']()).not.toThrow();
			expect(dom.observers).toHaveLength(0);
		});
	});

	describe('link clicks inside the frame', () => {
		const click = (dom: ReturnType<typeof makeDom>, anchor: any, extra: any = {}) => {
			const event = {
				target: { closest: () => anchor },
				preventDefault: jest.fn(),
				metaKey: false,
				ctrlKey: false,
				...extra,
			};
			dom.docListeners['click'](event);
			return event;
		};
		const anchor = (o: any) => ({
			classList: { contains: (c: string) => (o.classes ?? []).includes(c) },
			getAttribute: (k: string) => o.attrs?.[k] ?? null,
			href: o.href ?? '',
			hash: o.hash ?? '',
		});

		let dom: ReturnType<typeof makeDom>;
		beforeEach(() => {
			dom = makeDom();
			render(dom);
			dom.frameListeners['load']();
		});

		it('an internal link opens the note, in a new pane with Cmd or Ctrl', () => {
			const a = anchor({ classes: ['internal-link'], attrs: { 'data-href': 'Other Note' } });

			const e1 = click(dom, a);
			expect(e1.preventDefault).toHaveBeenCalled();
			expect(app.workspace.openLinkText).toHaveBeenLastCalledWith(
				'Other Note',
				'Note.md',
				false,
			);

			click(dom, a, { metaKey: true });
			expect(app.workspace.openLinkText).toHaveBeenLastCalledWith(
				'Other Note',
				'Note.md',
				true,
			);
			click(dom, a, { ctrlKey: true });
			expect(app.workspace.openLinkText).toHaveBeenLastCalledWith(
				'Other Note',
				'Note.md',
				true,
			);
		});

		it('an internal link falls back to href when data-href is missing', () => {
			click(dom, anchor({ classes: ['internal-link'], attrs: { href: 'Fallback' } }));
			expect(app.workspace.openLinkText).toHaveBeenCalledWith('Fallback', 'Note.md', false);
		});

		it('an in-page anchor scrolls within the frame', () => {
			const target = { scrollIntoView: jest.fn() };
			dom.innerDoc.getElementById.mockReturnValue(target);

			click(dom, anchor({ attrs: { href: '#sec' }, hash: '#sec' }));

			expect(dom.innerDoc.getElementById).toHaveBeenCalledWith('sec');
			expect(target.scrollIntoView).toHaveBeenCalled();
			expect(app.workspace.openLinkText).not.toHaveBeenCalled();
		});

		it('an external link opens in a new window without opener access', () => {
			const e = click(
				dom,
				anchor({ attrs: { href: 'https://x.org' }, href: 'https://x.org/' }),
			);
			expect(e.preventDefault).toHaveBeenCalled();
			expect(dom.open).toHaveBeenCalledWith('https://x.org/', '_blank', 'noopener');
		});

		it('a click that is not on a link is left alone', () => {
			const e = click(dom, null);
			expect(e.preventDefault).not.toHaveBeenCalled();
			expect(dom.open).not.toHaveBeenCalled();
			expect(app.workspace.openLinkText).not.toHaveBeenCalled();
		});
	});

	describe('disposal', () => {
		it('removes the frame and listeners, and stops observing, when the component unloads', () => {
			const dom = makeDom();
			render(dom);
			dom.frameListeners['load']();
			const observer = dom.observers[0];

			component.unload();

			expect(dom.frame.remove).toHaveBeenCalled();
			expect(dom.frame.removeEventListener).toHaveBeenCalledWith(
				'load',
				expect.any(Function),
			);
			expect(observer.disconnect).toHaveBeenCalled();
			expect(dom.innerDoc.removeEventListener).toHaveBeenCalledWith(
				'click',
				dom.docListeners['click'],
			);
		});

		it('a load that arrives after disposal changes nothing', () => {
			const dom = makeDom();
			render(dom, '<p>late</p>');

			component.unload();
			dom.frameListeners['load']();

			expect(dom.body.innerHTML).toBe('');
			expect(dom.observers).toHaveLength(0);
		});

		it('a resize after disposal does not touch the frame', () => {
			const dom = makeDom();
			const frame: any = render(dom);
			dom.frameListeners['load']();
			component.unload();
			frame.style.height = 'unchanged';

			dom.observers[0].cb();

			expect(frame.style.height).toBe('unchanged');
		});
	});
});
