import { EventEmitter } from 'events';

// 1. Mock child_process and fs globally

jest.mock('child_process', () => ({
	spawn: jest.fn(),
	exec: jest.fn(),
}));

jest.mock('fs', () => ({
	appendFileSync: jest.fn(),
	readFileSync: jest.fn(),
	writeFileSync: jest.fn(),
	existsSync: jest.fn(),
}));

// Mock CodeMirror packages to avoid DOM-dependency crashes
jest.mock('@codemirror/view', () => ({
	EditorView: class {
		dispatch() {}
		destroy() {}
	},
	lineNumbers: jest.fn(),
	keymap: { of: jest.fn() },
	Decoration: {
		line: jest.fn().mockReturnValue({}),
		set: jest.fn().mockReturnValue({}),
	},
	GutterMarker: class {},
	gutter: jest.fn(),
}));
jest.mock('@codemirror/state', () => ({
	EditorState: {
		create: jest.fn().mockReturnValue({}),
		transaction: jest.fn(),
	},
	Annotation: { define: jest.fn() },
	Facet: { define: jest.fn() },
	StateEffect: {
		define: jest.fn().mockReturnValue({}),
	},
	StateField: {
		define: jest.fn().mockReturnValue({}),
	},
}));
jest.mock('@codemirror/commands', () => ({
	defaultKeymap: [] as any[],
	history: jest.fn(),
	historyKeymap: [] as any[],
}));
jest.mock('@codemirror/lang-yaml', () => ({
	yaml: jest.fn(),
}));

// Mock Document and Window for TemplateRenderer
if (typeof document === 'undefined') {
	const mockDoc = {
		createElement: jest.fn().mockImplementation(() => ({
			style: { tabSize: '4' },
			appendChild: jest.fn(),
			removeChild: jest.fn(),
			setAttribute: jest.fn(),
			styleSheets: [],
			ownerDocument: null,
			innerHTML: '',
		})),
		createTextNode: jest.fn().mockReturnValue({}),
		body: {
			appendChild: jest.fn(),
			removeChild: jest.fn(),
		},
		documentElement: {
			style: { tabSize: '4' },
		},
		head: {
			appendChild: jest.fn(),
		},
		addEventListener: jest.fn(),
		removeEventListener: jest.fn(),
	};
	(global as any).document = mockDoc;
	(global as any).window = {
		document: mockDoc,
		navigator: { userAgent: 'node' },
		addEventListener: jest.fn(),
		removeEventListener: jest.fn(),
		requestAnimationFrame: jest.fn().mockImplementation((cb) => setTimeout(cb, 0)),
		cancelAnimationFrame: jest.fn().mockImplementation((id) => clearTimeout(id)),
		history: {},
		performance: { now: jest.fn().mockReturnValue(0) },
		location: {},
		screen: {},
		getComputedStyle: jest.fn().mockReturnValue({ tabSize: '4' }),
	};
	(global as any).navigator = (global as any).window.navigator;
	(global as any).Node = class {};
	(global as any).HTMLElement = class {};
	(global as any).MutationObserver = class {
		observe() {}
		disconnect() {}
	};
}

// Mock factories that return fresh instances
export const createMockElement = (tag?: string, opts?: any): any => {
	const el: any = {
		tag,
		addClass: jest.fn().mockReturnThis(),
		removeClass: jest.fn().mockReturnThis(),
		empty: jest.fn().mockReturnThis(),
		setText: jest.fn().mockReturnThis(),
		setButtonText: jest.fn().mockReturnThis(),
		setDisabled: jest.fn().mockReturnThis(),
		addEventListener: jest.fn().mockImplementation(function (this: any, name, cb) {
			this._listeners = this._listeners || {};
			this._listeners[name] = cb;
			return this;
		}),
		createDiv: jest.fn().mockImplementation((o) => createMockElement('div', o)),
		createEl: jest.fn().mockImplementation((t, o) => createMockElement(t, o)),
		createSpan: jest.fn().mockImplementation((o) => createMockElement('span', o)),
		appendChild: jest.fn().mockReturnThis(),
		removeChild: jest.fn().mockReturnThis(),
		...opts,
	};
	return el;
};

export const createMockSetting = (): any => {
	const mock: any = {
		setName: jest.fn().mockReturnThis(),
		setDesc: jest.fn().mockReturnThis(),
		addText: jest.fn().mockImplementation((cb: any) => {
			const mockText: any = {
				setPlaceholder: jest.fn().mockReturnThis(),
				setValue: jest.fn().mockReturnThis(),
				onChange: jest.fn().mockImplementation((ocb: any) => {
					mockText._onChange = ocb;
					return mockText;
				}),
			};
			cb(mockText);
			mock.mockText = mockText;
			return mock;
		}),
		addToggle: jest.fn().mockImplementation((cb: any) => {
			const mockToggle: any = {
				setValue: jest.fn().mockReturnThis(),
				onChange: jest.fn().mockImplementation((ocb: any) => {
					mockToggle._onChange = ocb;
					return mockToggle;
				}),
			};
			cb(mockToggle);
			mock.mockToggle = mockToggle;
			return mock;
		}),
		addDropdown: jest.fn().mockImplementation((cb: any) => {
			const mockDropdown: any = {
				addOption: jest.fn().mockReturnThis(),
				setValue: jest.fn().mockReturnThis(),
				onChange: jest.fn().mockImplementation((ocb: any) => {
					mockDropdown._onChange = ocb;
					return mockDropdown;
				}),
			};
			cb(mockDropdown);
			mock.mockDropdown = mockDropdown;
			return mock;
		}),
		addSlider: jest.fn().mockImplementation((cb: any) => {
			const mockSlider: any = {
				setLimits: jest.fn().mockReturnThis(),
				setValue: jest.fn().mockReturnThis(),
				setDynamicTooltip: jest.fn().mockReturnThis(),
				onChange: jest.fn().mockImplementation((ocb: any) => {
					mockSlider._onChange = ocb;
					return mockSlider;
				}),
			};
			cb(mockSlider);
			mock.mockSlider = mockSlider;
			return mock;
		}),
		addButton: jest.fn().mockImplementation((cb: any) => {
			const mockButton: any = {
				setButtonText: jest.fn().mockReturnThis(),
				onClick: jest.fn().mockImplementation((ocb: any) => {
					mockButton._onClick = ocb;
					return mockButton;
				}),
			};
			cb(mockButton);
			mock.mockButton = mockButton;
			return mock;
		}),
	};
	return mock;
};

// Make factories available to jest.mock
(global as any).mockCreateMockElement = createMockElement;
(global as any).mockCreateMockSetting = createMockSetting;

jest.mock('obsidian', () => {
	return {
		App: jest.fn().mockImplementation(() => ({
			vault: {
				adapter: { getBasePath: jest.fn() },
				getMarkdownFiles: jest.fn().mockReturnValue([]),
				read: jest.fn(),
				on: jest.fn(),
			},
			workspace: {
				getActiveFile: jest.fn(),
				getActiveViewOfType: jest.fn(),
				on: jest.fn(),
				onLayoutReady: jest.fn().mockImplementation((cb) => cb()),
				getLeavesOfType: jest.fn().mockReturnValue([]),
			},
			metadataCache: {
				getFileCache: jest.fn(),
				getFirstLinkpathDest: jest.fn(),
				on: jest.fn(),
			},
			commands: {
				findCommand: jest.fn(),
				listCommands: jest.fn().mockReturnValue([]),
			},
			setting: {
				openTabById: jest.fn(),
			},
			fileManager: {
				processFrontMatter: jest.fn(),
			},
		})),
		Plugin: class {
			app: any;
			manifest: any;
			constructor(app: any, manifest: any) {
				this.app = app;
				this.manifest = manifest;
			}
			addStatusBarItem() {
				return (global as any).mockCreateMockElement('div');
			}
			addRibbonIcon() {
				return (global as any).mockCreateMockElement('div');
			}
			addCommand(cmd: any) {
				(global as any).registeredCommands = (global as any).registeredCommands || {};
				(global as any).registeredCommands[cmd.id] = cmd;
				return cmd;
			}
			addSettingTab() {
				return {};
			}
			loadData() {
				return Promise.resolve({});
			}
			saveData() {
				return Promise.resolve();
			}
			registerEvent() {
				/* no-op */
			}
			registerView() {
				/* no-op */
			}
			registerEditorExtension() {
				/* no-op */
			}
		},
		PluginSettingTab: class {
			app: any;
			plugin: any;
			containerEl = (global as any).mockCreateMockElement('div');
			constructor(app: any, plugin: any) {
				this.app = app;
				this.plugin = plugin;
			}
		},
		Setting: jest.fn().mockImplementation(() => (global as any).mockCreateMockSetting()),
		Notice: jest.fn(),
		Modal: class {
			app: any;
			contentEl = (global as any).mockCreateMockElement('div');
			modalEl = (global as any).mockCreateMockElement('div');
			constructor(app: any) {
				this.app = app;
			}
			open() {
				/* no-op */
			}
			close() {
				/* no-op */
			}
		},
		FileSystemAdapter: class {},
		setIcon: jest.fn(),
		MarkdownView: class {},
		Editor: class {},
		TFile: class {},
		ItemView: class {
			contentEl = (global as any).mockCreateMockElement('div');
			constructor(leaf: any) {
				/* no-op */
			}
		},
		MarkdownRenderer: {
			render: jest.fn(),
		},
		FuzzySuggestModal: class {
			app: any;
			constructor(app: any) {
				this.app = app;
			}
			open() {
				/* no-op */
			}
			close() {
				/* no-op */
			}
			getItems(): any[] {
				return [];
			}
			getItemText(item: any): string {
				return '';
			}
			onChooseItem(item: any, evt: any) {
				/* no-op */
			}
		},
		WorkspaceLeaf: class {},
		requestUrl: jest.fn(),
	};
});

export const createMockChildProcess = () => {
	const child: any = new EventEmitter();
	child.stdout = new EventEmitter();
	child.stderr = new EventEmitter();
	child.unref = jest.fn();
	child.kill = jest.fn();
	return child;
};
