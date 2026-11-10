import '../../test-setup';
import { App, setIcon } from 'obsidian';
import O2APlugin from '@/main';
import { CheckResultModal } from '@presentation/modals/CheckResultModal';

describe('CheckResultModal Deep Coverage', () => {
	let plugin: O2APlugin;
	let app: App;
	let modal: CheckResultModal;

	beforeEach(() => {
		jest.clearAllMocks();
		app = new App();
		plugin = {
			runFix: jest.fn().mockResolvedValue(undefined),
			runCheck: jest.fn().mockResolvedValue(undefined),
		} as any;
	});

	test('Auto-fix is a call-to-action ButtonComponent that fixes then re-checks', async () => {
		const { ButtonComponent } = jest.requireMock('obsidian');
		ButtonComponent.instances.length = 0;
		const result = {
			ok: false,
			errors: [{ line: 1, message: 'Tab Character Error' }],
		};
		modal = new CheckResultModal(app, plugin, result, '/path/to/file.md');

		modal.onOpen();

		expect(modal.contentEl.createEl).not.toHaveBeenCalledWith('button', expect.anything());
		expect(ButtonComponent.instances).toHaveLength(1);
		const [btn] = ButtonComponent.instances;
		expect(btn.setButtonText).toHaveBeenCalledWith('Auto-fix issues');
		expect(btn.setCta).toHaveBeenCalled();

		await btn.clickHandler();

		expect(btn.setDisabled).toHaveBeenCalledWith(true);
		expect(btn.setButtonText).toHaveBeenCalledWith('Fixing...');
		expect(plugin.runFix).toHaveBeenCalledWith('/path/to/file.md');
		expect(plugin.runCheck).toHaveBeenCalledWith('/path/to/file.md');
	});

	test('the title comes from Modal.setTitle, with no hand-built heading', () => {
		modal = new CheckResultModal(app, plugin, { ok: true, stats: { cards_found: 1 } }, 'a.md');
		modal.onOpen();
		expect(modal.setTitle).toHaveBeenCalledWith('Arete file check');
		for (const call of (modal.contentEl.createEl as jest.Mock).mock.calls) {
			expect(call[0]).not.toMatch(/^h[1-6]$/);
		}
	});

	test('onOpen with ok: true constructs stats UI', () => {
		const result = { ok: true, stats: { deck: 'TestDeck', cards_found: 5 } };
		modal = new CheckResultModal(app, plugin, result, '/path/to/note.md');
		modal.onOpen();
		expect(modal.contentEl.createEl).toHaveBeenCalledWith('ul', expect.any(Object));
	});

	test('the status line is a Lucide icon plus text, with no emoji', () => {
		const spanTexts = (el: any) =>
			(el.createDiv as jest.Mock).mock.results.flatMap((r) =>
				(r.value.createSpan as jest.Mock).mock.calls.map((c) => c[0]?.text),
			);
		(setIcon as jest.Mock).mockClear();

		modal = new CheckResultModal(app, plugin, { ok: true, stats: { cards_found: 1 } }, 'a.md');
		modal.onOpen();
		expect((setIcon as jest.Mock).mock.calls.map((c) => c[1])).toEqual(['check']);
		expect(spanTexts(modal.contentEl)).toContain('Valid');

		(setIcon as jest.Mock).mockClear();
		modal = new CheckResultModal(app, plugin, { ok: false, errors: [] }, 'a.md');
		modal.onOpen();
		expect((setIcon as jest.Mock).mock.calls.map((c) => c[1])).toEqual(['circle-x']);
		expect(spanTexts(modal.contentEl)).toContain('Validation failed');
	});

	test('onClose empties contentEl', () => {
		modal = new CheckResultModal(app, plugin, { ok: true }, '/path/to/note.md');
		modal.onClose();
		expect(modal.contentEl.empty).toHaveBeenCalled();
	});
});
