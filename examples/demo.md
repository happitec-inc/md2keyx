---
theme: White
slide_size: 1920x1080
hierarchy: section
author: Happitec Inc.
footer: md2keyx • Apple Keynote Interchange Format (.keyx)
---

# Markdown to Keynote
## Validated `.keyx` decks straight from plain Markdown

<!-- note: Cover slide generated via md2keyx + apple/creator-studio-doc-format. -->

---
<!-- layout: section -->

# Section 1: Core Features
## Standard CommonMark + Keynote Navigator Hierarchy

---

# Text, Links, Lists & Quotes

This deck compiles **standard Markdown** into schema-validated `.keyx` bundles with *real* Keynote objects, ~~hacks~~, and `inline_code`.

- Top YAML frontmatter sets theme, size, and footer
- HTML comments (`<!-- depth: 2 -->`, `<!-- layout: section -->`) control Keynote cleanly
  - Nested bullet levels map to `ListProperties`
  - Works in GitHub & Obsidian previews without Setext heading bugs
- Links like [Apple .keyx Docs](https://developer.apple.com/documentation/keynoteinterchangeformat) use native `HyperlinkField`

> Every presentation passes Apple's `validate_document` hook before saving.

<!-- note: Notice Slide 2 set layout: section, so this slide automatically nested at depth=2 in Keynote's navigator. -->

---

# Code Blocks & Tables

Fenced code blocks render inside dark monospace cards (and `---` inside fences never splits slides):

```python
from pathlib import Path
from markdown2keyx import parse_markdown, compile_deck

# --- Safe inside fenced code! ---
deck = parse_markdown(Path("examples/demo.md").read_text())
compile_deck(deck, Path("out/demo.keyx"), base_dir=Path("examples"))
```

| Feature | `.keyx` Target | Status |
| :--- | :--- | :--- |
| Headings & Lists | `Textbox` + `ListProperties` | MVP |
| Tables | Native `Table` | MVP |
| Mermaid Diagrams | SVG / PNG `Image` | Phase 2 |

::: notes
Pandoc-style ::: notes blocks also map cleanly to Slide.note.
:::

---
<!-- layout: section -->

# Section 2: Next Up
## Phase 2 Value-Adds

---
<!-- depth: 2 -->
<!-- identifier: phase-2-roadmap -->

# Phase 2 Roadmap

1. **Mermaid → Embed**: Test Keynote 16.0 `.svg` vs raster/PDF support inside `Image.resource`
2. **Syntax Highlighting**: Pygments tokens → colored `TextRun`s
3. **Two-Column Layouts & Callouts**: `::right::` splits and `> [!NOTE]` cards

Jump to [Phase 2 Roadmap](#phase-2-roadmap) or check [creator-studio-doc-format](https://github.com/apple/creator-studio-doc-format).

<!-- note: Explicit depth: 2 and identifier: phase-2-roadmap set via HTML comments. -->
