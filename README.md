# md2keyx

Compile plain Markdown decks into validated Apple [Keynote Interchange Format](https://developer.apple.com/documentation/keynoteinterchangeformat) (`.keyx`) presentations using Apple's [`creator-studio-doc-format`](https://github.com/apple/creator-studio-doc-format) library.

- Runs offline on Linux or macOS (Python 3.13+) without Keynote running.
- Standard CommonMark + GFM syntax: one top YAML frontmatter block, `---` slide separators, and HTML comment directives (`<!-- depth: 2 -->`, `<!-- layout: section -->`) that render cleanly in GitHub and Obsidian.
- Validates every output via `creator_studio_doc_format.validation.validate_document` before writing `.keyx`.

## Install

### Homebrew (macOS)

```bash
brew install happitec-inc/tap/md2keyx
```

### Python / uv

```bash
uv pip install git+https://github.com/happitec-inc/md2keyx.git
```

## Usage

```bash
md2keyx examples/demo.md -o out/demo.keyx

# Use an exported .keyx theme/template's layouts and styles
md2keyx deck.md --template MyTheme.keyx -o out/deck.keyx

# Open directly in Keynote Creator Studio (macOS)
md2keyx examples/demo.md --open
```

## Markdown Format

### Top YAML Frontmatter

```yaml
---
theme: White          # Built-in Keynote theme name (default: White)
template: theme.keyx  # Optional .keyx template path for layout/placeholder mapping
slide_size: 1920x1080 # Canvas dimensions in points (default: 1920x1080)
locale: en_US         # Theme locale (default: en_US)
hierarchy: section    # "section" (auto-nest after section slides) or "flat"
author: Your Name     # Shown on cover slide
footer: Footer Text   # Shown on content slides
---
```

### Slides, Hierarchy & Notes

- **`---`** splits slides (ignored inside fenced code blocks).
- **`<!-- layout: section -->`** (or `<!-- section: true -->`) creates a section divider slide at Keynote navigator `depth: 1`; when `hierarchy: section` is active, following slides automatically nest at `depth: 2` until the next section slide.
- **`<!-- depth: N -->`** sets explicit Keynote navigator indentation (`1`–`6`, clamped to at most `+1` deeper than the preceding slide).
- **`<!-- background: #RRGGBB -->`** sets an explicit slide background color.
- **`<!-- identifier: my-slide -->`** sets a slide ID for `#my-slide` internal slide-jump links.
- **`<!-- note: ... -->`** or **`::: notes ... :::`** attaches presenter notes to `Slide.note`.

### Supported Elements

- Headings (`#`–`######`), paragraphs, **bold**, *italic*, ~~strikethrough~~, `inline code`, and `[links](https://...)` / `[slide jumps](#slide-id)`
- Nested bullet and ordered lists (`ListProperties`)
- Fenced code blocks (monospace dark card)
- Blockquotes (accent callout card)
- GFM pipe tables (native Keynote `Table` with header row and alternating rows)
- Local images (`![alt](path)` bundled into `Data/` with accessibility description)

## Development

```bash
uv sync --group dev
uv run pytest -v
```
