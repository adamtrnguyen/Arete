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
