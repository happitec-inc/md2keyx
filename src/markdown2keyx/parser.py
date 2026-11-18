"""Markdown deck parser for markdown2keyx.

Supports:
- One standard YAML frontmatter block at the top of the file.
- `---` slide delimiters (ignoring `---` inside fenced code blocks).
- Per-slide HTML comment directives (`<!-- depth: 2 -->`, `<!-- layout: section -->`,
  `<!-- background: #111827 -->`, `<!-- section: true -->`).
- Presenter notes via `<!-- note: ... -->` or Pandoc-style `::: notes ... :::`.
- Headings, paragraphs, inline styles (bold, italic, strikethrough, code, links),
  nested bullet/ordered lists, fenced code blocks, blockquotes, GFM tables, and images.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

import yaml
from markdown_it import MarkdownIt
from markdown_it.token import Token


_FENCE_RE = re.compile(r"^\s*(`{3,}|~{3,})")
_SLIDE_SPLIT_RE = re.compile(r"^\s*---\s*$")
_DIRECTIVE_KEYS = frozenset({"depth", "layout", "section", "background", "identifier"})


@dataclass(frozen=True)
class TextSpan:
    """A styled inline text run."""

    text: str
    bold: bool = False
    italic: bool = False
    strike: bool = False
    code: bool = False
    link_url: str | None = None


@dataclass(frozen=True)
class HeadingBlock:
    level: int
    spans: list[TextSpan]


@dataclass(frozen=True)
class ParagraphBlock:
    spans: list[TextSpan]


@dataclass(frozen=True)
class ListItem:
    level: int
    ordered: bool
    spans: list[TextSpan]


@dataclass(frozen=True)
class ListBlock:
    ordered: bool
    items: list[ListItem]


@dataclass(frozen=True)
class CodeBlock:
    lang: str
    code: str


@dataclass(frozen=True)
class QuoteBlock:
    paragraphs: list[list[TextSpan]]


@dataclass(frozen=True)
class TableBlock:
    headers: list[list[TextSpan]]
    rows: list[list[list[TextSpan]]]


@dataclass(frozen=True)
class ImageBlock:
    alt: str
    src: str
    title: str | None = None


SlideBlock = (
    HeadingBlock
    | ParagraphBlock
    | ListBlock
    | CodeBlock
    | QuoteBlock
    | TableBlock
    | ImageBlock
)


@dataclass
class SlideSpec:
    """Parsed representation of a single slide."""

    index: int
    depth: int = 1
    layout: str | None = None
    section: bool = False
    background: str | None = None
    identifier: str | None = None
    note: str | None = None
    blocks: list[SlideBlock] = field(default_factory=list)


@dataclass
class DeckConfig:
    """Global deck configuration from top YAML frontmatter."""

    theme: str = "White"
    template: str | None = None
    locale: str = "en_US"
    width: int = 1920
    height: int = 1080
    hierarchy: str = "section"  # "section" or "flat"
    author: str | None = None
    footer: str | None = None


@dataclass
class Deck:
    config: DeckConfig
    slides: list[SlideSpec]
    warnings: list[str] = field(default_factory=list)


def _split_frontmatter(source: str) -> tuple[dict[str, Any], str]:
    """Extract a single YAML frontmatter block at line 1 if present."""
    lines = source.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, source

    for idx in range(1, len(lines)):
        if lines[idx].strip() in ("---", "..."):
            raw_yaml = "\n".join(lines[1:idx])
            loaded = yaml.safe_load(raw_yaml) or {}
            if not isinstance(loaded, dict):
                raise ValueError("Top YAML frontmatter must be a mapping/object.")
            rest = "\n".join(lines[idx + 1 :])
            return loaded, rest

    return {}, source


def _split_slides_respecting_fences(markdown_body: str) -> list[str]:
    """Split markdown on `---` dividers outside fenced code blocks."""
    chunks: list[list[str]] = [[]]
    fence_char: str | None = None
    fence_len = 0

    for line in markdown_body.splitlines():
        fence_match = _FENCE_RE.match(line)
        if fence_match:
            marker = fence_match.group(1)
            ch, length = marker[0], len(marker)
            if fence_char is None:
                fence_char, fence_len = ch, length
            elif ch == fence_char and length >= fence_len and line.strip() == ch * length:
                fence_char, fence_len = None, 0
            chunks[-1].append(line)
            continue

        if fence_char is None and _SLIDE_SPLIT_RE.match(line):
            chunks.append([])
        else:
            chunks[-1].append(line)

    return ["\n".join(chunk).strip() for chunk in chunks if "\n".join(chunk).strip()]


def _extract_notes_and_directives(
    slide_md: str,
) -> tuple[str, dict[str, Any], list[str]]:
    """Strip presenter notes and HTML comment directives outside fenced code blocks."""
    lines = slide_md.splitlines()
    kept: list[str] = []
    notes: list[str] = []
    directives: dict[str, Any] = {}

    fence_char: str | None = None
    fence_len = 0
    i = 0
    while i < len(lines):
        line = lines[i]
        fence_match = _FENCE_RE.match(line)
        if fence_match:
            marker = fence_match.group(1)
            ch, length = marker[0], len(marker)
            if fence_char is None:
                fence_char, fence_len = ch, length
            elif ch == fence_char and length >= fence_len and line.strip() == ch * length:
                fence_char, fence_len = None, 0
            kept.append(line)
            i += 1
            continue

        if fence_char is not None:
            kept.append(line)
            i += 1
            continue

        if line.strip().lower() in ("::: notes", ":::notes"):
            note_lines: list[str] = []
            i += 1
            while i < len(lines) and lines[i].strip() != ":::":
                note_lines.append(lines[i])
                i += 1
            if i < len(lines) and lines[i].strip() == ":::":
                i += 1
            text = "\n".join(note_lines).strip()
            if text:
                notes.append(text)
            continue

        stripped = line.strip()
        if stripped.startswith("<!--"):
            comment_lines = [line]
            while "-->" not in comment_lines[-1] and i + 1 < len(lines):
                i += 1
                comment_lines.append(lines[i])
            raw_comment = "\n".join(comment_lines)
            inner = raw_comment.strip()
            if inner.startswith("<!--"):
                inner = inner[4:]
            if inner.endswith("-->"):
                inner = inner[:-3]
            inner_stripped = inner.strip()

            if inner_stripped.lower().startswith("note:"):
                note_text = inner_stripped[5:].strip()
                if note_text:
                    notes.append(note_text)
            elif inner_stripped.lower().startswith("note\n"):
                note_text = inner_stripped[5:].strip()
                if note_text:
                    notes.append(note_text)
            else:
                yaml_candidate = re.sub(
                    r"(:\s*)(#[0-9A-Fa-f]{3,6})(?=\s*$)",
                    r'\1"\2"',
                    inner_stripped,
                    flags=re.MULTILINE,
                )
                try:
                    parsed = yaml.safe_load(yaml_candidate)
                except yaml.YAMLError:
                    parsed = None
                if isinstance(parsed, dict) and any(k in _DIRECTIVE_KEYS for k in parsed):
                    for k, v in parsed.items():
                        if k in _DIRECTIVE_KEYS:
                            directives[k] = v
                        elif k == "note" and isinstance(v, str) and v.strip():
                            notes.append(v.strip())
            i += 1
            continue

        kept.append(line)
        i += 1

    return "\n".join(kept).strip(), directives, notes


def _parse_inline_spans(inline_token: Token) -> list[TextSpan]:
    """Convert a markdown-it inline token tree into flat TextSpan runs."""
    spans: list[TextSpan] = []
    bold = False
    italic = False
    strike = False
    link_url: str | None = None

    for child in inline_token.children or []:
        t = child.type
        if t == "strong_open":
            bold = True
        elif t == "strong_close":
            bold = False
        elif t == "em_open":
            italic = True
        elif t == "em_close":
            italic = False
        elif t == "s_open":
            strike = True
        elif t == "s_close":
            strike = False
        elif t == "link_open":
            href = child.attrGet("href")
            link_url = str(href) if href is not None else None
        elif t == "link_close":
            link_url = None
        elif t == "code_inline":
            spans.append(
                TextSpan(
                    text=child.content,
                    bold=bold,
                    italic=italic,
                    strike=strike,
                    code=True,
                    link_url=link_url,
                )
            )
        elif t in ("softbreak", "hardbreak"):
            spans.append(
                TextSpan(
                    text=" " if t == "softbreak" else "\n",
                    bold=bold,
                    italic=italic,
                    strike=strike,
                    link_url=link_url,
                )
            )
        elif t == "text":
            if child.content:
                spans.append(
                    TextSpan(
                        text=child.content,
                        bold=bold,
                        italic=italic,
                        strike=strike,
                        link_url=link_url,
                    )
                )
        elif t == "image":
            alt = child.content or "".join(
                c.content for c in (child.children or []) if c.type == "text"
            )
            src = str(child.attrGet("src") or "")
            if alt:
                spans.append(TextSpan(text=f"[{alt}]({src})", link_url=link_url))

    return spans


def _parse_blocks(slide_body: str, md: MarkdownIt) -> list[SlideBlock]:
    """Parse cleaned slide markdown into SlideBlock objects."""
    if not slide_body.strip():
        return []

    tokens = md.parse(slide_body)
    blocks: list[SlideBlock] = []
    i = 0

    while i < len(tokens):
        tok = tokens[i]

        if tok.type == "heading_open":
            level = int(tok.tag[1])
            inline_tok = tokens[i + 1]
            blocks.append(HeadingBlock(level=level, spans=_parse_inline_spans(inline_tok)))
            i += 3
            continue

        if tok.type == "paragraph_open":
            inline_tok = tokens[i + 1]
            children = inline_tok.children or []
            if len(children) == 1 and children[0].type == "image":
                img = children[0]
                alt = img.content or "".join(
                    c.content for c in (img.children or []) if c.type == "text"
                )
                src = str(img.attrGet("src") or "")
                title = img.attrGet("title")
                blocks.append(
                    ImageBlock(alt=alt, src=src, title=str(title) if title else None)
                )
            else:
                spans = _parse_inline_spans(inline_tok)
                if spans:
                    blocks.append(ParagraphBlock(spans=spans))
            i += 3
            continue

        if tok.type == "fence" or tok.type == "code_block":
            lang = (tok.info or "").strip().split()[0] if tok.info else ""
            blocks.append(CodeBlock(lang=lang, code=tok.content.rstrip("\n")))
            i += 1
            continue

        if tok.type in ("bullet_list_open", "ordered_list_open"):
            top_ordered = tok.type == "ordered_list_open"
            items: list[ListItem] = []
            list_stack: list[bool] = [top_ordered]
            depth = 1
            j = i + 1
            while j < len(tokens) and depth > 0:
                cur = tokens[j]
                if cur.type in ("bullet_list_open", "ordered_list_open"):
                    depth += 1
                    list_stack.append(cur.type == "ordered_list_open")
                elif cur.type in ("bullet_list_close", "ordered_list_close"):
                    depth -= 1
                    list_stack.pop()
                elif cur.type == "inline":
                    items.append(
                        ListItem(
                            level=min(len(list_stack) - 1, 8),
                            ordered=list_stack[-1],
                            spans=_parse_inline_spans(cur),
                        )
                    )
                j += 1
            if items:
                blocks.append(ListBlock(ordered=top_ordered, items=items))
            i = j
            continue

        if tok.type == "blockquote_open":
            quote_paras: list[list[TextSpan]] = []
            depth = 1
            j = i + 1
            while j < len(tokens) and depth > 0:
                cur = tokens[j]
                if cur.type == "blockquote_open":
                    depth += 1
                elif cur.type == "blockquote_close":
                    depth -= 1
                elif cur.type == "inline":
                    spans = _parse_inline_spans(cur)
                    if spans:
                        quote_paras.append(spans)
                j += 1
            if quote_paras:
                blocks.append(QuoteBlock(paragraphs=quote_paras))
            i = j
            continue

        if tok.type == "table_open":
            headers: list[list[TextSpan]] = []
            rows: list[list[list[TextSpan]]] = []
            in_head = False
            current_row: list[list[TextSpan]] = []
            j = i + 1
            while j < len(tokens) and tokens[j].type != "table_close":
                cur = tokens[j]
                if cur.type == "thead_open":
                    in_head = True
                elif cur.type == "thead_close":
                    in_head = False
                elif cur.type == "tr_open":
                    current_row = []
                elif cur.type == "tr_close":
                    if in_head:
                        headers = current_row
                    else:
                        rows.append(current_row)
                elif cur.type in ("th_open", "td_open"):
                    inline_tok = tokens[j + 1]
                    current_row.append(_parse_inline_spans(inline_tok))
                j += 1
            blocks.append(TableBlock(headers=headers, rows=rows))
            i = j + 1
            continue

        i += 1

    return blocks


def _build_deck_config(raw: dict[str, Any]) -> DeckConfig:
    size_val = raw.get("slide_size")
    width, height = 1920, 1080
    if isinstance(size_val, dict):
        width = int(size_val.get("width", 1920))
        height = int(size_val.get("height", 1080))
    elif isinstance(size_val, str) and "x" in size_val.lower():
        parts = size_val.lower().split("x", 1)
        width, height = int(parts[0].strip()), int(parts[1].strip())

    hierarchy = str(raw.get("hierarchy", "section")).lower()
    if hierarchy not in ("section", "flat"):
        raise ValueError(f"Unsupported hierarchy mode {hierarchy!r}; expected 'section' or 'flat'.")

    return DeckConfig(
        theme=str(raw.get("theme", "White")),
        template=str(raw["template"]) if raw.get("template") else None,
        locale=str(raw.get("locale", "en_US")),
        width=width,
        height=height,
        hierarchy=hierarchy,
        author=str(raw["author"]) if raw.get("author") else None,
        footer=str(raw["footer"]) if raw.get("footer") else None,
    )


def parse_markdown(source: str) -> Deck:
    """Parse a Markdown deck into a Deck configuration and SlideSpec list."""
    raw_frontmatter, body = _split_frontmatter(source)
    config = _build_deck_config(raw_frontmatter)
    raw_slides = _split_slides_respecting_fences(body)

    md = MarkdownIt("commonmark").enable(["table", "strikethrough"])
    slides: list[SlideSpec] = []
    warnings: list[str] = []
    in_section = False
    prev_depth = 1

    for idx, raw_slide in enumerate(raw_slides, start=1):
        cleaned_md, directives, notes = _extract_notes_and_directives(raw_slide)
        layout = str(directives["layout"]) if directives.get("layout") is not None else None
        is_section = bool(directives.get("section", False)) or (
            layout is not None and layout.strip().lower() == "section"
        )

        if idx == 1:
            requested_depth = directives.get("depth")
            if requested_depth is not None and int(requested_depth) != 1:
                warnings.append("Slide 1 depth forced to 1 (Keynote requires slide 1 at top level).")
            depth = 1
            if is_section:
                in_section = True
        elif "depth" in directives:
            requested = int(directives["depth"])
            max_allowed = min(6, prev_depth + 1)
            clamped = max(1, min(requested, max_allowed))
            if clamped != requested:
                warnings.append(
                    f"Slide {idx} depth {requested} clamped to {clamped} (prev={prev_depth})."
                )
            depth = clamped
            if depth == 1:
                in_section = is_section
        elif config.hierarchy == "section":
            if is_section:
                depth = 1
                in_section = True
            elif in_section:
                depth = 2
            else:
                depth = 1
        else:
            depth = 1

        prev_depth = depth
        blocks = _parse_blocks(cleaned_md, md)
        slides.append(
            SlideSpec(
                index=idx,
                depth=depth,
                layout=layout,
                section=is_section,
                background=str(directives["background"]) if directives.get("background") else None,
                identifier=str(directives["identifier"]) if directives.get("identifier") else None,
                note="\n\n".join(notes) if notes else None,
                blocks=blocks,
            )
        )

    if not slides:
        raise ValueError("No slides found in Markdown input.")

    return Deck(config=config, slides=slides, warnings=warnings)
