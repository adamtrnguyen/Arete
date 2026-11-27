import httpx


def test_complex_markdown(tmp_path, anki_url, setup_anki, run_arete, test_deck):
    """Verify that Code blocks, MathJax, and Tables are preserved/converted correctly."""
    md_file = tmp_path / "complex.md"
    content = f"""---
deck: {test_deck}
arete: true
cards:
  - Front: Math Test
    Back: |
      The formula is $E=mc^2$.
      And a block:
      $$
      \\sum_{{i=0}}^n i^2
      $$
  - Front: Code Test
    Back: |
      ```python
      def hello():
          return "world"
      ```
  - Front: Table Test
    Back: |
      | A | B |
      |---|---|
      | 1 | 2 |
---
"""
    md_file.write_text(content, encoding="utf-8")

    run_arete(tmp_path, anki_url)

    # Verify Math
    resp = httpx.post(
        anki_url,
        json={
            "action": "findNotes",
            "version": 6,
            "params": {"query": f'"deck:{test_deck}" "Math Test"'},
        },
    )
    nid = resp.json()["result"][0]
    info = httpx.post(
        anki_url, json={"action": "notesInfo", "version": 6, "params": {"notes": [nid]}}
    ).json()["result"][0]
    back = info["fields"]["Back"]["value"]

    # arete converts $ to \( \) for Anki compatibility usually, or preserves them?
    # Checking implementation: convert_math_to_tex_delimiters does the job.
    assert r"\(E=mc^2\)" in back
    assert r"\[" in back  # Block math

    # Verify Code
    # Markdown conversion usually wraps code in <pre> or <code>
    resp_code = httpx.post(
        anki_url,
        json={
            "action": "findNotes",
            "version": 6,
            "params": {"query": f'"deck:{test_deck}" "Code Test"'},
        },
    )
    nid = resp_code.json()["result"][0]
    info = httpx.post(
        anki_url, json={"action": "notesInfo", "version": 6, "params": {"notes": [nid]}}
    ).json()["result"][0]
    back = info["fields"]["Back"]["value"]
    assert "def hello():" in back
    assert "return &quot;world&quot;" in back or 'return "world"' in back

    # Verify Table (Check for <table> tag)
    resp_table = httpx.post(
        anki_url,
        json={
            "action": "findNotes",
            "version": 6,
            "params": {"query": f'"deck:{test_deck}" "Table Test"'},
        },
    )
    nid = resp_table.json()["result"][0]
    info = httpx.post(
        anki_url, json={"action": "notesInfo", "version": 6, "params": {"notes": [nid]}}
    ).json()["result"][0]
    back = info["fields"]["Back"]["value"]
    assert "<table>" in back
    assert "<td>1</td>" in back
