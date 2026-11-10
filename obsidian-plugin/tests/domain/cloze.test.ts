import { clozeOrdinals, renderCloze } from '@/domain/cloze';

describe('cloze rendering (one Anki card)', () => {
	const text =
		'FlashAttention uses {{c1::online softmax}} with a running {{c2::max and sum::two stats}}.';

	it('lists the cloze numbers in a field', () => {
		expect(clozeOrdinals(text)).toEqual([1, 2]);
		expect(clozeOrdinals('no deletions')).toEqual([]);
	});

	it('hides the active deletion on the front and shows the others', () => {
		expect(renderCloze(text, 1, 'Front')).toBe(
			'FlashAttention uses <span class="cloze">[...]</span> with a running max and sum.',
		);
	});

	it('shows the hint in place of the answer when there is one', () => {
		expect(renderCloze(text, 2, 'Front')).toContain('<span class="cloze">[two stats]</span>');
	});

	it('reveals the active deletion on the back', () => {
		expect(renderCloze(text, 1, 'Back')).toBe(
			'FlashAttention uses <span class="cloze">online softmax</span> with a running max and sum.',
		);
	});

	it('keeps math inside a deletion intact', () => {
		expect(renderCloze('Scale by {{c1::$1/\\sqrt{d_k}$}}.', 1, 'Back')).toBe(
			'Scale by <span class="cloze">$1/\\sqrt{d_k}$</span>.',
		);
	});
});

describe('cloze deletion inside math', () => {
	const text = 'Laws: $P \\land T_0 \\equiv {{c1::P}}$, $P \\lor T_0 \\equiv {{c2::T_0}}$.';

	it('hides it on the front as TeX text, with no HTML inside the math', () => {
		expect(renderCloze(text, 1, 'Front')).toBe(
			'Laws: $P \\land T_0 \\equiv [...]$, $P \\lor T_0 \\equiv T_0$.',
		);
	});

	it('reveals it on the back as TeX text', () => {
		expect(renderCloze(text, 2, 'Back')).toBe(
			'Laws: $P \\land T_0 \\equiv P$, $P \\lor T_0 \\equiv T_0$.',
		);
	});

	it('does the same inside display math', () => {
		expect(renderCloze('$$a = {{c1::b::rhs}}$$', 1, 'Front')).toBe('$$a = [rhs]$$');
	});

	it('still wraps a deletion that sits between two math spans', () => {
		expect(renderCloze('$a$ {{c1::and}} $b$', 1, 'Front')).toBe(
			'$a$ <span class="cloze">[...]</span> $b$',
		);
	});
});
