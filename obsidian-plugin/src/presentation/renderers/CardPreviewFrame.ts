import { App, Component, Keymap } from 'obsidian';

/** Hard upper bound on auto-sized iframe height so runaway 100vh templates cannot hang layout. */
const MAX_FRAME_HEIGHT_PX = 8000;
/** Stop observing after this many strictly increasing observer-driven resizes in a row. */
const MAX_CONSECUTIVE_GROWTHS = 8;

/**
 * Base rules prepended before Anki model CSS. `html { height: auto }` breaks `100vh`
 * resize feedback loops, `:where(body.card)` yields to any model margin, and
 * `body.nightMode` (specificity 0,1,1) mirrors Anki's own night-mode override of the
 * default `.card { color: black; background-color: white }` (0,1,0) while still letting
 * model-authored `.card.nightMode`, `.night_mode .card`, or later `body.nightMode` rules win.
 */
const FRAME_BASE_CSS = [
	'html { height: auto; min-height: 0; }',
	':where(body.card) { margin: 8px; }',
	'body.nightMode { color-scheme: dark; color: #f0f0f0; background-color: transparent; }',
].join('\n');

/** Give model CSS its own document; no selector rewriting or Obsidian theme CSS. */
export function renderCardPreviewFrame(
	app: App,
	container: HTMLElement,
	rendered: { html: string; css: string },
	sourcePath: string,
	component: Component,
): HTMLIFrameElement {
	const hostDocument = container.ownerDocument;
	const frame = hostDocument.createElement('iframe');
	frame.className = 'arete-card-frame';
	frame.title = 'Anki card preview';
	// Parent code can populate the document, but card-template scripts cannot run.
	frame.setAttribute('sandbox', 'allow-same-origin');
	frame.srcdoc =
		'<!doctype html><html><head><meta charset="utf-8"></head><body class="card"></body></html>';
	let disposed = false;
	let observer: ResizeObserver | undefined;

	const onLoad = () => {
		if (disposed) return;
		const doc = frame.contentDocument;
		if (!doc) return;

		const base = doc.createElement('base');
		base.href = hostDocument.baseURI;
		doc.head.appendChild(base);

		const syncTheme = () => {
			if (disposed) return;
			const isDark = hostDocument.body?.classList?.contains('theme-dark') ?? false;
			doc.documentElement.classList?.toggle('nightMode', isDark);
			doc.documentElement.classList?.toggle('night_mode', isDark);
			doc.body.classList?.toggle('nightMode', isDark);
			doc.body.classList?.toggle('night_mode', isDark);
		};
		syncTheme();
		if (typeof app.workspace?.on === 'function') {
			const themeRef = app.workspace.on('css-change', syncTheme);
			if (typeof component.registerEvent === 'function') {
				component.registerEvent(themeRef);
			} else {
				component.register(() => app.workspace.offref?.(themeRef));
			}
		}

		// Obsidian's MathJax output uses shared font/glyph rules. Copy only those
		// rules, not the theme styles that would override the Anki model.
		for (const source of Array.from(hostDocument.querySelectorAll('style[id^="MJX-"]'))) {
			const mathStyle = doc.createElement('style');
			// MathJax inserts glyph rules through CSSOM; textContent omits them.
			const rules = (source as HTMLStyleElement).sheet?.cssRules;
			mathStyle.textContent = rules
				? Array.from(rules)
						.map((rule) => rule.cssText)
						.join('\n')
				: source.textContent;
			doc.head.appendChild(mathStyle);
		}
		const baseStyle = doc.createElement('style');
		baseStyle.setAttribute('data-arete-base', 'true');
		baseStyle.textContent = FRAME_BASE_CSS;
		doc.head.appendChild(baseStyle);

		const style = doc.createElement('style');
		style.textContent = rendered.css;
		doc.head.appendChild(style);
		// eslint-disable-next-line @microsoft/sdl/no-inner-html, no-unsanitized/property -- sandboxed iframe (allow-same-origin, scripts disabled) renders Anki card HTML
		doc.body.innerHTML = rendered.html;

		for (const image of Array.from(doc.images)) {
			const src = image.getAttribute('src') || '';
			if (!src || /^(?:[a-z][a-z\d+.-]*:|\/\/|#)/i.test(src)) continue;
			let link = src;
			try {
				link = decodeURIComponent(src);
			} catch {
				/* Literal percent in filename. */
			}
			const file = app.metadataCache.getFirstLinkpathDest(link, sourcePath);
			if (file) image.src = app.vault.getResourcePath(file);
		}

		const onClick = (event: MouseEvent) => {
			const target = event.target as Element | null;
			const anchor = target?.closest('a');
			if (!anchor) return;
			event.preventDefault();
			if (anchor.classList.contains('internal-link')) {
				const link = anchor.getAttribute('data-href') || anchor.getAttribute('href');
				if (link)
					void app.workspace.openLinkText(link, sourcePath, Keymap.isModEvent(event));
			} else if (anchor.getAttribute('href')?.startsWith('#')) {
				doc.getElementById(anchor.hash.slice(1))?.scrollIntoView();
			} else if (anchor.href) {
				try {
					const parsed = new URL(anchor.href);
					if (
						parsed.protocol === 'http:' ||
						parsed.protocol === 'https:' ||
						parsed.protocol === 'mailto:'
					) {
						hostDocument.defaultView?.open(parsed.href, '_blank', 'noopener');
					}
				} catch {
					/* Ignore malformed or unsupported link URLs. */
				}
			}
		};
		doc.addEventListener('click', onClick);
		component.register(() => doc.removeEventListener('click', onClick));

		let lastHeight = 0;
		let growthStreak = 0;
		const applyHeight = (fromObserver: boolean) => {
			if (disposed) return;
			// The root box includes collapsed paragraph margins outside the body.
			const raw = Math.ceil(doc.documentElement.getBoundingClientRect().height);
			const nextHeight = Math.min(raw, MAX_FRAME_HEIGHT_PX);
			if (nextHeight === lastHeight) return;
			if (fromObserver && lastHeight > 0 && nextHeight > lastHeight) {
				growthStreak++;
				if (growthStreak > MAX_CONSECUTIVE_GROWTHS) {
					observer?.disconnect();
					observer = undefined;
					return;
				}
			} else {
				growthStreak = 0;
			}
			lastHeight = nextHeight;
			frame.style.height = `${nextHeight}px`;
		};
		observer = new ResizeObserver(() => applyHeight(true));
		observer.observe(doc.documentElement);
		void doc.fonts.ready.then(() => applyHeight(false));
		applyHeight(false);
	};
	frame.addEventListener('load', onLoad, { once: true });
	component.register(() => {
		disposed = true;
		observer?.disconnect();
		frame.removeEventListener('load', onLoad);
		frame.remove();
	});
	container.appendChild(frame);
	return frame;
}
