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

	// The ids come from the parsed mapping alone. Each block below spells ids in its
	// text, so a parser that searched the text would find them.
	it('gives a card whose YAML does not parse no ids, and still its range', () => {
		parse.mockImplementation(() => {
			throw new Error('bad yaml');
		});
		const text = note(...block(['anki:', `  'nid': '111'`, `  "cid": "222"`]));

		const { ranges } = CardParserService.parseCards(text);

		expect(ranges).toHaveLength(1);
		expect([ranges[0].nid, ranges[0].cid]).toEqual([null, null]);
	});

	it.each([
		['null', null],
		['a string', 'x'],
		['an object without anki', { Front: 'q' }],
		['a flat nid beside the fields', { Front: 'q', nid: 7, cid: 8 }],
		['anki set to null', { anki: null }],
		['anki set to a string', { anki: 'x' }],
		['anki set to a list', { anki: [7, 8] }],
		['anki set to an empty mapping', { anki: {} }],
	])('gives no ids when parseYaml returns %s', (_name, parsed) => {
		parse.mockReturnValue(parsed);
		const text = note(...block(['anki:', '  nid: 7', '  cid: 8']));
		const { ranges } = CardParserService.parseCards(text);
		expect([ranges[0].nid, ranges[0].cid]).toEqual([null, null]);
	});

	it('reads the id the mapping has and leaves the one it lacks null', () => {
		parse.mockReturnValue({ anki: { nid: 5 } });
		const text = note(...block(['anki:', '  nid: 999', '  cid: 6']));
		const { ranges } = CardParserService.parseCards(text);
		expect([ranges[0].nid, ranges[0].cid]).toEqual([5, null]);
	});

	it.each([
		['a word', 'abc'],
		['a mapping', {}],
		['a fraction', 1.5],
		['a negative number as text', '-3'],
		['a number past the safe integer range', 2 ** 60],
	])('gives no id for %s', (_name, raw) => {
		parse.mockReturnValue({ anki: { nid: raw, cid: raw } });
		const text = note(...block(['anki:', '  nid: 12', '  cid: 34']));
		const { ranges } = CardParserService.parseCards(text);
		expect([ranges[0].nid, ranges[0].cid]).toEqual([null, null]);
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
