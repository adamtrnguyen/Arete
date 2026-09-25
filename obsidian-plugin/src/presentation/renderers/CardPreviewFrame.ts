import { App, Component } from 'obsidian';

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
		const style = doc.createElement('style');
		style.textContent = rendered.css;
		doc.head.appendChild(style);
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
					void app.workspace.openLinkText(
						link,
						sourcePath,
						event.metaKey || event.ctrlKey,
					);
			} else if (anchor.getAttribute('href')?.startsWith('#')) {
				doc.getElementById(anchor.hash.slice(1))?.scrollIntoView();
			} else {
				hostDocument.defaultView?.open(anchor.href, '_blank', 'noopener');
			}
		};
		doc.addEventListener('click', onClick);
		component.register(() => doc.removeEventListener('click', onClick));

		const resize = () => {
			if (disposed) return;
			// The root box includes collapsed paragraph margins outside the body.
			const height = doc.documentElement.getBoundingClientRect().height;
			frame.style.height = `${Math.ceil(height)}px`;
		};
		observer = new ResizeObserver(resize);
		observer.observe(doc.documentElement);
		void doc.fonts.ready.then(resize);
		resize();
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
