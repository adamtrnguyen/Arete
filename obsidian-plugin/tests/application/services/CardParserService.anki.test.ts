import { parseYaml } from 'obsidian';
import { CardParserService } from '@/application/services/CardParserService';

// parseYaml is Obsidian's; here each test says what it returns for a card block, so the
// parser's handling of every shape of `anki:` value is pinned.
jest.mock('obsidian', () => ({ parseYaml: jest.fn() }));

const parse = parseYaml as jest.Mock;

const note = (...cardBlocks: string[]) =>
	['---', 'deck: D', 'cards:', ...cardBlocks, '---', '', 'body'].join('\n');

const block = (anki: string[] = []) => [
	'  - model: Basic',
	'    Front: q',
	'    Back: a',
	...anki.map((l) => `    ${l}`),
];

describe('CardParserService.parseCards: reading anki ids', () => {
	beforeEach(() => {
		parse.mockReset();
	});

	it('hands parseYaml the card with the dash blanked in place, so every key keeps its column', () => {
		parse.mockReturnValue({});
		CardParserService.parseCards(note(...block(['anki:', '  nid: 1'])));

		expect(parse).toHaveBeenCalledTimes(1);
		expect(parse.mock.calls[0][0]).toBe(
			['    model: Basic', '    Front: q', '    Back: a', '    anki:', '      nid: 1'].join(
				'\n',
			),
		);
	});

	it('reads numeric ids from the parsed anki mapping', () => {
		parse.mockReturnValue({ anki: { nid: 1762277751241, cid: 1762277751999 } });
		const { ranges } = CardParserService.parseCards(note(...block(['anki:'])));
		expect(ranges).toEqual([
			expect.objectContaining({ nid: 1762277751241, cid: 1762277751999 }),
		]);
	});

	it('reads ids that YAML parsed as strings', () => {
		parse.mockReturnValue({ anki: { nid: '1762277751241', cid: '42' } });
		const { ranges } = CardParserService.parseCards(note(...block(['anki:'])));
		expect(ranges[0].nid).toBe(1762277751241);
		expect(ranges[0].cid).toBe(42);
	});

	it('parses each card block on its own', () => {
		parse
			.mockReturnValueOnce({ anki: { nid: 1, cid: 2 } })
			.mockReturnValueOnce({ anki: { nid: 3, cid: 4 } });
		const text = note(...block(['anki:']), ...block(['anki:']));

		const { ranges, hasCards } = CardParserService.parseCards(text);

		expect(hasCards).toBe(true);
		expect(ranges.map((r) => [r.nid, r.cid])).toEqual([
			[1, 2],
			[3, 4],
		]);
		expect(ranges.map((r) => r.index)).toEqual([0, 1]);
		expect(parse).toHaveBeenCalledTimes(2);
	});

	it('falls back to the text when parseYaml throws, accepting quoted keys and values', () => {
		parse.mockImplementation(() => {
			throw new Error('bad yaml');
		});
		const text = note(...block(['anki:', `  'nid': '111'`, `  "cid": "222"`]));

		const { ranges } = CardParserService.parseCards(text);

		expect(ranges[0].nid).toBe(111);
		expect(ranges[0].cid).toBe(222);
	});

	it.each([
		['null', null],
		['a string', 'x'],
		['an object without anki', { Front: 'q' }],
		['anki set to null', { anki: null }],
		['anki set to a string', { anki: 'x' }],
		['anki set to an empty mapping', { anki: {} }],
	])('falls back to the text when parseYaml returns %s', (_name, parsed) => {
		parse.mockReturnValue(parsed);
		const text = note(...block(['anki:', '  nid: 7', '  cid: 8']));
		const { ranges } = CardParserService.parseCards(text);
		expect([ranges[0].nid, ranges[0].cid]).toEqual([7, 8]);
	});

	it('takes the id the mapping lacks from the text, and keeps the one it has', () => {
		parse.mockReturnValue({ anki: { nid: 5 } });
		const text = note(...block(['anki:', '  nid: 999', '  cid: 6']));
		const { ranges } = CardParserService.parseCards(text);
		// nid comes from the parsed mapping, not the text; cid only the text has
		expect([ranges[0].nid, ranges[0].cid]).toEqual([5, 6]);
	});

	it('ignores a parsed id that is not a number and reads the text instead', () => {
		parse.mockReturnValue({ anki: { nid: 'abc', cid: {} } });
		const text = note(...block(['anki:', '  nid: 12', '  cid: 34']));
		const { ranges } = CardParserService.parseCards(text);
		expect([ranges[0].nid, ranges[0].cid]).toEqual([12, 34]);
	});

	it('leaves both ids null for a card that was never synced', () => {
		parse.mockReturnValue({});
		const { ranges } = CardParserService.parseCards(note(...block()));
		expect([ranges[0].nid, ranges[0].cid]).toEqual([null, null]);
	});

	it('ends a card at the last non-blank line before the next card', () => {
		parse.mockReturnValue({});
		const text = note(...block(['anki:', '  nid: 1']), '', ...block());
		const lines = text.split('\n');

		const { ranges } = CardParserService.parseCards(text);

		expect(ranges).toHaveLength(2);
		expect(lines[ranges[0].endLine]).toBe('      nid: 1');
		expect(lines[ranges[1].startLine]).toBe('  - model: Basic');
	});
});
