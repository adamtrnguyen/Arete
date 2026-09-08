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
