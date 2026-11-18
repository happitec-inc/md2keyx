"""Tests for markdown2keyx parser and compiler."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import creator_studio_doc_format.examples as doc_examples
import pytest
from creator_studio_doc_format.core import KeyxDocument

from markdown2keyx import compile_deck, parse_markdown
from markdown2keyx.parser import (
    CodeBlock,
    HeadingBlock,
    ImageBlock,
    ListBlock,
    ParagraphBlock,
    QuoteBlock,
    TableBlock,
)


def _find_nulls(obj: object, path: str = "main") -> list[str]:
    found: list[str] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if v is None:
                found.append(f"{path}.{k}")
            else:
                found.extend(_find_nulls(v, f"{path}.{k}"))
    elif isinstance(obj, list):
        for i, item in enumerate(obj):
            found.extend(_find_nulls(item, f"{path}[{i}]"))
    return found


def test_parse_frontmatter_and_fenced_hr() -> None:
    md = """---
theme: Black
slide_size: 1280x720
hierarchy: section
---

# Cover

---
<!-- layout: section -->
# Part 1

---
# Inside Part 1

```markdown
---
not a slide break
---
```

<!-- note: presenter reminder -->
"""
    deck = parse_markdown(md)
    assert deck.config.theme == "Black"
    assert (deck.config.width, deck.config.height) == (1280, 720)
    assert len(deck.slides) == 3
    assert [s.depth for s in deck.slides] == [1, 1, 2]
    assert deck.slides[2].note == "presenter reminder"
    assert isinstance(deck.slides[2].blocks[1], CodeBlock)
    assert "---" in deck.slides[2].blocks[1].code


def test_depth_clamping_and_pandoc_notes() -> None:
    md = """---
hierarchy: flat
---

<!-- depth: 3 -->
# Slide 1

---
<!-- depth: 5 -->
# Slide 2

::: notes
Line one
Line two
:::
"""
    deck = parse_markdown(md)
    assert deck.slides[0].depth == 1
    assert deck.slides[1].depth == 2
    assert len(deck.warnings) == 2
    assert deck.slides[1].note == "Line one\nLine two"


def test_blocks_parsing() -> None:
    md = """# Title

Paragraph with **bold**, *italic*, ~~strike~~, `code`, and [link](https://example.com).

- Item 1
  - Nested 1a
- Item 2

1. First
2. Second

> Quote line

| Col A | Col B |
| --- | --- |
| 1 | 2 |

![Alt text](pic.png)
"""
    deck = parse_markdown(md)
    blocks = deck.slides[0].blocks
    assert isinstance(blocks[0], HeadingBlock)
    assert isinstance(blocks[1], ParagraphBlock)
    assert isinstance(blocks[2], ListBlock)
    assert blocks[2].ordered is False
    assert blocks[2].items[1].level == 1
    assert isinstance(blocks[3], ListBlock)
    assert blocks[3].ordered is True
    assert isinstance(blocks[4], QuoteBlock)
    assert isinstance(blocks[5], TableBlock)
    assert blocks[5].rows[0][0][0].text == "1"
    assert isinstance(blocks[6], ImageBlock)
    assert blocks[6].src == "pic.png"


def test_empty_input_raises() -> None:
    with pytest.raises(ValueError, match="No slides found"):
        parse_markdown("---\ntheme: White\n---\n")


def test_compile_e2e_with_image_links_and_roundtrip(tmp_path: Path) -> None:
    png_bytes = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\xcf\xc0"
        b"\x00\x00\x03\x01\x01\x00\xc9\xfe\x92\xef\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    (tmp_path / "sample.png").write_bytes(png_bytes)

    md_path = tmp_path / "deck.md"
    md_path.write_text(
        """---
theme: White
slide_size: 1920x1080
author: Test Author
footer: Test Footer
---

# Title Slide
## Subtitle

---
<!-- layout: section -->
<!-- identifier: sec-1 -->
# Section One

---
<!-- background: #F5F7FA -->
# Content Slide

Visit [Example](https://example.com), jump to [Section One](#sec-1), or [broken](#missing).

![Sample image](sample.png)

![Missing image](does-not-exist.png)

| A | B |
| - | - |
| x | y |

<!-- note: Speak clearly. -->
""",
        encoding="utf-8",
    )

    out_keyx = tmp_path / "deck.keyx"
    deck = parse_markdown(md_path.read_text(encoding="utf-8"))
    result = compile_deck(deck, out_keyx, base_dir=tmp_path)

    assert result.slide_count == 3
    assert out_keyx.is_file()
    assert any("does-not-exist.png" in w for w in result.warnings)
    assert any("#missing" in w for w in result.warnings)

    with zipfile.ZipFile(out_keyx, "r") as zf:
        names = set(zf.namelist())
        assert {"main.json", "manifest.json", "presets.json", "Data/sample.png"}.issubset(names)
        main = json.loads(zf.read("main.json"))
    assert _find_nulls(main) == []

    reopened = KeyxDocument.open(out_keyx)
    assert len(reopened.presentation.slides) == 3
    assert [s.depth for s in reopened.presentation.slides] == [1, 1, 2]
    assert reopened.presentation.slides[2].note.string == "Speak clearly."
    assert "sample.png" in reopened.resources


def test_compile_demo_deck(tmp_path: Path) -> None:
    demo_md = Path(__file__).resolve().parent.parent / "examples" / "demo.md"
    deck = parse_markdown(demo_md.read_text(encoding="utf-8"))
    out = tmp_path / "demo.keyx"
    result = compile_deck(deck, out, base_dir=demo_md.parent)
    assert result.slide_count == 6

    with zipfile.ZipFile(out, "r") as zf:
        main = json.loads(zf.read("main.json"))
    assert _find_nulls(main) == []

    reopened = KeyxDocument.open(out)
    assert len(reopened.presentation.slides) == 6
    assert [s.depth for s in reopened.presentation.slides] == [1, 1, 2, 2, 1, 2]


def test_compile_with_tidal_template(tmp_path: Path) -> None:
    tidal = Path(doc_examples.__file__).parent / "fixtures" / "Tidal_en.keyx"
    md = """---
hierarchy: section
---

# Tidal Deck
## Template test

---
<!-- layout: Title & Bullets -->
# Key Points

- First bullet
  - Sub bullet
- Second bullet
"""
    deck = parse_markdown(md)
    out = tmp_path / "tidal_out.keyx"
    result = compile_deck(deck, out, base_dir=tmp_path, template_override=tidal)
    assert result.slide_count == 2
    reopened = KeyxDocument.open(out)
    assert len(reopened.presentation.slides) == 2
    assert reopened.presentation.slides[1].layout == "Title & Bullets"
