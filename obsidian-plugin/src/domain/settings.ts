export interface AretePluginSettings {
	python_path: string;
	debug_mode: boolean;
	backend: 'auto' | 'direct' | 'ankiconnect';
	workers: number;
	anki_connect_url: string;
	anki_media_dir: string;
	renderer_mode: 'obsidian' | 'anki';
	stats_algorithm: 'sm2' | 'fsrs';
	stats_lapse_threshold: number;
	stats_ease_threshold: number; // 2100 = 210%
	stats_difficulty_threshold: number; // For FSRS, e.g. 0.9 (90%)

	// Sync Options
	sync_on_save: boolean;
	sync_on_save_delay: number; // ms debounce

	// UI Persistence
	ui_expanded_decks: string[];
	ui_expanded_concepts: string[];
	last_sync_time: number | null; // Unix timestamp

	// Graph Coloring
	graph_coloring_enabled: boolean;
	graph_tag_prefix: string;

	project_root: string;
}

export const DEFAULT_SETTINGS: AretePluginSettings = {
	python_path: 'python3',
	debug_mode: false,
	backend: 'auto',
	workers: 4,
	anki_connect_url: 'http://localhost:8765',
	anki_media_dir: '',
	renderer_mode: 'obsidian',
	stats_algorithm: 'fsrs', // Default to FSRS
	stats_lapse_threshold: 3,
	stats_ease_threshold: 2100, // 210%
	stats_difficulty_threshold: 0.9, // 90% Max Difficulty

	// Sync Options
	sync_on_save: false,
	sync_on_save_delay: 2000, // 2 second debounce

	ui_expanded_decks: [],
	ui_expanded_concepts: [],
	last_sync_time: null,

	// Graph Coloring
	graph_coloring_enabled: false,
	graph_tag_prefix: 'arete/retention',

	project_root: '',
};

const BACKENDS: ReadonlySet<AretePluginSettings['backend']> = new Set([
	'auto',
	'direct',
	'ankiconnect',
]);
const RENDERER_MODES: ReadonlySet<AretePluginSettings['renderer_mode']> = new Set([
	'obsidian',
	'anki',
]);
const STATS_ALGORITHMS: ReadonlySet<AretePluginSettings['stats_algorithm']> = new Set([
	'sm2',
	'fsrs',
]);

function str(value: unknown, fallback: string): string {
	return typeof value === 'string' ? value : fallback;
}

function bool(value: unknown, fallback: boolean): boolean {
	return typeof value === 'boolean' ? value : fallback;
}

function finiteInRange(
	value: unknown,
	fallback: number,
	min: number,
	max: number,
	integer = false,
): number {
	if (typeof value !== 'number' || !Number.isFinite(value) || value < min || value > max) {
		return fallback;
	}
	return integer ? Math.round(value) : value;
}

function stringList(value: unknown): string[] {
	return Array.isArray(value) ? value.filter((v): v is string => typeof v === 'string') : [];
}

/**
 * Coerce a raw object parsed from `data.json` into valid plugin settings. Unknown keys
 * (from removed features) are dropped and malformed fields fall back to defaults.
 */
export function normalizeSettings(raw: unknown): AretePluginSettings {
	const d = DEFAULT_SETTINGS;
	if (!raw || typeof raw !== 'object' || Array.isArray(raw)) {
		return { ...d, ui_expanded_decks: [], ui_expanded_concepts: [] };
	}
	const o = raw as Record<string, unknown>;
	return {
		python_path: str(o.python_path, d.python_path),
		debug_mode: bool(o.debug_mode, d.debug_mode),
		backend: BACKENDS.has(o.backend as AretePluginSettings['backend'])
			? (o.backend as AretePluginSettings['backend'])
			: d.backend,
		workers: finiteInRange(o.workers, d.workers, 1, 64, true),
		anki_connect_url: str(o.anki_connect_url, d.anki_connect_url),
		anki_media_dir: str(o.anki_media_dir, d.anki_media_dir),
		renderer_mode: RENDERER_MODES.has(o.renderer_mode as AretePluginSettings['renderer_mode'])
			? (o.renderer_mode as AretePluginSettings['renderer_mode'])
			: d.renderer_mode,
		stats_algorithm: STATS_ALGORITHMS.has(
			o.stats_algorithm as AretePluginSettings['stats_algorithm'],
		)
			? (o.stats_algorithm as AretePluginSettings['stats_algorithm'])
			: d.stats_algorithm,
		stats_lapse_threshold: finiteInRange(
			o.stats_lapse_threshold,
			d.stats_lapse_threshold,
			1,
			1000,
			true,
		),
		stats_ease_threshold: finiteInRange(
			o.stats_ease_threshold,
			d.stats_ease_threshold,
			1,
			10_000,
		),
		stats_difficulty_threshold: finiteInRange(
			o.stats_difficulty_threshold,
			d.stats_difficulty_threshold,
			0.01,
			1,
		),
		sync_on_save: bool(o.sync_on_save, d.sync_on_save),
		sync_on_save_delay: finiteInRange(
			o.sync_on_save_delay,
			d.sync_on_save_delay,
			0,
			60_000,
			true,
		),
		ui_expanded_decks: stringList(o.ui_expanded_decks),
		ui_expanded_concepts: stringList(o.ui_expanded_concepts),
		last_sync_time:
			typeof o.last_sync_time === 'number' &&
			Number.isFinite(o.last_sync_time) &&
			o.last_sync_time >= 0
				? o.last_sync_time
				: null,
		graph_coloring_enabled: bool(o.graph_coloring_enabled, d.graph_coloring_enabled),
		graph_tag_prefix: str(o.graph_tag_prefix, d.graph_tag_prefix),
		project_root: str(o.project_root, d.project_root),
	};
}
