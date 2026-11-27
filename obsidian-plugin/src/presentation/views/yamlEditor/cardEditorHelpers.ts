import { parseYaml, stringifyYaml } from 'obsidian';
import { isReservedCardKey } from '@/domain/cardKeys';

export type CardData = Record<string, any>;

export enum CardEditorViewMode {
	Source = 'source',
	Fields = 'fields',
	Preview = 'preview',
}

export function cloneValue<T>(value: T): T {
	if (Array.isArray(value)) {
		return value.map((item) => cloneValue(item)) as unknown as T;
	}
	if (value && typeof value === 'object') {
		if (value instanceof Date) {
			return new Date(value.getTime()) as unknown as T;
		}
		const copy: Record<string, unknown> = {};
		for (const [k, v] of Object.entries(value)) {
			copy[k] = cloneValue(v);
		}
		return copy as T;
	}
	return value;
}

/** Fit textareas to their content: every clear, then every read, so layout runs once. */
export function fitTextareas(inputs: HTMLTextAreaElement[]): void {
	for (const input of inputs) input.style.removeProperty?.('height');
	const heights = inputs.map((input) => input.scrollHeight);
	inputs.forEach((input, i) => {
		input.style.height = `${heights[i]}px`;
	});
}

export function serializeCardYaml(card: CardData | undefined | null): string {
	if (!card || typeof card !== 'object') return '# No card selected\n';
	return stringifyYaml(card);
}

export function parseCardYaml(yamlStr: string): CardData | null {
	try {
		const raw = parseYaml(yamlStr);
		if (raw === null || typeof raw !== 'object' || Array.isArray(raw)) {
			return null;
		}
		return raw as CardData;
	} catch (e) {
		console.error('[Arete] Failed to parse card YAML:', e);
		return null;
	}
}

/** Card keys minus structural keys, cast to the string map the renderer wants. */
export function extractCardFields(card: CardData): Record<string, string> {
	const fields: Record<string, string> = {};
	for (const [key, value] of Object.entries(card)) {
		if (isReservedCardKey(key)) continue;
		if (value === null || value === undefined) continue;
		if (typeof value === 'object') continue;
		fields[key] = String(value);
	}
	return fields;
}
