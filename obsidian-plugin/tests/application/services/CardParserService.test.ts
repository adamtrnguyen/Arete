import { CardParserService } from '@/application/services/CardParserService';
import { card, noteYaml } from '../../factories';

// Mock Obsidian's parseYaml since it's not available in Node/Jest environment
jest.mock('obsidian', () => ({
	parseYaml: jest.fn((text: string) => {
		// Very basic YAML parser simulation for testing
		const data: any = {};
		const lines = text.split('\n');
		for (const line of lines) {
			const nidMatch = line.match(/['"]?(?:nid|NID)['"]?\s*:\s*['"]?(\d+)/i);
			if (nidMatch) data.nid = nidMatch[1];
			const cidMatch = line.match(/['"]?(?:cid|CID)['"]?\s*:\s*['"]?(\d+)/i);
			if (cidMatch) data.cid = cidMatch[1];
		}
		return data;
	}),
}));

describe('CardParserService', () => {
	const musclesYaml = noteYaml({
		deck: 'Anatomy',
		cards: [
			card({
				Front: '![[Foot Muscles.png]] What is #1?',
				Back: '1. Peroneus Longus',
				nid: 1762277751241,
			}),
			card({
				Front: '![[Foot Muscles.png]] What is #2?',
				Back: '2. Peroneus Brevis',
				nid: 1762277751465,
			}),
		],
		body: '# Muscles of the Foot',
	});

	it('should parse 13-digit NIDs correctly (unquoted)', () => {
		const result = CardParserService.parseCards(musclesYaml);
		expect(result.ranges.length).toBe(2);
		expect(result.ranges[0].nid).toBe(1762277751241);
	});

	it('should parse 13-digit NIDs correctly (quoted)', () => {
		const result = CardParserService.parseCards(musclesYaml);
		expect(result.ranges.length).toBe(2);
		expect(result.ranges[1].nid).toBe(1762277751465);
	});

	it('should identify line ranges correctly', () => {
		const lines = musclesYaml.split('\n');
		const result = CardParserService.parseCards(musclesYaml);

		// Derived from the fixture, not hard-coded, so a change to the shared
		// factory cannot silently move these and break the test.
		expect(lines[result.ranges[0].startLine].trim().startsWith('- ')).toBe(true);
		expect(lines[result.ranges[0].endLine]).toContain('1762277751241');
		expect(result.ranges[0].endLine).toBeLessThan(result.ranges[1].startLine);
		expect(lines[result.ranges[1].startLine].trim().startsWith('- ')).toBe(true);
	});

	it('should find frontmatter end line', () => {
		const lines = musclesYaml.split('\n');
		const result = CardParserService.parseCards(musclesYaml);
		expect(result.frontmatterEndLine).not.toBeNull();
		expect(lines[result.frontmatterEndLine!]).toBe('---');
	});

	it('should handle missing nid/cid gracefully', () => {
		const partialYaml = `---
cards:
  - model: Basic
    front: dummy
---`;
		const result = CardParserService.parseCards(partialYaml);
		expect(result.ranges.length).toBe(1);
		expect(result.ranges[0].nid).toBeNull();
	});
});

describe('CardParserService.noCardsResult', () => {
	const quick = (text: string) => CardParserService.noCardsResult(text.split('\n'));

	// Either null (needs the full parse) or exactly what parseCards returns, and never
	// a no-cards answer for a note in which parseCards finds a card.
	const expectAgrees = (text: string) => {
		const full = CardParserService.parseCards(text);
		const fast = quick(text);
		if (fast !== null) expect(fast).toEqual(full);
		if (full.hasCards) expect(fast).toBeNull();
		return fast;
	};

	it('needs the full parse when cards sit below a long frontmatter', () => {
		const keys = Array.from({ length: 400 }, (_, i) => `key_${i}: value ${i}`);
		const text = noteYaml({ cards: [card({ nid: 1 })] }).replace(
			'---\n',
			`---\n${keys.join('\n')}\n`,
		);
		expect(CardParserService.parseCards(text).hasCards).toBe(true);
		expect(expectAgrees(text)).toBeNull();
	});

	it('needs the full parse for a cards key anywhere parseCards looks', () => {
		for (const text of [
			'---\ntags:\n  - a\n  cards:\n    - Front: q\n---\n',
			'---\ncards: []\n---\n',
			'# No frontmatter\ncards:\n  - Front: q\n',
			'---\nunclosed: true\ncards:\n  - Front: q\n',
		]) {
			expect(expectAgrees(text)).toBeNull();
		}
	});

	it('answers without parsing when parseCards cannot find a cards key', () => {
		for (const text of [
			'',
			'---',
			'---\n---',
			'plain text',
			'---\ntitle: x\n---\n# Body\n',
			'---\ntitle: x\n---\ncards:\n  - Front: in the body, after the frontmatter\n',
			'cards:\n  - Front: on line 0, which parseCards skips\n',
		]) {
			expect(expectAgrees(text)).not.toBeNull();
		}
	});

	it('agrees with parseCards on generated notes', () => {
		let seed = 11;
		const rand = (n: number) => {
			seed ^= seed << 13;
			seed ^= seed >>> 17;
			seed ^= seed << 5;
			return (seed >>> 0) % n;
		};
		const pool = [
			'---',
			'cards:',
			'  cards:',
			'cards: []',
			'  - Front: q',
			'    Back: a',
			'- bullet',
			'title: x',
			'',
			'body text',
			'    anki:',
			"      nid: '123'",
		];
		let full = 0;
		for (let n = 0; n < 3000; n++) {
			const lines = Array.from({ length: 1 + rand(12) }, () => pool[rand(pool.length)]);
			if (rand(2) === 0) lines.unshift('---');
			if (expectAgrees(lines.join('\n')) === null) full++;
		}
		// Both outcomes occur, so the agreement above covers both.
		expect(full).toBeGreaterThan(100);
		expect(full).toBeLessThan(2900);
	});
});
