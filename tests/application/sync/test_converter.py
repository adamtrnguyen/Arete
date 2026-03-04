import pytest

from arete.application.sync.converter import markdown_to_anki_html


def test_math_placeholder_indices_do_not_collide():
    text = " ".join(f"${i}$" for i in range(12))

    html = markdown_to_anki_html(text)

    assert r"\(10\)" in html
    assert r"\(11\)" in html
    assert r"\(1\)0" not in html
    assert r"\(1\)1" not in html


def test_math_placeholder_state_resets_between_calls():
    warmup = " ".join(f"${i}$" for i in range(15))
    markdown_to_anki_html(warmup)

    text = " ".join(f"${i}$" for i in range(12))
    html = markdown_to_anki_html(text)

    assert r"\(10\)" in html
    assert r"\(11\)" in html
    assert r"\(1\)0" not in html


def test_math_is_not_parsed_inside_code():
    text = "`$x$`\n\n```\n$y$\n```\n\n$z$"

    html = markdown_to_anki_html(text)

    assert "<code>$x$</code>" in html
    assert "<code>$y$" in html
    assert r"\(z\)" in html
    assert r"\(x\)" not in html
    assert r"\(y\)" not in html


def test_escaped_dollar_is_not_math():
    text = r"Price is \$5 and math is $x$."

    html = markdown_to_anki_html(text)

    assert r"\(x\)" in html
    assert r"\(5\)" not in html


def test_display_math_converted_to_brackets():
    text = "$$E = mc^2$$"

    html = markdown_to_anki_html(text)

    assert r"\[E = mc^2\]" in html
    assert "$$" not in html


def test_display_math_multiline():
    text = "$$\nx + y\n= z\n$$"

    html = markdown_to_anki_html(text)

    assert r"\[" in html
    assert r"\]" in html
    assert "$$" not in html


def test_mixed_inline_and_display_math():
    text = "Inline $a+b$ then display $$c+d$$ end."

    html = markdown_to_anki_html(text)

    assert r"\(a+b\)" in html
    assert r"\[c+d\]" in html
    assert "$" not in html.replace(r"\(", "").replace(r"\)", "").replace(r"\[", "").replace(r"\]", "")


@pytest.mark.parametrize(
    "text,protected,converted",
    [
        pytest.param("~~~\n$x + y$\n~~~\n\n$z$", "$x + y$", r"\(z\)", id="tilde_fence"),
        pytest.param("``$x + y$`` and $z$", "$x + y$", r"\(z\)", id="double_backtick"),
        pytest.param(
            '```python\nprice = 5\nx = "$100"\n```\n\n$a$',
            "$100",
            r"\(a\)",
            id="lang_fence",
        ),
    ],
)
def test_code_blocks_preserve_dollars(text, protected, converted):
    html = markdown_to_anki_html(text)
    assert protected in html
    assert converted in html


def test_empty_display_math():
    text = "$$$$"

    html = markdown_to_anki_html(text)

    assert r"\[\]" in html


def test_math_with_special_characters():
    text = r"$\frac{a}{b}$ and $x_{i}^{2}$ and $\sum_{n=1}^{\infty}$"

    html = markdown_to_anki_html(text)

    assert r"\(\frac{a}{b}\)" in html
    assert r"\(x_{i}^{2}\)" in html
    assert r"\(\sum_{n=1}^{\infty}\)" in html


def test_multiple_consecutive_inline_math():
    text = "$a$ $b$ $c$"

    html = markdown_to_anki_html(text)

    assert r"\(a\)" in html
    assert r"\(b\)" in html
    assert r"\(c\)" in html


def test_dollar_at_start_of_line():
    text = "$x + 1$ is positive."

    html = markdown_to_anki_html(text)

    assert r"\(x + 1\)" in html


def test_dollar_at_end_of_line():
    text = "The answer is $42$"

    html = markdown_to_anki_html(text)

    assert r"\(42\)" in html


def test_display_math_with_braces_and_subscripts():
    text = r"$$\int_{0}^{1} f(x)\, dx = F(1) - F(0)$$"

    html = markdown_to_anki_html(text)

    assert r"\[\int_{0}^{1} f(x)\, dx = F(1) - F(0)\]" in html
    assert "$$" not in html


def test_code_block_with_dollar_signs_not_converted():
    text = "```\nif price > $100:\n    total = $200\n```"

    html = markdown_to_anki_html(text)

    assert "$100" in html
    assert "$200" in html
    assert r"\(" not in html


def test_multiple_display_math_blocks():
    text = "First: $$a = 1$$ Second: $$b = 2$$"

    html = markdown_to_anki_html(text)

    assert r"\[a = 1\]" in html
    assert r"\[b = 2\]" in html
