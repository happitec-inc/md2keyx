"""Tests for markdown2keyx parser, mermaid renderer, CLI, and compiler."""

from __future__ import annotations

import json
import struct
import zipfile
import zlib
from pathlib import Path

import creator_studio_doc_format.examples as doc_examples
import pytest
from creator_studio_doc_format.core import KeyxDocument

from markdown2keyx import compile_deck, parse_markdown
from markdown2keyx.cli import main as cli_main
from markdown2keyx.mermaid import build_mermaid_ink_url, image_dimensions
from markdown2keyx.parser import (
    CalloutBlock,
    CodeBlock,
    HeadingBlock,
    ImageBlock,
    ListBlock,
    MermaidBlock,
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


def _make_png(width: int, height: int) -> bytes:
    """Create a valid minimal RGB PNG with explicit pixel dimensions."""

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    raw = b"".join(b"\x00" + b"\x22\x66\xcc" * width for _ in range(height))
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


def test_parse_frontmatter_and_fenced_hr() -> None:
    md = """---
theme: Black
slide_size: 1280x720
hierarchy: section
mermaid: off
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
    assert deck.config.mermaid == "off"
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


def test_multiline_hex_background_directive() -> None:
    md = """# Slide 1

---
<!--
background: #112233
depth: 2
-->
# Slide 2
"""
    deck = parse_markdown(md)
    assert deck.slides[1].background == "#112233"
    assert deck.slides[1].depth == 2


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


def test_mermaid_parsing_and_mock_renderer_aspect_ratio(tmp_path: Path) -> None:
    md = """# Architecture Diagram

```mermaid
flowchart LR
  A[Client] --> B[Server]
```

```python
x = 1
```
"""
    deck = parse_markdown(md)
    blocks = deck.slides[0].blocks
    assert isinstance(blocks[0], HeadingBlock)
    assert isinstance(blocks[1], MermaidBlock)
    assert "A[Client] --> B[Server]" in blocks[1].code
    assert isinstance(blocks[2], CodeBlock)
    assert blocks[2].lang == "python"

    # 2:1 landscape PNG (800x400) -> should keep 2:1 aspect ratio and center horizontally.
    wide_png = _make_png(800, 400)
    out = tmp_path / "mermaid.keyx"
    result = compile_deck(
        deck,
        out,
        base_dir=tmp_path,
        mermaid_renderer=lambda _code: (wide_png, ".png"),
    )
    assert result.warnings == []

    with zipfile.ZipFile(out, "r") as zf:
        names = set(zf.namelist())
        assert "Data/mermaid-1.png" in names
        main = json.loads(zf.read("main.json"))
    assert _find_nulls(main) == []

    reopened = KeyxDocument.open(out)
    assert "mermaid-1.png" in reopened.resources
    images = [o.object.image for o in reopened.presentation.slides[0].contents if o.object.image]
    assert len(images) == 1
    img = images[0]
    assert img.resource.indirect is not None
    assert img.resource.indirect.filename == "mermaid-1.png"
    assert img.geometry is not None
    assert img.geometry.size.width / img.geometry.size.height == pytest.approx(2.0, abs=0.02)
    # Centered inside [120, 1800] content width (1680):
    expected_x = 120.0 + (1680.0 - img.geometry.size.width) / 2.0
    assert img.geometry.position.x == pytest.approx(expected_x, abs=0.2)


def test_mermaid_off_and_failure_fallback(tmp_path: Path) -> None:
    md = """---
mermaid: off
---

# Offline Diagram

```mermaid
graph TD
  A --> B
```
"""
    deck = parse_markdown(md)
    out_off = tmp_path / "off.keyx"
    res_off = compile_deck(deck, out_off, base_dir=tmp_path)
    assert any("mermaid=off" in w for w in res_off.warnings)

    reopened_off = KeyxDocument.open(out_off)
    assert not any(o.object.image for o in reopened_off.presentation.slides[0].contents)
    shapes = [o.object.shape for o in reopened_off.presentation.slides[0].contents if o.object.shape]
    assert any(
        s.text and any("A --> B" in (r.run.string or "") for p in s.text.paragraphs for r in p.contents)
        for s in shapes
    )

    # Also test renderer failure fallback when mermaid mode is auto
    deck_auto = parse_markdown("# Fallback\n\n```mermaid\ngraph LR\n  X --> Y\n```\n")
    out_err = tmp_path / "err.keyx"

    def _boom(_code: str) -> tuple[bytes, str]:
        raise RuntimeError("network unreachable")

    res_err = compile_deck(deck_auto, out_err, base_dir=tmp_path, mermaid_renderer=_boom)
    assert any("network unreachable" in w for w in res_err.warnings)
    reopened_err = KeyxDocument.open(out_err)
    assert not any(o.object.image for o in reopened_err.presentation.slides[0].contents)

    # Empty extension from custom renderer defaults to .png
    out_empty_ext = tmp_path / "empty_ext.keyx"
    compile_deck(
        deck_auto,
        out_empty_ext,
        base_dir=tmp_path,
        mermaid_renderer=lambda _code: (_make_png(120, 60), ""),
    )
    with zipfile.ZipFile(out_empty_ext, "r") as zf:
        assert "Data/mermaid-1.png" in set(zf.namelist())

    # Invalid mermaid_mode fails fast even without Mermaid blocks
    plain_deck = parse_markdown("# Plain Slide\n\nNo diagrams.\n")
    with pytest.raises(ValueError, match="Unsupported mermaid mode"):
        compile_deck(plain_deck, tmp_path / "invalid.keyx", base_dir=tmp_path, mermaid_mode="bogus")


def test_cli_mermaid_off(tmp_path: Path) -> None:
    md_path = tmp_path / "deck.md"
    md_path.write_text("# Diagram\n\n```mermaid\ngraph LR\n  A --> B\n```\n", encoding="utf-8")
    out_path = tmp_path / "cli.keyx"
    rc = cli_main([str(md_path), "-o", str(out_path), "--mermaid", "off"])
    assert rc == 0
    assert out_path.is_file()


def test_image_dimensions_and_ink_url() -> None:
    png_dims = image_dimensions(_make_png(320, 180), ".png")
    assert png_dims == (320.0, 180.0)
    svg_dims = image_dimensions(b'<svg viewBox="0 0 640 320"></svg>', ".svg")
    assert svg_dims == (640.0, 320.0)
    # Minimal JPEG SOF0 header: FFD8 FFC0 len(11) prec(8) H(200) W(400)
    jpeg = b"\xff\xd8\xff\xc0\x00\x0b\x08\x00\xc8\x01\x90\x03\x01\x11\x00\xff\xd9"
    assert image_dimensions(jpeg, ".jpg") == (400.0, 200.0)
    url = build_mermaid_ink_url("graph LR\n  A --> B")
    assert url.startswith("https://mermaid.ink/img/pako:")
    assert url.endswith("?type=png")


def test_pygments_syntax_highlighting_and_no_null_overrides(tmp_path: Path) -> None:
    md = """# Code Highlighting

```python
# Greeting helper
def greet(name: str) -> str:
    count = 42
    return f"Hello, {name}"
```
"""
    deck = parse_markdown(md)
    out = tmp_path / "code.keyx"
    compile_deck(deck, out, base_dir=tmp_path)

    with zipfile.ZipFile(out, "r") as zf:
        main = json.loads(zf.read("main.json"))
    assert _find_nulls(main) == []

    reopened = KeyxDocument.open(out)
    code_shapes = [
        o.object.shape
        for o in reopened.presentation.slides[0].contents
        if o.object.shape and o.object.shape.preset_identifier == "m2k_shape_code"
    ]
    assert len(code_shapes) == 1
    paras = code_shapes[0].text.paragraphs
    assert len(paras) == 4  # One Paragraph per line

    colors = set()
    has_bold_keyword = False
    has_italic_comment = False
    for para in paras:
        for item in para.contents:
            run = item.run
            assert run is not None and run.style is not None
            fill = run.style.character_fill.get()
            rgba = fill.color.rgba
            colors.add((round(rgba.red, 3), round(rgba.green, 3), round(rgba.blue, 3)))
            if run.string == "def" and run.style.bold.is_value and run.style.bold.get():
                has_bold_keyword = True
            if "# Greeting helper" in (run.string or "") and run.style.italic.is_value:
                has_italic_comment = True

    assert len(colors) >= 4
    assert has_bold_keyword
    assert has_italic_comment


def test_gfm_callouts_all_kinds_and_plain_quote(tmp_path: Path) -> None:
    md = """# Callouts

> [!note]
> Single-paragraph note with **bold** and `code`.

> [!TIP] Custom Tip Title
> Tip body text.

> [!IMPORTANT]
>
> Multi-paragraph important body.
>
> Second paragraph with [link](https://example.com).

> [!warning]
> Watch out.

> [!CAUTION]
> Danger zone.

> Plain standard blockquote.

> [!UNKNOWN]
> Not a GFM callout kind.
"""
    deck = parse_markdown(md)
    blocks = deck.slides[0].blocks
    callouts = [b for b in blocks if isinstance(b, CalloutBlock)]
    quotes = [b for b in blocks if isinstance(b, QuoteBlock)]

    assert [c.kind for c in callouts] == ["NOTE", "TIP", "IMPORTANT", "WARNING", "CAUTION"]
    assert callouts[0].title is None
    assert callouts[1].title == "Custom Tip Title"
    assert len(callouts[2].paragraphs) == 2
    assert len(quotes) == 2

    out = tmp_path / "callouts.keyx"
    compile_deck(deck, out, base_dir=tmp_path)

    with zipfile.ZipFile(out, "r") as zf:
        main = json.loads(zf.read("main.json"))
    assert _find_nulls(main) == []

    reopened = KeyxDocument.open(out)
    shapes = [
        o.object.shape
        for o in reopened.presentation.slides[0].contents
        if o.object.shape and o.object.shape.preset_identifier != "m2k_shape_rule"
    ]
    # 5 callouts * 2 shapes (card + bar) + 2 quotes * 2 shapes (card + bar) = 14 shapes
    assert len(shapes) == 14
    callout_cards = [
        s for s in shapes if s.text and s.preset_identifier != "m2k_shape_quote"
    ]
    assert len(callout_cards) == 5
    headers = [c.text.paragraphs[0].contents[0].run.string for c in callout_cards]
    assert headers == ["Note", "Custom Tip Title", "Important", "Warning", "Caution"]
    bg_colors = {
        (
            round(c.fill.color.rgba.red, 3),
            round(c.fill.color.rgba.green, 3),
            round(c.fill.color.rgba.blue, 3),
        )
        for c in callout_cards
        if c.fill and c.fill.color
    }
    assert len(bg_colors) == 5


def test_two_column_slide_and_custom_ratio(tmp_path: Path) -> None:
    md = """<!-- columns: 60/40 -->
# Two-Column Architecture

Left side bullets:

- Item A
- Item B

<!-- column -->

Right side card:

> [!TIP] Fast Path
> Fits cleanly inside the right column.
"""
    deck = parse_markdown(md)
    slide_spec = deck.slides[0]
    assert slide_spec.columns is not None
    assert slide_spec.column_ratio == pytest.approx(0.6)
    assert len(slide_spec.blocks) == 1
    assert isinstance(slide_spec.blocks[0], HeadingBlock)

    out = tmp_path / "columns.keyx"
    compile_deck(deck, out, base_dir=tmp_path)

    with zipfile.ZipFile(out, "r") as zf:
        main = json.loads(zf.read("main.json"))
    assert _find_nulls(main) == []

    reopened = KeyxDocument.open(out)
    contents = reopened.presentation.slides[0].contents
    # Title textbox + divider rule + left paragraph + left list + right paragraph + right callout (card + bar)
    textboxes = [o.object.textbox for o in contents if o.object.textbox]
    shapes = [o.object.shape for o in contents if o.object.shape]
    left_box = next(
        t for t in textboxes if any("Left side" in (c.run.string or "") for c in t.text.paragraphs[0].contents)
    )
    right_box = next(
        t for t in textboxes if any("Right side" in (c.run.string or "") for c in t.text.paragraphs[0].contents)
    )
    assert left_box.geometry.position.x == pytest.approx(120.0)
    assert right_box.geometry.position.x > left_box.geometry.position.x + left_box.geometry.size.width
    assert left_box.geometry.size.width > right_box.geometry.size.width
    assert left_box.geometry.size.width / (left_box.geometry.size.width + right_box.geometry.size.width) == pytest.approx(0.6, abs=0.01)

    callout_card = next(s for s in shapes if s.text is not None)
    assert callout_card.geometry.position.x == pytest.approx(right_box.geometry.position.x)
    assert callout_card.geometry.size.width == pytest.approx(right_box.geometry.size.width)


def test_two_column_ratio_clamping_and_intro_blocks() -> None:
    md = """# Header

Full-width intro paragraph.

<!-- columns: 10/90 -->

Left

<!-- col -->

Right
"""
    deck = parse_markdown(md)
    assert deck.slides[0].column_ratio == pytest.approx(0.2)
    assert any("clamped" in w for w in deck.warnings)
    assert len(deck.slides[0].blocks) == 2
    assert deck.slides[0].columns is not None


def test_compile_e2e_with_image_links_and_roundtrip(tmp_path: Path) -> None:
    png_bytes = _make_png(200, 100)
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
    img = next(o.object.image for o in reopened.presentation.slides[2].contents if o.object.image)
    assert img.geometry.size.width / img.geometry.size.height == pytest.approx(2.0, abs=0.02)
    bg_override = reopened.presentation.slides[2].background
    assert bg_override.is_value
    bg_rgba = bg_override.get().standard.color.rgba
    assert bg_rgba.red == pytest.approx(0xF5 / 255.0)
    assert bg_rgba.green == pytest.approx(0xF7 / 255.0)
    assert bg_rgba.blue == pytest.approx(0xFA / 255.0)


def test_compile_demo_deck(tmp_path: Path) -> None:
    demo_md = Path(__file__).resolve().parent.parent / "examples" / "demo.md"
    deck = parse_markdown(demo_md.read_text(encoding="utf-8"))
    out = tmp_path / "demo.keyx"
    diagram_png = _make_png(900, 500)
    result = compile_deck(
        deck,
        out,
        base_dir=demo_md.parent,
        mermaid_renderer=lambda _code: (diagram_png, ".png"),
    )
    assert result.slide_count == 6
    assert result.warnings == []

    with zipfile.ZipFile(out, "r") as zf:
        names = set(zf.namelist())
        assert "Data/mermaid-1.png" in names
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

---
<!-- layout: Title & Bullets -->
# Data Summary

| Metric | Value |
| --- | --- |
| Slides | 3 |

---
<!-- columns: 50/50 -->
# Two Columns With Template

Left content

<!-- column -->

Right content
"""
    deck = parse_markdown(md)
    out = tmp_path / "tidal_out.keyx"
    result = compile_deck(deck, out, base_dir=tmp_path, template_override=tidal)
    assert result.slide_count == 4

    with zipfile.ZipFile(out, "r") as zf:
        main = json.loads(zf.read("main.json"))
    assert _find_nulls(main) == []

    reopened = KeyxDocument.open(out)
    assert len(reopened.presentation.slides) == 4
    assert reopened.presentation.slides[1].layout == "Title & Bullets"
    body_placeholder = next(
        obj
        for obj in reopened.presentation.slides[2].contents
        if obj.placeholder_info
        and obj.placeholder_info.defined
        and obj.placeholder_info.defined.kind.value == "body"
    )
    body_obj = body_placeholder.object.textbox or body_placeholder.object.shape
    assert body_obj is not None
    assert body_obj.text is None or all(p.contents is None for p in (body_obj.text.paragraphs or []))
    assert any(obj.object.table is not None for obj in reopened.presentation.slides[2].contents)
    col_boxes = [
        o.object.textbox
        for o in reopened.presentation.slides[3].contents
        if o.object.textbox
    ]
    left_box = next(b for b in col_boxes if b.text.paragraphs[0].contents[0].run.string == "Left content")
    right_box = next(b for b in col_boxes if b.text.paragraphs[0].contents[0].run.string == "Right content")
    assert right_box.geometry.position.x > left_box.geometry.position.x
