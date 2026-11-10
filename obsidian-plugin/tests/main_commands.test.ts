import './test-setup';
import { App, Notice, TFile, setIcon, setTooltip } from 'obsidian';
import AretePlugin from '@/main';
import { LocalGraphView } from '@presentation/views/LocalGraphView';

jest.mock('@infrastructure/arete/SyncService');
jest.mock('@infrastructure/arete/CheckService');
jest.mock('@application/services/StatsService', () => ({
	StatsService: jest.fn().mockImplementation(() => ({
		refreshStats: jest.fn().mockResolvedValue([]),
		getCache: jest.fn().mockReturnValue({ concepts: {} }),
		settings: {},
	})),
}));

type Cmd = { id: string; callback?: () => any; editorCallback?: (e: any, v: any) => any };

describe('AretePlugin commands, views and status bar', () => {
	let plugin: AretePlugin;
	let app: any;
	let commands: Record<string, Cmd>;
	let protocolHandlers: Record<string, (p: any) => void>;

	beforeEach(async () => {
		(Notice as unknown as jest.Mock).mockClear();
		app = new App();
		plugin = new AretePlugin(app, { dir: 'test-plugin-dir' } as any);
		plugin.loadData = jest.fn().mockResolvedValue({});
		commands = {};
		protocolHandlers = {};
		plugin.addCommand = jest.fn().mockImplementation((c: Cmd) => {
			commands[c.id] = c;
			return c;
		}) as any;
		plugin.registerObsidianProtocolHandler = jest
			.fn()
			.mockImplementation((action: string, h: (p: any) => void) => {
				protocolHandlers[action] = h;
			}) as any;
		await plugin.onload();
		// runSync() persists last_sync_time; keep the test off the real settings writer.
		plugin.saveData = jest.fn().mockResolvedValue(undefined);
	});

	const notices = () => (Notice as unknown as jest.Mock).mock.calls.map((c) => c[0]);

	describe('sync commands', () => {
		test('Sync Current File syncs the absolute path of the active note, forced', () => {
			app.vault.adapter.getBasePath.mockReturnValue('/vault');
			app.workspace.getActiveFile.mockReturnValue({ path: 'notes/A.md' });
			const spy = jest.spyOn(plugin, 'runSync').mockResolvedValue(undefined);

			commands['arete-sync-current-file'].callback!();

			expect(spy).toHaveBeenCalledWith(false, '/vault/notes/A.md', true);
		});

		test('Sync Current File tells the user when there is no active note', () => {
			app.workspace.getActiveFile.mockReturnValue(null);
			const spy = jest.spyOn(plugin, 'runSync').mockResolvedValue(undefined);

			commands['arete-sync-current-file'].callback!();

			expect(spy).not.toHaveBeenCalled();
			expect(notices()).toContain('No active file to sync.');
		});

		test('Sync Current File tells the user when the vault path is unknown', () => {
			app.vault.adapter.getBasePath.mockReturnValue(null);
			app.workspace.getActiveFile.mockReturnValue({ path: 'A.md' });
			const spy = jest.spyOn(plugin, 'runSync').mockResolvedValue(undefined);

			commands['arete-sync-current-file'].callback!();

			expect(spy).not.toHaveBeenCalled();
			expect(notices()).toContain('Cannot resolve the file path.');
		});

		test('Force Re-upload All syncs the whole vault with force set', () => {
			const spy = jest.spyOn(plugin, 'runSync').mockResolvedValue(undefined);
			commands['arete-sync-force-all'].callback!();
			expect(spy).toHaveBeenCalledWith(false, null, true);
		});

		test('Check Current File checks the absolute path of the note in the editor', () => {
			app.vault.adapter.getBasePath.mockReturnValue('/vault');
			const spy = jest.spyOn(plugin, 'runCheck').mockResolvedValue(undefined);

			commands['arete-check-file'].editorCallback!({}, { file: { path: 'B.md' } });

			expect(spy).toHaveBeenCalledWith('/vault/B.md');
		});

		test('Check Current File reports an unknown vault path', () => {
			app.vault.adapter.getBasePath.mockReturnValue(null);
			const spy = jest.spyOn(plugin, 'runCheck').mockResolvedValue(undefined);

			commands['arete-check-file'].editorCallback!({}, { file: { path: 'B.md' } });

			expect(spy).not.toHaveBeenCalled();
			expect(notices()).toContain('Cannot determine the vault path.');
		});
	});

	describe('runSync / runCheck', () => {
		test('runSync forwards its arguments to SyncService and stamps last_sync_time', async () => {
			(plugin.syncService.runSync as jest.Mock).mockResolvedValue(undefined);
			jest.spyOn(Date, 'now').mockReturnValue(1_700_000_000_000);

			await plugin.runSync(true, '/v/x.md', true);

			expect(plugin.syncService.runSync).toHaveBeenCalledWith(
				true,
				'/v/x.md',
				true,
				expect.any(Function),
			);
			expect(plugin.settings.last_sync_time).toBe(1_700_000_000_000);
			expect(plugin.saveData).toHaveBeenCalledWith(
				expect.objectContaining({ last_sync_time: 1_700_000_000_000 }),
			);
			jest.spyOn(Date, 'now').mockRestore();
		});

		test('runCheck reports a failing check as a notice and opens no modal', async () => {
			(plugin.checkService.getCheckResult as jest.Mock).mockRejectedValue(new Error('boom'));

			await plugin.runCheck('/v/x.md');

			expect(notices()).toEqual(['Checking file...', 'Check failed: boom']);
		});

		test('runFix delegates to CheckService', async () => {
			await plugin.runFix('/v/x.md');
			expect(plugin.checkService.runFix).toHaveBeenCalledWith('/v/x.md');
		});
	});

	describe('Sync Stats command', () => {
		test('tags each concept note in the graph and refreshes an open YAML toolbar', async () => {
			plugin.settings.graph_coloring_enabled = true;
			const file = Object.assign(new TFile(), { path: 'Concept.md' });
			app.vault.getAbstractFileByPath = jest
				.fn()
				.mockImplementation((p: string) => (p === 'Concept.md' ? file : null));
			const concept = { filePath: 'Concept.md' } as any;
			const missing = { filePath: 'Gone.md' } as any;
			(plugin.statsService.refreshStats as jest.Mock).mockResolvedValue([concept, missing]);
			plugin.graphService.updateGraphTags = jest.fn().mockResolvedValue(undefined);
			const renderToolbar = jest.fn();
			app.workspace.getLeavesOfType.mockReturnValue([{ view: { renderToolbar } }]);

			await commands['arete-sync-stats'].callback!();

			expect(plugin.graphService.updateGraphTags).toHaveBeenCalledTimes(1);
			expect(plugin.graphService.updateGraphTags).toHaveBeenCalledWith(file, concept);
			expect(renderToolbar).toHaveBeenCalled();
			expect(notices()).toEqual([
				'Refreshing stats...',
				'Graph tags updated.',
				'Stats refreshed.',
			]);
		});

		test('leaves graph tags alone when colouring is off', async () => {
			plugin.settings.graph_coloring_enabled = false;
			(plugin.statsService.refreshStats as jest.Mock).mockResolvedValue([
				{ filePath: 'Concept.md' },
			]);
			plugin.graphService.updateGraphTags = jest.fn();

			await commands['arete-sync-stats'].callback!();

			expect(plugin.graphService.updateGraphTags).not.toHaveBeenCalled();
		});

		test('Reload Note Types clears the template model cache', () => {
			plugin.templateRenderer.clearModelCache = jest.fn();
			commands['arete-reload-note-types'].callback!();
			expect(plugin.templateRenderer.clearModelCache).toHaveBeenCalled();
			expect(notices().join(' ')).toContain('Note types will be fetched');
		});

		test('Clear Retention Tags delegates to GraphService', async () => {
			plugin.graphService.clearAllTags = jest.fn().mockResolvedValue(undefined);
			await commands['arete-graph-clear'].callback!();
			expect(plugin.graphService.clearAllTags).toHaveBeenCalled();
		});
	});

	describe('obsidian://arete handler and showCard', () => {
		const file = { path: 'Cards.md', basename: 'Cards' };
		let openFile: jest.Mock;

		beforeEach(() => {
			openFile = jest.fn().mockResolvedValue(undefined);
			app.vault.getFileByPath = jest
				.fn()
				.mockImplementation((p: string) => (p === 'Cards.md' ? file : null));
			app.workspace.getLeaf = jest.fn().mockReturnValue({ openFile });
			jest.spyOn(plugin, 'activateYamlEditorView').mockResolvedValue(undefined);
		});

		test('the protocol handler passes file and card to showCard', () => {
			const spy = jest.spyOn(plugin, 'showCard').mockResolvedValue(undefined);
			protocolHandlers['arete']({ file: 'Cards', card: '2' });
			expect(spy).toHaveBeenCalledWith('Cards', '2');
		});

		test('appends .md, opens the note and focuses the card by id', async () => {
			app.metadataCache.getFileCache.mockReturnValue({
				frontmatter: { cards: [{ id: 'arete_A' }, { id: 'arete_B' }] },
			});

			await plugin.showCard('Cards', 'arete_B');

			expect(openFile).toHaveBeenCalledWith(file);
			expect(plugin.activateYamlEditorView).toHaveBeenCalledWith(1);
		});

		test('a 1-based position selects that card', async () => {
			app.metadataCache.getFileCache.mockReturnValue({
				frontmatter: { cards: [{ id: 'a' }, { id: 'b' }] },
			});
			await plugin.showCard('Cards.md', '1');
			expect(plugin.activateYamlEditorView).toHaveBeenCalledWith(0);
		});

		test('an unknown note opens nothing and says so', async () => {
			await plugin.showCard('Nope', '1');
			expect(openFile).not.toHaveBeenCalled();
			expect(notices()).toEqual(['No note found at Nope.']);
		});

		test('a missing file argument is reported, not thrown', async () => {
			await plugin.showCard(undefined, undefined);
			expect(notices()).toEqual(['No note found at undefined.']);
		});

		test('an unknown card id leaves the editor closed and says so', async () => {
			app.metadataCache.getFileCache.mockReturnValue({
				frontmatter: { cards: [{ id: 'a' }] },
			});
			await plugin.showCard('Cards', 'zzz');
			expect(plugin.activateYamlEditorView).not.toHaveBeenCalled();
			expect(notices()).toEqual(['Cards has no card zzz.']);
		});

		test('waits for the metadata cache to parse a note written a moment ago', async () => {
			let parsed = false;
			app.metadataCache.getFileCache.mockImplementation(() =>
				parsed ? { frontmatter: { cards: [{ id: 'a' }] } } : null,
			);
			let onChanged: (f: any) => void = () => {};
			app.metadataCache.on = jest.fn().mockImplementation((_n: string, cb: any) => {
				onChanged = cb;
				return 'ref';
			});
			app.metadataCache.offref = jest.fn();

			const pending = plugin.showCard('Cards', '1');
			await Promise.resolve();
			await Promise.resolve();
			await Promise.resolve();
			expect(plugin.activateYamlEditorView).not.toHaveBeenCalled();

			onChanged({ path: 'other.md' });
			await Promise.resolve();
			expect(plugin.activateYamlEditorView).not.toHaveBeenCalled();

			parsed = true;
			onChanged({ path: 'Cards.md' });
			await pending;

			expect(app.metadataCache.offref).toHaveBeenCalledWith('ref');
			expect(plugin.activateYamlEditorView).toHaveBeenCalledWith(0);
		});

		test('gives up waiting for the cache after the timeout', async () => {
			jest.useFakeTimers();
			try {
				app.metadataCache.getFileCache.mockReturnValue(null);
				app.metadataCache.on = jest.fn().mockReturnValue('ref');
				app.metadataCache.offref = jest.fn();

				const pending = plugin.showCard('Cards', '1');
				await jest.advanceTimersByTimeAsync(2000);
				await pending;

				expect(app.metadataCache.offref).toHaveBeenCalledWith('ref');
				expect(notices()).toEqual(['Cards has no card 1.']);
			} finally {
				jest.useRealTimers();
			}
		});
	});

	describe('view activation', () => {
		function sidebarLeaf(type: string, existing?: any) {
			const created: any = existing ?? { view: {}, setViewState: jest.fn() };
			let open = !!existing;
			app.workspace.getLeavesOfType = jest
				.fn()
				.mockImplementation((t: string) => (t === type && open ? [created] : []));
			const rightLeaf = {
				setViewState: jest.fn().mockImplementation(async () => {
					open = true;
				}),
			};
			app.workspace.getRightLeaf = jest.fn().mockReturnValue(rightLeaf);
			app.workspace.revealLeaf = jest.fn();
			return { created, rightLeaf };
		}

		test('dashboard opens in the right sidebar when absent, then reveals it', async () => {
			const { created, rightLeaf } = sidebarLeaf('arete-stats-view');

			await plugin.activateDashboardView();

			expect(rightLeaf.setViewState).toHaveBeenCalledWith({
				type: 'arete-stats-view',
				active: true,
			});
			expect(app.workspace.revealLeaf).toHaveBeenCalledWith(created);
		});

		test('dashboard reuses an open leaf instead of creating another', async () => {
			const { rightLeaf } = sidebarLeaf('arete-stats-view', { view: {} });
			await plugin.activateDashboardView();
			expect(rightLeaf.setViewState).not.toHaveBeenCalled();
			expect(app.workspace.revealLeaf).toHaveBeenCalledTimes(1);
		});

		test('YAML editor view focuses the requested card index on an open view', async () => {
			const showCardIndex = jest.fn().mockResolvedValue(undefined);
			sidebarLeaf('arete-yaml-editor', { view: { showCardIndex } });

			await plugin.activateYamlEditorView(3);

			expect(showCardIndex).toHaveBeenCalledWith(3);
		});

		test('YAML editor view does not move focus when no index is given', async () => {
			const showCardIndex = jest.fn();
			sidebarLeaf('arete-yaml-editor', { view: { showCardIndex } });
			await plugin.activateYamlEditorView();
			expect(showCardIndex).not.toHaveBeenCalled();
		});

		test('syncYamlEditorToCard forwards the index to an open editor and ignores a closed one', () => {
			const focusCard = jest.fn();
			app.workspace.getLeavesOfType = jest.fn().mockReturnValue([{ view: { focusCard } }]);
			plugin.syncYamlEditorToCard(2);
			expect(focusCard).toHaveBeenCalledWith(2);

			app.workspace.getLeavesOfType = jest.fn().mockReturnValue([]);
			expect(() => plugin.syncYamlEditorToCard(2)).not.toThrow();
		});

		test('global graph opens in a new tab when absent', async () => {
			const leaf = { setViewState: jest.fn().mockResolvedValue(undefined) };
			app.workspace.getLeavesOfType = jest.fn().mockReturnValue([]);
			app.workspace.getLeaf = jest.fn().mockReturnValue(leaf);
			app.workspace.revealLeaf = jest.fn();

			await plugin.activateGlobalGraphView();

			expect(app.workspace.getLeaf).toHaveBeenCalledWith('tab');
			expect(leaf.setViewState).toHaveBeenCalledWith({
				type: 'arete-global-graph',
				active: true,
			});
			expect(app.workspace.revealLeaf).toHaveBeenCalledWith(leaf);
		});

		describe('local graph', () => {
			let view: any;

			beforeEach(() => {
				view = Object.create(LocalGraphView.prototype);
				view.setContext = jest.fn();
				sidebarLeaf('arete-local-graph', { view });
				app.workspace.getActiveFile.mockReturnValue({ path: 'N.md' });
			});

			test('an explicit card id sets the context on the graph and remembers it', async () => {
				await plugin.activateLocalGraphView('arete_X');
				expect(view.setContext).toHaveBeenCalledWith('N.md', 'arete_X');
				expect(plugin.getCardContext('N.md')).toBe('arete_X');
			});

			test('without a card id the graph follows the remembered card for the file', async () => {
				plugin.setCardContext('N.md', 'arete_Y');
				await plugin.activateLocalGraphView();
				expect(view.setContext).toHaveBeenCalledWith('N.md', 'arete_Y');
			});

			test('without an active file the graph context is left alone', async () => {
				app.workspace.getActiveFile.mockReturnValue(null);
				await plugin.activateLocalGraphView('arete_X');
				expect(view.setContext).not.toHaveBeenCalled();
			});
		});
	});

	describe('status bar', () => {
		function bar() {
			const el: any = {
				empty: jest.fn(),
				setText: jest.fn(),
				createSpan: jest.fn().mockReturnValue({}),
			};
			plugin.statusBarItem = el;
			(setIcon as jest.Mock).mockClear();
			(setTooltip as jest.Mock).mockClear();
			return el;
		}
		const labels = (el: any) =>
			el.createSpan.mock.calls.map((c: any[]) => c[0]?.text).filter(Boolean);
		const icons = () => (setIcon as jest.Mock).mock.calls.map((c) => c[1]);
		const tooltip = () => [...(setTooltip as jest.Mock).mock.calls].pop()?.[1];

		test('idle shows the age of the last sync', () => {
			const el = bar();
			const now = 1_700_000_000_000;
			jest.spyOn(Date, 'now').mockReturnValue(now);
			try {
				const cases: Array<[number, string]> = [
					[now - 10_000, 'Just now'],
					[now - 5 * 60_000, '5m ago'],
					[now - 3 * 3_600_000, '3h ago'],
					[now - 2 * 86_400_000, '2d ago'],
				];
				for (const [ts, text] of cases) {
					el.createSpan.mockClear();
					plugin.settings.last_sync_time = ts;
					plugin.updateStatusBar('idle');
					expect(labels(el)).toEqual([text]);
					expect([...icons()].pop()).toBe('layers');
				}
				expect(tooltip()).toBe('Click to sync to Anki');
			} finally {
				jest.spyOn(Date, 'now').mockRestore();
			}
		});

		test('idle before any sync says so', () => {
			const el = bar();
			plugin.settings.last_sync_time = null;
			plugin.updateStatusBar('idle');
			expect(labels(el)).toEqual(['Arete']);
			expect(tooltip()).toBe('Never synced. Click to sync.');
		});

		test('success shows a tick, then returns to idle after 3 seconds', () => {
			jest.useFakeTimers();
			try {
				const el = bar();
				plugin.settings.last_sync_time = null;
				plugin.updateStatusBar('success');
				expect(labels(el)).toEqual(['Synced']);
				expect(icons()).toEqual(['check']);
				el.createSpan.mockClear();
				jest.advanceTimersByTime(3000);
				expect(labels(el)).toEqual(['Arete']);
			} finally {
				jest.useRealTimers();
			}
		});

		test('error without a message falls back to a hint', () => {
			const el = bar();
			plugin.updateStatusBar('error');
			expect(labels(el)).toEqual(['Error']);
			expect(icons()).toEqual(['circle-x']);
			expect(tooltip()).toBe('Check logs');
		});

		test('does nothing before the status bar exists', () => {
			plugin.statusBarItem = undefined as any;
			expect(() => plugin.updateStatusBar('syncing')).not.toThrow();
		});
	});

	describe('onunload', () => {
		test('kills Arete processes and clears pending sync-on-save timers', () => {
			jest.useFakeTimers();
			try {
				const fired = jest.fn();
				(plugin as any).syncOnSaveTimers.set('A.md', setTimeout(fired, 1000));
				const dispose = jest.spyOn(plugin.areteClient, 'dispose');

				plugin.onunload();
				jest.advanceTimersByTime(2000);

				expect(fired).not.toHaveBeenCalled();
				expect((plugin as any).syncOnSaveTimers.size).toBe(0);
				expect(dispose).toHaveBeenCalledTimes(1);
				expect(plugin.checkService.dispose).toHaveBeenCalledTimes(1);
				expect(plugin.syncService.dispose).toHaveBeenCalledTimes(1);
			} finally {
				jest.useRealTimers();
			}
		});
	});

	describe('settings persistence', () => {
		test('project_root round-trips through ~ ', async () => {
			const home = require('os').homedir();
			plugin.loadData = jest
				.fn()
				.mockResolvedValue({ project_root: '~/arete', removed_key: 1, debug_mode: true });

			await plugin.loadSettings();

			expect(plugin.settings.project_root).toBe(`${home}/arete`);
			expect(plugin.settings.debug_mode).toBe(true);
			expect((plugin.settings as any).removed_key).toBeUndefined();

			await plugin.saveSettings();
			const saved = (plugin.saveData as jest.Mock).mock.calls.pop()![0];
			expect(saved.project_root).toBe('~/arete');
			expect(saved.removed_key).toBeUndefined();
		});

		test('saveStats stores the stats cache beside the settings', async () => {
			const cache = { concepts: { 'A.md': {} } };
			(plugin.statsService.getCache as jest.Mock).mockReturnValue(cache);

			await plugin.saveStats();

			expect((plugin.saveData as jest.Mock).mock.calls.pop()![0].statsCache).toBe(cache);
		});
	});
});
