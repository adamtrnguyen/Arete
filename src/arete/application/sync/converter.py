"""Markdown to Anki HTML conversion logic.
Extracted from apy logic to allow direct Python usage.
"""

from __future__ import annotations

import re

import markdown
from markdown.extensions import Extension
from markdown.postprocessors import Postprocessor
from markdown.preprocessors import Preprocessor


class MathProtectExtension(Extension):
    """Extension to avoid converting markdown within math blocks"""

    def __init__(self, markdown_latex_mode: str = "mathjax") -> None:
        super().__init__()
        self.markdown_latex_mode: str = markdown_latex_mode

    def extendMarkdown(self, md: markdown.Markdown) -> None:
        math_preprocessor = MathPreprocessor(md, self.markdown_latex_mode)
        math_postprocessor = MathPostprocessor(md, math_preprocessor.placeholders)

        md.preprocessors.register(math_preprocessor, "math_block_processor", 25)
        md.postprocessors.register(math_postprocessor, "math_block_restorer", 25)


class MathPreprocessor(Preprocessor):
    def __init__(self, md: markdown.Markdown, markdown_latex_mode: str) -> None:
        super().__init__(md)
        self.counter: int = 0
        self.placeholders: dict[str, str] = {}

        # Apply latex translation based on specified latex mode
        if markdown_latex_mode == "latex":
            self.fmt_display: str = "[$$]{math}[/$$]"
            self.fmt_inline: str = "[$]{math}[/$]"
        else:
            # Default to MathJax style
            self.fmt_display = r"\[{math}\]"
            self.fmt_inline = r"\({math}\)"

    def run(self, lines: list[str]) -> list[str]:
        # Reset per conversion. The markdown instance is reused globally.
        self.counter = 0
        self.placeholders.clear()

        lines_joined = "\n".join(lines)
        lines_processed = self._replace_math_delimiters(lines_joined)
        return lines_processed.split("\n")

    def _replace_math_delimiters(self, text: str) -> str:
        out: list[str] = []
        i = 0
        n = len(text)

        in_fence = False
        fence_char = ""
        fence_len = 0
        code_span_len = 0

        while i < n:
            line_start = i == 0 or text[i - 1] == "\n"

            # Preserve fenced code blocks verbatim.
            if line_start:
                line_end = text.find("\n", i)
                if line_end == -1:
                    line_end = n
                line = text[i:line_end]

                fence = self._parse_fence(line)
                if in_fence:
                    if fence and fence[0] == fence_char and fence[1] >= fence_len:
                        in_fence = False
                    out.append(line)
                    if line_end < n:
                        out.append("\n")
                    i = line_end + (1 if line_end < n else 0)
                    continue

                if code_span_len == 0 and fence:
                    in_fence = True
                    fence_char, fence_len = fence
                    out.append(line)
                    if line_end < n:
                        out.append("\n")
                    i = line_end + (1 if line_end < n else 0)
                    continue

            ch = text[i]

            # Preserve inline code spans verbatim.
            if code_span_len:
                if ch == "`":
                    run_len = self._backtick_run_length(text, i)
                    if run_len == code_span_len:
                        code_span_len = 0
                    out.append(text[i : i + run_len])
                    i += run_len
                    continue
                out.append(ch)
                i += 1
                continue

            if ch == "`":
                run_len = self._backtick_run_length(text, i)
                code_span_len = run_len
                out.append(text[i : i + run_len])
                i += run_len
                continue

            # Parse math only when not escaped and not in code.
            if ch == "$" and not self._is_escaped(text, i):
                # Display math: $$...$$ (can span lines)
                if i + 1 < n and text[i + 1] == "$":
                    close = self._find_display_close(text, i + 2)
                    if close != -1:
                        placeholder = self._store_math(text[i + 2 : close], is_display=True)
                        out.append(placeholder)
                        i = close + 2
                        continue

                    # No closing delimiter; treat as literal.
                    out.append("$$")
                    i += 2
                    continue

                # Inline math: $...$ (single line)
                if i + 1 < n and not text[i + 1].isspace():
                    close = self._find_inline_close(text, i + 1)
                    if close != -1:
                        placeholder = self._store_math(text[i + 1 : close], is_display=False)
                        out.append(placeholder)
                        i = close + 1
                        continue

                # Not a valid inline math opening.
                out.append("$")
                i += 1
                continue

            out.append(ch)
            i += 1

        return "".join(out)

    @staticmethod
    def _parse_fence(line: str) -> tuple[str, int] | None:
        idx = 0
        while idx < len(line) and idx < 3 and line[idx] == " ":
            idx += 1

        if idx >= len(line):
            return None

        ch = line[idx]
        if ch not in {"`", "~"}:
            return None

        j = idx
        while j < len(line) and line[j] == ch:
            j += 1

        run_len = j - idx
        if run_len < 3:
            return None
        return ch, run_len

    @staticmethod
    def _backtick_run_length(text: str, start: int) -> int:
        j = start
        while j < len(text) and text[j] == "`":
            j += 1
        return j - start

    @staticmethod
    def _is_escaped(text: str, idx: int) -> bool:
        backslashes = 0
        j = idx - 1
        while j >= 0 and text[j] == "\\":
            backslashes += 1
            j -= 1
        return (backslashes % 2) == 1

    def _find_display_close(self, text: str, start: int) -> int:
        j = start
        while j < len(text) - 1:
            if text[j] == "$" and text[j + 1] == "$" and not self._is_escaped(text, j):
                return j
            j += 1
        return -1

    def _find_inline_close(self, text: str, start: int) -> int:
        j = start
        while j < len(text):
            if text[j] == "\n":
                return -1
            if text[j] == "$" and not self._is_escaped(text, j) and not text[j - 1].isspace():
                return j
            j += 1
        return -1

    def _store_math(self, content: str, is_display: bool) -> str:
        placeholder = f"MATH-PLACEHOLDER-{self.counter}"
        self.counter += 1
        if is_display:
            self.placeholders[placeholder] = self.fmt_display.format(math=content)
        else:
            self.placeholders[placeholder] = self.fmt_inline.format(math=content)
        return placeholder


class MathPostprocessor(Postprocessor):
    def __init__(self, md: markdown.Markdown, placeholders: dict[str, str]) -> None:
        super().__init__(md)
        self.placeholders: dict[str, str] = placeholders

    def run(self, text: str) -> str:
        # Replace complete placeholder tokens only.
        # This avoids collisions like MATH-PLACEHOLDER-1 inside
        # MATH-PLACEHOLDER-10.
        def restore(match: re.Match[str]) -> str:
            placeholder = match.group(0)
            return self.placeholders.get(placeholder, placeholder)

        return re.sub(r"MATH-PLACEHOLDER-\d+", restore, text)


_md_instance: markdown.Markdown | None = None


def markdown_to_anki_html(text: str, latex_mode: str = "mathjax") -> str:
    """Convert markdown text to Anki-compatible HTML.
    Includes special handling for MathJax protection.
    """
    global _md_instance
    if _md_instance is None:
        _md_instance = markdown.Markdown(
            extensions=[
                "fenced_code",
                "tables",
                MathProtectExtension(latex_mode),
            ]
        )
    else:
        _md_instance.reset()

    html = _md_instance.convert(text)
    # Add arete's marker comment for consistency detection if needed
    # but strictly speaking we don't need it if we trust our DB.
    # We'll add it to match apy behavior for now.
    return f"<!-- arete markdown -->\n{html}"
