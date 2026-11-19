"""Compile parsed Markdown Deck specs into validated .keyx files via creator-studio-doc-format."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, MutableMapping

from creator_studio_doc_format.authoring import (
    set_slide_placeholder_text,
    slide_from_layout,
    summarize_slide_layouts,
)
from creator_studio_doc_format.core import (
    RGBA,
    AlignmentType,
    BezierPath,
    BodyStrokeProperties,
    CanvasObject,
    Cell,
    CellProperties,
    CharacterProperties,
    Color,
    ColumnHeader,
    Constituent,
    Fill,
    Format,
    Geometry,
    HeaderFooterStrokeProperties,
    Hyperlink,
    HyperlinkField,
    Image,
    IndirectResource,
    Insets,
    KeynoteTheme,
    KeyxDocument,
    LayoutProperties,
    Line,
    LineSpacingKindType,
    LineSpacingType,
    ListLabelType,
    ListLevelType,
    ListNumberLabel,
    ListProperties,
    ListStringLabel,
    NumberFormatType,
    NumberFormatTypeKind,
    OverallProperties,
    Paragraph,
    ParagraphProperties,
    Path as ShapePath,
    Position,
    Presentation,
    Preset,
    Rect,
    Resource,
    Row,
    Shape,
    Size,
    Slide,
    SlideBackground,
    SlideObject,
    SlideTarget,
    StrikethroughType,
    Stroke,
    Table,
    Text,
    Textbox,
    TextRun,
    UnderlineType,
    VerticalAlignmentType,
    VisualProperties,
)
from creator_studio_doc_format.validation import validate_document
from pygments import lex
from pygments.lexers import TextLexer, get_lexer_by_name
from pygments.token import Comment, Error, Generic, Keyword, Name, Number, Operator, Punctuation, String
from pygments.util import ClassNotFound

from .mermaid import MERMAID_MODES, MermaidRenderer, image_dimensions, render_mermaid
from .parser import (
    CalloutBlock,
    CodeBlock,
    Deck,
    HeadingBlock,
    ImageBlock,
    ListBlock,
    MermaidBlock,
    ParagraphBlock,
    QuoteBlock,
    SlideBlock,
    SlideSpec,
    TableBlock,
    TextSpan,
)


_RGBA_WHITE = RGBA(red=1.0, green=1.0, blue=1.0)
_RGBA_INK = RGBA(red=0.10, green=0.11, blue=0.14)
_RGBA_MUTED = RGBA(red=0.38, green=0.41, blue=0.48)
_RGBA_ACCENT = RGBA(red=0.05, green=0.45, blue=0.95)
_RGBA_SECTION_BG = RGBA(red=0.07, green=0.10, blue=0.16)
_RGBA_SECTION_TEXT = RGBA(red=0.96, green=0.97, blue=0.99)
_RGBA_CODE_BG = RGBA(red=0.09, green=0.11, blue=0.16)
_RGBA_CODE_TEXT = RGBA(red=0.90, green=0.94, blue=0.98)
_RGBA_INLINE_CODE_BG = RGBA(red=0.92, green=0.93, blue=0.96)
_RGBA_QUOTE_BG = RGBA(red=0.95, green=0.97, blue=0.99)
_RGBA_RULE = RGBA(red=0.86, green=0.88, blue=0.92)
_RGBA_TBL_HEADER = RGBA(red=0.93, green=0.94, blue=0.97)
_RGBA_TBL_ALT = RGBA(red=0.97, green=0.98, blue=0.99)
_RGBA_TBL_GRID = RGBA(red=0.82, green=0.84, blue=0.89)

_NO_BORDER = Stroke(empty=True)


@dataclass(frozen=True)
class _CalloutStyle:
    label: str
    accent: RGBA
    bg: RGBA
    border: RGBA


def _rgba_hex(hex_color: str) -> RGBA:
    return _parse_hex_color(hex_color)


@dataclass(frozen=True)
class CompileResult:
    output_path: Path
    slide_count: int
    warnings: list[str] = field(default_factory=list)


def _unique_resource_name(resources: MutableMapping[str, bytes], name: str) -> str:
    """Return a collision-free flat Data/ filename."""
    if name not in resources:
        return name
    stem = Path(name).stem
    suffix = Path(name).suffix
    idx = 2
    while f"{stem}-{idx}{suffix}" in resources:
        idx += 1
    return f"{stem}-{idx}{suffix}"


def _parse_hex_color(value: str) -> RGBA:
    hex_str = value.strip().lstrip("#")
    if len(hex_str) == 3:
        hex_str = "".join(ch * 2 for ch in hex_str)
    if len(hex_str) != 6:
        raise ValueError(f"Invalid hex background color {value!r}; expected #RRGGBB.")
    r = int(hex_str[0:2], 16) / 255.0
    g = int(hex_str[2:4], 16) / 255.0
    b = int(hex_str[4:6], 16) / 255.0
    return RGBA(red=r, green=g, blue=b)


_CALLOUT_STYLES: dict[str, _CalloutStyle] = {
    "NOTE": _CalloutStyle(
        label="Note",
        accent=_rgba_hex("#0969DA"),
        bg=_rgba_hex("#EEF5FF"),
        border=_rgba_hex("#B6D4FE"),
    ),
    "TIP": _CalloutStyle(
        label="Tip",
        accent=_rgba_hex("#1A7F37"),
        bg=_rgba_hex("#ECFDF3"),
        border=_rgba_hex("#A7E8BF"),
    ),
    "IMPORTANT": _CalloutStyle(
        label="Important",
        accent=_rgba_hex("#8250DF"),
        bg=_rgba_hex("#F4EFFF"),
        border=_rgba_hex("#D2BDFB"),
    ),
    "WARNING": _CalloutStyle(
        label="Warning",
        accent=_rgba_hex("#9A5B00"),
        bg=_rgba_hex("#FFF8E6"),
        border=_rgba_hex("#FCDCA0"),
    ),
    "CAUTION": _CalloutStyle(
        label="Caution",
        accent=_rgba_hex("#CF222E"),
        bg=_rgba_hex("#FEF0F0"),
        border=_rgba_hex("#F8BABA"),
    ),
}

_PYGMENTS_PALETTE: tuple[tuple[Any, tuple[RGBA, bool, bool]], ...] = (
    (Comment, (RGBA(red=0.52, green=0.58, blue=0.68), False, True)),
    (String, (RGBA(red=0.62, green=0.87, blue=0.49), False, False)),
    (Number, (RGBA(red=0.98, green=0.63, blue=0.42), False, False)),
    (Keyword.Constant, (RGBA(red=0.98, green=0.63, blue=0.42), False, False)),
    (Keyword.Type, (RGBA(red=0.45, green=0.85, blue=0.94), False, False)),
    (Keyword, (RGBA(red=0.78, green=0.57, blue=0.98), True, False)),
    (Operator.Word, (RGBA(red=0.78, green=0.57, blue=0.98), True, False)),
    (Operator, (RGBA(red=0.55, green=0.82, blue=0.96), False, False)),
    (Punctuation, (RGBA(red=0.74, green=0.79, blue=0.87), False, False)),
    (Name.Function, (RGBA(red=0.48, green=0.69, blue=1.00), False, False)),
    (Name.Class, (RGBA(red=0.96, green=0.78, blue=0.45), False, False)),
    (Name.Namespace, (RGBA(red=0.96, green=0.78, blue=0.45), False, False)),
    (Name.Exception, (RGBA(red=0.96, green=0.78, blue=0.45), False, False)),
    (Name.Builtin, (RGBA(red=0.45, green=0.85, blue=0.94), False, False)),
    (Name.Decorator, (RGBA(red=0.45, green=0.85, blue=0.94), False, False)),
    (Name.Tag, (RGBA(red=0.95, green=0.48, blue=0.56), False, False)),
    (Name.Attribute, (RGBA(red=0.96, green=0.78, blue=0.45), False, False)),
    (Generic.Inserted, (RGBA(red=0.52, green=0.89, blue=0.56), False, False)),
    (Generic.Deleted, (RGBA(red=0.98, green=0.48, blue=0.52), False, False)),
    (Generic.Heading, (RGBA(red=0.48, green=0.69, blue=1.00), True, False)),
    (Generic.Subheading, (RGBA(red=0.48, green=0.69, blue=1.00), True, False)),
    (Error, (RGBA(red=0.98, green=0.48, blue=0.52), False, False)),
)


def _rounded_rect_path(width: float, height: float, radius: float) -> ShapePath | None:
    """Build a closed SVG BezierPath for a rounded rectangle in local shape coordinates."""
    w = round(width, 2)
    h = round(height, 2)
    r = round(min(radius, w / 2.0, h / 2.0), 2)
    if r <= 0 or w <= 0 or h <= 0:
        return None
    k = 0.5522847498 * r
    c = round(r - k, 2)
    d = (
        f"M {r} 0 "
        f"L {round(w - r, 2)} 0 "
        f"C {round(w - c, 2)} 0 {w} {c} {w} {r} "
        f"L {w} {round(h - r, 2)} "
        f"C {w} {round(h - c, 2)} {round(w - c, 2)} {h} {round(w - r, 2)} {h} "
        f"L {r} {h} "
        f"C {c} {h} 0 {round(h - c, 2)} 0 {round(h - r, 2)} "
        f"L 0 {r} "
        f"C 0 {c} {c} 0 {r} 0 Z"
    )
    return ShapePath(
        bezier=BezierPath(
            path=d,
            space=Rect(position=Position(x=0, y=0), size=Size(width=w, height=h)),
        )
    )


def _token_style(ttype: Any) -> tuple[RGBA, bool, bool]:
    for token_group, style in _PYGMENTS_PALETTE:
        if ttype in token_group:
            return style
    return (_RGBA_CODE_TEXT, False, False)


def _code_to_paragraphs(code: str, lang: str, font_size: float) -> list[Paragraph]:
    """Lex code with Pygments and return one Paragraph of merged TextRuns per line."""
    try:
        lexer = get_lexer_by_name(lang, stripnl=False) if lang else TextLexer(stripnl=False)
    except ClassNotFound:
        lexer = TextLexer(stripnl=False)

    lines: list[list[tuple[str, RGBA, bool, bool]]] = [[]]
    for ttype, value in lex(code, lexer):
        if not value:
            continue
        color, bold, italic = _token_style(ttype)
        parts = value.split("\n")
        for idx, part in enumerate(parts):
            if idx > 0:
                lines.append([])
            if not part:
                continue
            cur_line = lines[-1]
            if cur_line and cur_line[-1][1:] == (color, bold, italic):
                prev_text, _, _, _ = cur_line[-1]
                cur_line[-1] = (prev_text + part, color, bold, italic)
            else:
                cur_line.append((part, color, bold, italic))

    # Pygments lexers often append a single trailing newline token; drop the empty phantom line
    # if the original code did not end with a newline.
    if len(lines) > 1 and not lines[-1] and not code.endswith("\n"):
        lines.pop()

    paragraphs: list[Paragraph] = []
    for line_runs in lines:
        if not line_runs:
            contents = [
                Constituent(
                    run=TextRun(
                        string="",
                        style=CharacterProperties(
                            font_name="Menlo-Regular",
                            font_size=font_size,
                            character_fill=Fill(color=Color(rgba=_RGBA_CODE_TEXT)),
                        ),
                    )
                )
            ]
        else:
            contents = []
            for text, color, bold, italic in line_runs:
                char_kwargs: dict[str, Any] = {
                    "font_name": "Menlo-Regular",
                    "font_size": font_size,
                    "character_fill": Fill(color=Color(rgba=color)),
                }
                if bold:
                    char_kwargs["bold"] = True
                if italic:
                    char_kwargs["italic"] = True
                contents.append(
                    Constituent(
                        run=TextRun(
                            string=text,
                            style=CharacterProperties(**char_kwargs),
                        )
                    )
                )
        paragraphs.append(
            Paragraph(
                style=ParagraphProperties(
                    alignment=AlignmentType.LEFT,
                    line_spacing=LineSpacingType(value=1.25, kind=LineSpacingKindType.RELATIVE),
                ),
                contents=contents,
            )
        )
    return paragraphs


def _default_presets() -> list[Preset]:
    return [
        Preset(
            identifier="m2k_shape_code",
            display_name="Markdown Code Block",
            properties={
                "fill": {
                    "color": {"rgba": _RGBA_CODE_BG.model_dump(mode="json")},
                },
                "stroke": "empty",
            },
        ),
        Preset(
            identifier="m2k_shape_quote",
            display_name="Markdown Quote Card",
            properties={
                "fill": {
                    "color": {"rgba": _RGBA_QUOTE_BG.model_dump(mode="json")},
                },
                "stroke": "empty",
            },
        ),
        Preset(
            identifier="m2k_shape_accent",
            display_name="Markdown Accent Bar",
            properties={
                "fill": {
                    "color": {"rgba": _RGBA_ACCENT.model_dump(mode="json")},
                },
                "stroke": "empty",
            },
        ),
        Preset(
            identifier="m2k_shape_rule",
            display_name="Markdown Divider Rule",
            properties={
                "fill": {
                    "color": {"rgba": _RGBA_RULE.model_dump(mode="json")},
                },
                "stroke": "empty",
            },
        ),
    ]


def _bullet_list_properties(scale: float) -> ListProperties:
    symbols = ["•", "◦", "▸"]
    levels = [
        ListLevelType(
            label_indent=round(lvl * 36 * scale, 1),
            text_indent=round(1.5 + lvl * 1.2, 2),
            label=ListLabelType(string=ListStringLabel(string=symbols[lvl % len(symbols)])),
        )
        for lvl in range(9)
    ]
    return ListProperties(levels=levels)


def _numbered_list_properties(scale: float) -> ListProperties:
    levels = [
        ListLevelType(
            label_indent=round(lvl * 36 * scale, 1),
            text_indent=round(1.5 + lvl * 1.2, 2),
            label=ListLabelType(
                number=ListNumberLabel(
                    number_format=NumberFormatType(
                        kind=NumberFormatTypeKind.NUMERIC,
                        format=Format.DECIMAL,
                    )
                )
            ),
        )
        for lvl in range(9)
    ]
    return ListProperties(levels=levels)


def _spans_plain_text(spans: list[TextSpan]) -> str:
    return "".join(s.text for s in spans)


def _spans_to_constituents(
    spans: list[TextSpan],
    *,
    base_size: float,
    base_color: RGBA,
    base_bold: bool = False,
    base_italic: bool = False,
    slide_ids: set[str],
    warnings: list[str],
) -> list[Constituent]:
    constituents: list[Constituent] = []
    for span in spans:
        is_link = span.link_url is not None
        color = _RGBA_ACCENT if is_link else base_color
        char_kwargs: dict[str, Any] = {
            "font_name": "Menlo-Regular" if span.code else "Helvetica Neue",
            "font_size": round(base_size * 0.9, 1) if span.code else base_size,
            "character_fill": Fill(color=Color(rgba=color)),
        }
        if base_bold or span.bold:
            char_kwargs["bold"] = True
        if base_italic or span.italic:
            char_kwargs["italic"] = True
        if span.strike:
            char_kwargs["strikethrough"] = StrikethroughType.SINGLE
        if is_link:
            char_kwargs["underline"] = UnderlineType.SINGLE
        if span.code:
            char_kwargs["background_color"] = Color(rgba=_RGBA_INLINE_CODE_BG)

        style = CharacterProperties(**char_kwargs)
        run = TextRun(string=span.text, style=style)

        if span.link_url:
            target = _resolve_hyperlink(span.link_url, slide_ids, warnings)
            if target is not None:
                constituents.append(
                    Constituent(hyperlink=HyperlinkField(target=target, run=run))
                )
                continue

        constituents.append(Constituent(run=run))

    if not constituents:
        constituents.append(Constituent(run=TextRun(string="")))
    return constituents


def _resolve_hyperlink(
    url: str,
    slide_ids: set[str],
    warnings: list[str],
) -> Hyperlink | None:
    if url.startswith("#"):
        target_id = url[1:]
        if target_id in slide_ids:
            return Hyperlink(slide_jump=SlideTarget(by_identifier=target_id))
        warnings.append(f"Slide anchor {url!r} does not match any slide identifier; kept as styled text.")
        return None
    if url.startswith("file:"):
        return Hyperlink(file=url)
    if "://" in url or url.startswith(("mailto:", "tel:")):
        return Hyperlink(url=url)
    warnings.append(f"Relative/unrecognized link {url!r} is not an absolute URL; kept as styled text.")
    return None


def _estimate_lines(text: str, box_width: float, font_size: float) -> int:
    chars_per_line = max(12, int(box_width / max(font_size * 0.56, 1.0)))
    total = 0
    for para in text.splitlines() or [""]:
        total += max(1, math.ceil(len(para) / chars_per_line))
    return total


def _make_textbox(
    *,
    x: float,
    y: float,
    width: float,
    height: float,
    paragraphs: list[Paragraph],
    alignment: AlignmentType = AlignmentType.LEFT,
    vertical_alignment: VerticalAlignmentType = VerticalAlignmentType.TOP,
    shrink_to_fit: bool = True,
) -> SlideObject:
    return SlideObject(
        object=CanvasObject(
            textbox=Textbox(
                stroke=_NO_BORDER,
                geometry=Geometry(
                    position=Position(x=round(x, 1), y=round(y, 1)),
                    size=Size(width=round(width, 1), height=round(height, 1)),
                ),
                layout_properties=LayoutProperties(
                    vertical_alignment=vertical_alignment,
                    shrink_to_fit=shrink_to_fit,
                ),
                paragraph_properties=ParagraphProperties(
                    alignment=alignment,
                    line_spacing=LineSpacingType(value=1.18, kind=LineSpacingKindType.RELATIVE),
                ),
                text=Text(paragraphs=paragraphs),
            )
        )
    )


def _fit_image_box(
    natural_dims: tuple[float, float] | None,
    *,
    x: float,
    y: float,
    max_width: float,
    max_height: float,
) -> tuple[float, float, float, float]:
    """Fit natural image dimensions within (max_width, max_height) and center horizontally."""
    if natural_dims and natural_dims[0] > 0 and natural_dims[1] > 0:
        nat_w, nat_h = natural_dims
    else:
        nat_w, nat_h = 16.0, 9.0

    fit_scale = min(max_width / nat_w, max_height / nat_h)
    img_w = max(1.0, nat_w * fit_scale)
    img_h = max(1.0, nat_h * fit_scale)
    img_x = x + max(0.0, (max_width - img_w) / 2.0)
    return img_x, y, img_w, img_h


def _render_image_object(
    data: bytes,
    *,
    preferred_name: str,
    ext: str,
    description: str | None,
    x: float,
    y: float,
    width: float,
    max_bottom: float,
    scale: float,
    is_last_block: bool,
    resources: MutableMapping[str, bytes],
) -> tuple[SlideObject, float]:
    """Bundle image bytes into resources and return a centered aspect-preserving Image."""
    filename = _unique_resource_name(resources, preferred_name)
    resources[filename] = data
    avail_h = max(200 * scale, max_bottom - y)
    max_img_h = avail_h if is_last_block else min(460 * scale, avail_h)
    dims = image_dimensions(data, ext=ext)
    img_x, img_y, img_w, img_h = _fit_image_box(
        dims,
        x=x,
        y=y,
        max_width=width,
        max_height=max_img_h,
    )
    obj = SlideObject(
        object=CanvasObject(
            image=Image(
                description=description,
                aspect_ratio_locked=True,
                stroke=_NO_BORDER,
                geometry=Geometry(
                    position=Position(x=round(img_x, 1), y=round(img_y, 1)),
                    size=Size(width=round(img_w, 1), height=round(img_h, 1)),
                ),
                resource=Resource(
                    indirect=IndirectResource(filename=filename)
                ),
            )
        )
    )
    return obj, img_h + 24 * scale


def _render_code_card(
    code: str,
    lang: str,
    *,
    x: float,
    y: float,
    width: float,
    max_bottom: float,
    scale: float,
) -> tuple[list[SlideObject], float]:
    font_size = round(24 * scale, 1)
    code_lines = code.splitlines() or [""]
    inner_w = max(120 * scale, width - 52 * scale)
    wrapped_lines = sum(_estimate_lines(line or " ", inner_w, font_size) for line in code_lines)
    height = min(
        max(100 * scale, wrapped_lines * font_size * 1.42 + 44 * scale),
        max(140 * scale, max_bottom - y),
    )
    code_paragraphs = _code_to_paragraphs(code, lang, font_size)
    card = SlideObject(
        object=CanvasObject(
            shape=Shape(
                preset_identifier="m2k_shape_code",
                path=_rounded_rect_path(width, height, 10 * scale),
                stroke=_NO_BORDER,
                geometry=Geometry(
                    position=Position(x=round(x, 1), y=round(y, 1)),
                    size=Size(width=round(width, 1), height=round(height, 1)),
                ),
                layout_properties=LayoutProperties(
                    padding=Insets(
                        top=round(22 * scale, 1),
                        bottom=round(22 * scale, 1),
                        left=round(26 * scale, 1),
                        right=round(26 * scale, 1),
                    ),
                    vertical_alignment=VerticalAlignmentType.TOP,
                    shrink_to_fit=True,
                ),
                text=Text(paragraphs=code_paragraphs),
            )
        )
    )
    return [card], height + 24 * scale


def _render_block(
    block: SlideBlock,
    *,
    x: float,
    y: float,
    width: float,
    max_bottom: float,
    scale: float,
    text_color: RGBA,
    muted_color: RGBA,
    slide_ids: set[str],
    warnings: list[str],
    resources: MutableMapping[str, bytes],
    base_dir: Path,
    mermaid_mode: str = "auto",
    mermaid_renderer: MermaidRenderer | None = None,
    mermaid_counter: list[int] | None = None,
    is_last_block: bool = True,
) -> tuple[list[SlideObject], float]:
    """Render one SlideBlock into SlideObject(s) and return the vertical height consumed."""
    objects: list[SlideObject] = []

    if isinstance(block, HeadingBlock):
        size_map = {1: 68, 2: 48, 3: 38, 4: 34, 5: 32, 6: 32}
        font_size = round(size_map.get(block.level, 34) * scale, 1)
        plain = _spans_plain_text(block.spans)
        lines = _estimate_lines(plain, width, font_size)
        height = max(font_size * 1.4, lines * font_size * 1.25 + 10 * scale)
        para = Paragraph(
            contents=_spans_to_constituents(
                block.spans,
                base_size=font_size,
                base_color=text_color,
                base_bold=True,
                slide_ids=slide_ids,
                warnings=warnings,
            )
        )
        objects.append(_make_textbox(x=x, y=y, width=width, height=height, paragraphs=[para]))
        return objects, height + 18 * scale

    if isinstance(block, ParagraphBlock):
        font_size = round(36 * scale, 1)
        plain = _spans_plain_text(block.spans)
        lines = _estimate_lines(plain, width, font_size)
        height = max(font_size * 1.5, lines * font_size * 1.32 + 8 * scale)
        para = Paragraph(
            contents=_spans_to_constituents(
                block.spans,
                base_size=font_size,
                base_color=text_color,
                slide_ids=slide_ids,
                warnings=warnings,
            )
        )
        objects.append(_make_textbox(x=x, y=y, width=width, height=height, paragraphs=[para]))
        return objects, height + 20 * scale

    if isinstance(block, ListBlock):
        font_size = round(34 * scale, 1)
        list_style = (
            _numbered_list_properties(scale)
            if block.ordered
            else _bullet_list_properties(scale)
        )
        paragraphs: list[Paragraph] = []
        total_lines = 0
        for idx, item in enumerate(block.items):
            plain = _spans_plain_text(item.spans)
            indent_px = item.level * 36 * scale
            total_lines += _estimate_lines(plain, max(width - indent_px - 40 * scale, 200), font_size)
            paragraphs.append(
                Paragraph(
                    list_level=item.level,
                    list_style=list_style if idx == 0 else None,
                    style=ParagraphProperties(space_after=round(10 * scale, 1)),
                    contents=_spans_to_constituents(
                        item.spans,
                        base_size=font_size,
                        base_color=text_color,
                        slide_ids=slide_ids,
                        warnings=warnings,
                    ),
                )
            )
        height = max(80 * scale, total_lines * font_size * 1.35 + len(block.items) * 14 * scale)
        height = min(height, max(120 * scale, max_bottom - y))
        objects.append(_make_textbox(x=x, y=y, width=width, height=height, paragraphs=paragraphs))
        return objects, height + 22 * scale

    if isinstance(block, QuoteBlock):
        font_size = round(34 * scale, 1)
        total_lines = sum(
            _estimate_lines(_spans_plain_text(p), width - 72 * scale, font_size)
            for p in block.paragraphs
        )
        height = max(84 * scale, total_lines * font_size * 1.35 + 36 * scale)
        paras = [
            Paragraph(
                style=ParagraphProperties(space_after=round(8 * scale, 1)),
                contents=_spans_to_constituents(
                    p,
                    base_size=font_size,
                    base_color=muted_color,
                    base_italic=True,
                    slide_ids=slide_ids,
                    warnings=warnings,
                ),
            )
            for p in block.paragraphs
        ]
        card = SlideObject(
            object=CanvasObject(
                shape=Shape(
                    preset_identifier="m2k_shape_quote",
                    stroke=_NO_BORDER,
                    geometry=Geometry(
                        position=Position(x=round(x, 1), y=round(y, 1)),
                        size=Size(width=round(width, 1), height=round(height, 1)),
                    ),
                    layout_properties=LayoutProperties(
                        padding=Insets(
                            top=round(18 * scale, 1),
                            bottom=round(18 * scale, 1),
                            left=round(34 * scale, 1),
                            right=round(24 * scale, 1),
                        ),
                        vertical_alignment=VerticalAlignmentType.CENTER,
                        shrink_to_fit=True,
                    ),
                    text=Text(paragraphs=paras),
                )
            )
        )
        bar = SlideObject(
            object=CanvasObject(
                shape=Shape(
                    preset_identifier="m2k_shape_accent",
                    stroke=_NO_BORDER,
                    geometry=Geometry(
                        position=Position(x=round(x, 1), y=round(y, 1)),
                        size=Size(width=round(8 * scale, 1), height=round(height, 1)),
                    ),
                )
            )
        )
        objects.extend([card, bar])
        return objects, height + 22 * scale

    if isinstance(block, CalloutBlock):
        style = _CALLOUT_STYLES.get(block.kind.upper(), _CALLOUT_STYLES["NOTE"])
        header_text = block.title or style.label
        header_size = round(28 * scale, 1)
        body_size = round(28 * scale, 1)
        inner_w = max(140 * scale, width - 64 * scale)
        header_lines = _estimate_lines(header_text, inner_w, header_size)
        body_lines = sum(
            _estimate_lines(_spans_plain_text(p), inner_w, body_size)
            for p in block.paragraphs
        )
        extra_gap = len(block.paragraphs) * 8 * scale
        raw_h = (
            header_lines * header_size * 1.3
            + body_lines * body_size * 1.35
            + extra_gap
            + 40 * scale
        )
        height = min(max(92 * scale, raw_h), max(120 * scale, max_bottom - y))

        paras: list[Paragraph] = [
            Paragraph(
                style=ParagraphProperties(
                    space_after=round(8 * scale, 1) if block.paragraphs else 0.0,
                ),
                contents=[
                    Constituent(
                        run=TextRun(
                            string=header_text,
                            style=CharacterProperties(
                                font_name="Helvetica Neue",
                                font_size=header_size,
                                bold=True,
                                character_fill=Fill(color=Color(rgba=style.accent)),
                            ),
                        )
                    )
                ],
            )
        ]
        for idx, p in enumerate(block.paragraphs):
            paras.append(
                Paragraph(
                    style=ParagraphProperties(
                        space_after=round(8 * scale, 1) if idx < len(block.paragraphs) - 1 else 0.0,
                    ),
                    contents=_spans_to_constituents(
                        p,
                        base_size=body_size,
                        base_color=_RGBA_INK,
                        slide_ids=slide_ids,
                        warnings=warnings,
                    ),
                )
            )

        card = SlideObject(
            object=CanvasObject(
                shape=Shape(
                    path=_rounded_rect_path(width, height, 12 * scale),
                    fill=Fill(color=Color(rgba=style.bg)),
                    stroke=Stroke(line=Line(color=Color(rgba=style.border), width=1.0)),
                    geometry=Geometry(
                        position=Position(x=round(x, 1), y=round(y, 1)),
                        size=Size(width=round(width, 1), height=round(height, 1)),
                    ),
                    layout_properties=LayoutProperties(
                        padding=Insets(
                            top=round(18 * scale, 1),
                            bottom=round(18 * scale, 1),
                            left=round(36 * scale, 1),
                            right=round(24 * scale, 1),
                        ),
                        vertical_alignment=VerticalAlignmentType.TOP,
                        shrink_to_fit=True,
                    ),
                    text=Text(paragraphs=paras),
                )
            )
        )
        bar_w = round(6 * scale, 1)
        bar_h = round(max(24 * scale, height - 32 * scale), 1)
        bar = SlideObject(
            object=CanvasObject(
                shape=Shape(
                    path=_rounded_rect_path(bar_w, bar_h, bar_w / 2.0),
                    fill=Fill(color=Color(rgba=style.accent)),
                    stroke=_NO_BORDER,
                    geometry=Geometry(
                        position=Position(x=round(x + 14 * scale, 1), y=round(y + 16 * scale, 1)),
                        size=Size(width=bar_w, height=bar_h),
                    ),
                )
            )
        )
        objects.extend([card, bar])
        return objects, height + 22 * scale

    if isinstance(block, CodeBlock):
        return _render_code_card(
            block.code,
            block.lang,
            x=x,
            y=y,
            width=width,
            max_bottom=max_bottom,
            scale=scale,
        )

    if isinstance(block, MermaidBlock):
        rendered = render_mermaid(
            block.code,
            mode=mermaid_mode,
            renderer=mermaid_renderer,
            warnings=warnings,
        )
        if rendered is None:
            return _render_code_card(
                block.code,
                "mermaid",
                x=x,
                y=y,
                width=width,
                max_bottom=max_bottom,
                scale=scale,
            )
        img_bytes, ext = rendered
        if mermaid_counter is not None:
            mermaid_counter[0] += 1
            num = mermaid_counter[0]
        else:
            num = 1
        img_obj, consumed_h = _render_image_object(
            img_bytes,
            preferred_name=f"mermaid-{num}{ext}",
            ext=ext,
            description="Mermaid diagram",
            x=x,
            y=y,
            width=width,
            max_bottom=max_bottom,
            scale=scale,
            is_last_block=is_last_block,
            resources=resources,
        )
        return [img_obj], consumed_h

    if isinstance(block, TableBlock):
        num_cols = max(
            len(block.headers),
            max((len(r) for r in block.rows), default=0),
            1,
        )
        col_width = round(width / num_cols, 1)
        header_row_height = round(68 * scale, 1)
        body_row_height = round(64 * scale, 1)
        total_rows = (1 if block.headers else 0) + len(block.rows)
        table_height = round(
            (header_row_height if block.headers else 0) + len(block.rows) * body_row_height,
            1,
        )

        col_headers = [
            ColumnHeader(
                column_identifier=f"col_{c_idx}",
                title=_spans_plain_text(block.headers[c_idx]) if c_idx < len(block.headers) else f"Col {c_idx + 1}",
                column_width=col_width,
            )
            for c_idx in range(num_cols)
        ]

        rows: list[Row] = []
        if block.headers:
            hdr_cells = [
                Cell(
                    value=_spans_plain_text(block.headers[c_idx])
                    if c_idx < len(block.headers)
                    else ""
                )
                for c_idx in range(num_cols)
            ]
            rows.append(
                Row(row_identifier="row_hdr", row_height=header_row_height, cells=hdr_cells)
            )

        for r_idx, row_spans in enumerate(block.rows, start=1):
            cells = [
                Cell(
                    value=_spans_plain_text(row_spans[c_idx])
                    if c_idx < len(row_spans)
                    else ""
                )
                for c_idx in range(num_cols)
            ]
            rows.append(
                Row(row_identifier=f"row_{r_idx}", row_height=body_row_height, cells=cells)
            )

        grid_stroke = Stroke(line=Line(color=Color(rgba=_RGBA_TBL_GRID), width=0.75))
        border_stroke = Stroke(line=Line(color=Color(rgba=_RGBA_TBL_GRID), width=1.25))
        visual = VisualProperties(
            overall_properties=OverallProperties(
                has_alternating_rows=True,
                alternating_rows_fill=Fill(color=Color(rgba=_RGBA_TBL_ALT)),
            ),
            body_stroke_properties=BodyStrokeProperties(
                horizontal_stroke=grid_stroke,
                vertical_stroke=grid_stroke,
                horizontal_border_stroke=border_stroke,
                vertical_border_stroke=border_stroke,
            ),
            header_row_cell_properties=CellProperties(
                fill=Fill(color=Color(rgba=_RGBA_TBL_HEADER)),
                vertical_alignment=VerticalAlignmentType.CENTER,
                padding=Insets(
                    top=round(10 * scale, 1),
                    bottom=round(10 * scale, 1),
                    left=round(14 * scale, 1),
                    right=round(14 * scale, 1),
                ),
            ),
            header_row_text_properties=ParagraphProperties(
                alignment=AlignmentType.LEFT,
                character_properties=CharacterProperties(
                    font_name="Helvetica Neue",
                    font_size=round(28 * scale, 1),
                    bold=True,
                    character_fill=Fill(color=Color(rgba=_RGBA_INK)),
                ),
            ),
            header_row_stroke_properties=HeaderFooterStrokeProperties(
                separator_stroke=border_stroke,
                separator_visible=True,
            ),
            body_cell_properties=CellProperties(
                vertical_alignment=VerticalAlignmentType.CENTER,
                padding=Insets(
                    top=round(10 * scale, 1),
                    bottom=round(10 * scale, 1),
                    left=round(14 * scale, 1),
                    right=round(14 * scale, 1),
                ),
            ),
            body_text_properties=ParagraphProperties(
                alignment=AlignmentType.LEFT,
                character_properties=CharacterProperties(
                    font_name="Helvetica Neue",
                    font_size=round(26 * scale, 1),
                    character_fill=Fill(color=Color(rgba=_RGBA_INK)),
                ),
            ),
        )

        objects.append(
            SlideObject(
                object=CanvasObject(
                    table=Table(
                        geometry=Geometry(
                            position=Position(x=round(x, 1), y=round(y, 1)),
                            size=Size(width=round(width, 1), height=table_height),
                        ),
                        number_of_columns=num_cols,
                        number_of_rows=total_rows,
                        number_of_header_rows=1 if block.headers else 0,
                        visual_properties=visual,
                        column_headers=col_headers,
                        content=rows,
                    )
                )
            )
        )
        return objects, table_height + 24 * scale

    if isinstance(block, ImageBlock):
        img_path = (base_dir / block.src).resolve() if not Path(block.src).is_absolute() else Path(block.src)
        if not img_path.is_file():
            warnings.append(f"Image not found: {block.src!r}; inserted placeholder note instead.")
            fallback = Paragraph(
                contents=[
                    Constituent(
                        run=TextRun(
                            string=f"[Missing image: {block.src}]",
                            style=CharacterProperties(
                                font_size=round(28 * scale, 1),
                                italic=True,
                                character_fill=Fill(color=Color(rgba=muted_color)),
                            ),
                        )
                    )
                ]
            )
            h = 72 * scale
            objects.append(_make_textbox(x=x, y=y, width=width, height=h, paragraphs=[fallback]))
            return objects, h + 16 * scale

        img_bytes = img_path.read_bytes()
        img_obj, consumed_h = _render_image_object(
            img_bytes,
            preferred_name=img_path.name,
            ext=img_path.suffix,
            description=block.alt or None,
            x=x,
            y=y,
            width=width,
            max_bottom=max_bottom,
            scale=scale,
            is_last_block=is_last_block,
            resources=resources,
        )
        return [img_obj], consumed_h

    return objects, 0.0


def _render_block_sequence(
    blocks: list[SlideBlock],
    *,
    slide_index: int,
    x: float,
    y_start: float,
    width: float,
    max_bottom: float,
    scale: float,
    text_color: RGBA,
    muted_color: RGBA,
    slide_ids: set[str],
    warnings: list[str],
    resources: MutableMapping[str, bytes],
    base_dir: Path,
    mermaid_mode: str,
    mermaid_renderer: MermaidRenderer | None,
    mermaid_counter: list[int],
    region_label: str = "content",
) -> tuple[list[SlideObject], float]:
    """Lay out a vertical sequence of blocks within a bounded box."""
    objects: list[SlideObject] = []
    y_cursor = y_start
    total = len(blocks)
    for idx, block in enumerate(blocks):
        if y_cursor >= max_bottom - 40 * scale:
            warnings.append(
                f"Slide {slide_index} {region_label} exceeded vertical canvas budget; remaining blocks may overlap."
            )
            y_cursor = max_bottom - 120 * scale
        objs, consumed_h = _render_block(
            block,
            x=x,
            y=y_cursor,
            width=width,
            max_bottom=max_bottom,
            scale=scale,
            text_color=text_color,
            muted_color=muted_color,
            slide_ids=slide_ids,
            warnings=warnings,
            resources=resources,
            base_dir=base_dir,
            mermaid_mode=mermaid_mode,
            mermaid_renderer=mermaid_renderer,
            mermaid_counter=mermaid_counter,
            is_last_block=(idx == total - 1),
        )
        objects.extend(objs)
        y_cursor += consumed_h
    return objects, y_cursor


def _compile_standard_slide(
    spec: SlideSpec,
    *,
    deck: Deck,
    slide_ids: set[str],
    warnings: list[str],
    resources: MutableMapping[str, bytes],
    base_dir: Path,
    mermaid_mode: str = "auto",
    mermaid_renderer: MermaidRenderer | None = None,
    mermaid_counter: list[int] | None = None,
    canvas_size: tuple[float, float] | None = None,
) -> Slide:
    if canvas_size is not None:
        width, height = float(canvas_size[0]), float(canvas_size[1])
    else:
        width = float(deck.config.width)
        height = float(deck.config.height)
    scale = min(width / 1920.0, height / 1080.0)
    margin_x = 120.0 * scale
    content_w = width - 2 * margin_x
    max_bottom = height - (90.0 * scale if deck.config.footer else 64.0 * scale)
    counter = mermaid_counter if mermaid_counter is not None else [0]

    is_cover = (
        spec.index == 1
        and not spec.section
        and spec.columns is None
        and len(spec.blocks) <= 3
        and all(isinstance(b, (HeadingBlock, ParagraphBlock)) for b in spec.blocks)
    )
    is_hero_slide = spec.section or is_cover

    text_color = _RGBA_SECTION_TEXT if spec.section else _RGBA_INK
    muted_color = (
        RGBA(red=0.72, green=0.78, blue=0.88) if spec.section else _RGBA_MUTED
    )

    background: SlideBackground | None = None
    if spec.background:
        background = SlideBackground(
            standard=Fill(color=Color(rgba=_parse_hex_color(spec.background)))
        )
    elif spec.section:
        background = SlideBackground(
            standard=Fill(color=Color(rgba=_RGBA_SECTION_BG))
        )

    slide_objects: list[SlideObject] = []
    blocks = list(spec.blocks)

    if is_hero_slide:
        y_cursor = height * 0.34
        if spec.section:
            slide_objects.append(
                SlideObject(
                    object=CanvasObject(
                        shape=Shape(
                            preset_identifier="m2k_shape_accent",
                            stroke=_NO_BORDER,
                            geometry=Geometry(
                                position=Position(x=round(margin_x, 1), y=round(y_cursor - 28 * scale, 1)),
                                size=Size(width=round(96 * scale, 1), height=round(8 * scale, 1)),
                            ),
                        )
                    )
                )
            )
        for idx, block in enumerate(blocks):
            if isinstance(block, HeadingBlock):
                font_size = round((104 if idx == 0 else 44) * scale, 1)
                plain = _spans_plain_text(block.spans)
                lines = _estimate_lines(plain, content_w, font_size)
                box_h = max(font_size * 1.35, lines * font_size * 1.2 + 12 * scale)
                para = Paragraph(
                    contents=_spans_to_constituents(
                        block.spans,
                        base_size=font_size,
                        base_color=text_color,
                        base_bold=(idx == 0),
                        slide_ids=slide_ids,
                        warnings=warnings,
                    )
                )
                slide_objects.append(
                    _make_textbox(
                        x=margin_x,
                        y=y_cursor,
                        width=content_w,
                        height=box_h,
                        paragraphs=[para],
                    )
                )
                y_cursor += box_h + 20 * scale
            elif isinstance(block, ParagraphBlock):
                font_size = round(38 * scale, 1)
                plain = _spans_plain_text(block.spans)
                lines = _estimate_lines(plain, content_w, font_size)
                box_h = max(font_size * 1.4, lines * font_size * 1.3 + 10 * scale)
                para = Paragraph(
                    contents=_spans_to_constituents(
                        block.spans,
                        base_size=font_size,
                        base_color=muted_color,
                        slide_ids=slide_ids,
                        warnings=warnings,
                    )
                )
                slide_objects.append(
                    _make_textbox(
                        x=margin_x,
                        y=y_cursor,
                        width=content_w,
                        height=box_h,
                        paragraphs=[para],
                    )
                )
                y_cursor += box_h + 16 * scale

        if is_cover and deck.config.author:
            author_para = Paragraph(
                contents=[
                    Constituent(
                        run=TextRun(
                            string=deck.config.author,
                            style=CharacterProperties(
                                font_name="Helvetica Neue",
                                font_size=round(26 * scale, 1),
                                character_fill=Fill(color=Color(rgba=muted_color)),
                            ),
                        )
                    )
                ]
            )
            slide_objects.append(
                _make_textbox(
                    x=margin_x,
                    y=height - 140 * scale,
                    width=content_w,
                    height=50 * scale,
                    paragraphs=[author_para],
                )
            )
    else:
        y_cursor = 76.0 * scale
        if blocks and isinstance(blocks[0], HeadingBlock):
            hdr = blocks.pop(0)
            hdr_objs, hdr_h = _render_block(
                hdr,
                x=margin_x,
                y=y_cursor,
                width=content_w,
                max_bottom=max_bottom,
                scale=scale,
                text_color=text_color,
                muted_color=muted_color,
                slide_ids=slide_ids,
                warnings=warnings,
                resources=resources,
                base_dir=base_dir,
                mermaid_mode=mermaid_mode,
                mermaid_renderer=mermaid_renderer,
                mermaid_counter=counter,
                is_last_block=(not blocks and spec.columns is None),
            )
            slide_objects.extend(hdr_objs)
            y_cursor += hdr_h
            slide_objects.append(
                SlideObject(
                    object=CanvasObject(
                        shape=Shape(
                            preset_identifier="m2k_shape_rule",
                            stroke=_NO_BORDER,
                            geometry=Geometry(
                                position=Position(x=round(margin_x, 1), y=round(y_cursor - 6 * scale, 1)),
                                size=Size(width=round(content_w, 1), height=round(2 * scale, 1)),
                            ),
                        )
                    )
                )
            )
            y_cursor += 22 * scale

        if blocks:
            top_objs, y_cursor = _render_block_sequence(
                blocks,
                slide_index=spec.index,
                x=margin_x,
                y_start=y_cursor,
                width=content_w,
                max_bottom=max_bottom,
                scale=scale,
                text_color=text_color,
                muted_color=muted_color,
                slide_ids=slide_ids,
                warnings=warnings,
                resources=resources,
                base_dir=base_dir,
                mermaid_mode=mermaid_mode,
                mermaid_renderer=mermaid_renderer,
                mermaid_counter=counter,
            )
            slide_objects.extend(top_objs)

        if spec.columns is not None:
            left_blocks, right_blocks = spec.columns
            gutter = 56.0 * scale
            usable_w = content_w - gutter
            left_w = round(usable_w * spec.column_ratio, 1)
            right_w = round(usable_w - left_w, 1)
            left_x = round(margin_x, 1)
            right_x = round(margin_x + left_w + gutter, 1)

            left_objs, _ = _render_block_sequence(
                left_blocks,
                slide_index=spec.index,
                x=left_x,
                y_start=y_cursor,
                width=left_w,
                max_bottom=max_bottom,
                scale=scale,
                text_color=text_color,
                muted_color=muted_color,
                slide_ids=slide_ids,
                warnings=warnings,
                resources=resources,
                base_dir=base_dir,
                mermaid_mode=mermaid_mode,
                mermaid_renderer=mermaid_renderer,
                mermaid_counter=counter,
                region_label="left column",
            )
            right_objs, _ = _render_block_sequence(
                right_blocks,
                slide_index=spec.index,
                x=right_x,
                y_start=y_cursor,
                width=right_w,
                max_bottom=max_bottom,
                scale=scale,
                text_color=text_color,
                muted_color=muted_color,
                slide_ids=slide_ids,
                warnings=warnings,
                resources=resources,
                base_dir=base_dir,
                mermaid_mode=mermaid_mode,
                mermaid_renderer=mermaid_renderer,
                mermaid_counter=counter,
                region_label="right column",
            )
            slide_objects.extend(left_objs)
            slide_objects.extend(right_objs)

    if deck.config.footer and not spec.section:
        footer_para = Paragraph(
            contents=[
                Constituent(
                    run=TextRun(
                        string=deck.config.footer,
                        style=CharacterProperties(
                            font_name="Helvetica Neue",
                            font_size=round(18 * scale, 1),
                            character_fill=Fill(color=Color(rgba=muted_color)),
                        ),
                    )
                )
            ]
        )
        slide_objects.append(
            _make_textbox(
                x=margin_x,
                y=height - 58 * scale,
                width=content_w,
                height=36 * scale,
                paragraphs=[footer_para],
                alignment=AlignmentType.RIGHT,
            )
        )

    slide_kwargs: dict[str, Any] = {
        "identifier": spec.identifier,
        "depth": spec.depth,
        "contents": slide_objects,
        "note": Text(string=spec.note) if spec.note else None,
    }
    if background is not None:
        slide_kwargs["background"] = background

    return Slide(**slide_kwargs)


def _compile_template_slide(
    spec: SlideSpec,
    *,
    doc: KeyxDocument,
    deck: Deck,
    slide_ids: set[str],
    warnings: list[str],
    base_dir: Path,
    mermaid_mode: str = "auto",
    mermaid_renderer: MermaidRenderer | None = None,
    mermaid_counter: list[int] | None = None,
) -> Slide:
    """Create a slide from a template .keyx theme layout when available."""
    size = doc.presentation.slide_size or Size(width=deck.config.width, height=deck.config.height)
    if spec.columns is not None:
        return _compile_standard_slide(
            spec,
            deck=deck,
            slide_ids=slide_ids,
            warnings=warnings,
            resources=doc.resources,
            base_dir=base_dir,
            mermaid_mode=mermaid_mode,
            mermaid_renderer=mermaid_renderer,
            mermaid_counter=mermaid_counter,
            canvas_size=(size.width, size.height),
        )

    theme = doc.presentation.theme
    layouts = summarize_slide_layouts(theme) if theme and theme.slide_layouts else {}
    layout_lookup = {name.lower(): name for name in layouts}

    chosen_layout: str | None = None
    if spec.layout and spec.layout.lower() in layout_lookup:
        chosen_layout = layout_lookup[spec.layout.lower()]
    elif spec.layout:
        warnings.append(
            f"Slide {spec.index} requested layout {spec.layout!r} not in template; falling back."
        )

    if chosen_layout is None:
        if spec.index == 1 and "title" in layout_lookup:
            chosen_layout = layout_lookup["title"]
        elif spec.section and "section" in layout_lookup:
            chosen_layout = layout_lookup["section"]
        elif "title & bullets" in layout_lookup:
            chosen_layout = layout_lookup["title & bullets"]
        elif layouts:
            chosen_layout = next(iter(layouts))

    if chosen_layout is None or theme is None:
        return _compile_standard_slide(
            spec,
            deck=deck,
            slide_ids=slide_ids,
            warnings=warnings,
            resources=doc.resources,
            base_dir=base_dir,
            mermaid_mode=mermaid_mode,
            mermaid_renderer=mermaid_renderer,
            mermaid_counter=mermaid_counter,
            canvas_size=(size.width, size.height),
        )

    slide = slide_from_layout(theme, chosen_layout)
    slide.identifier = spec.identifier
    slide.depth = spec.depth
    if spec.note:
        slide.note = Text(string=spec.note)
    if spec.background:
        slide.background = SlideBackground(
            standard=Fill(color=Color(rgba=_parse_hex_color(spec.background)))
        )

    placeholders = {p.name.upper(): p.name for p in layouts[chosen_layout]}
    remaining_blocks = list(spec.blocks)

    if remaining_blocks and isinstance(remaining_blocks[0], HeadingBlock) and "TITLE" in placeholders:
        title_block = remaining_blocks.pop(0)
        set_slide_placeholder_text(
            slide, placeholders["TITLE"], _spans_plain_text(title_block.spans)
        )

    if "SUBTITLE" in placeholders:
        if remaining_blocks and isinstance(remaining_blocks[0], (HeadingBlock, ParagraphBlock)):
            sub_block = remaining_blocks.pop(0)
            set_slide_placeholder_text(
                slide, placeholders["SUBTITLE"], _spans_plain_text(sub_block.spans)
            )
        else:
            set_slide_placeholder_text(slide, placeholders["SUBTITLE"], None)

    if "BODY" in placeholders:
        if remaining_blocks and isinstance(remaining_blocks[0], ListBlock):
            first_list = remaining_blocks.pop(0)
            lines = [_spans_plain_text(item.spans) for item in first_list.items]
            levels = [item.level for item in first_list.items]
            set_slide_placeholder_text(
                slide, placeholders["BODY"], lines, list_levels=levels
            )
        elif remaining_blocks and isinstance(remaining_blocks[0], (ParagraphBlock, HeadingBlock)):
            first_text = remaining_blocks.pop(0)
            set_slide_placeholder_text(
                slide, placeholders["BODY"], _spans_plain_text(first_text.spans)
            )
        else:
            set_slide_placeholder_text(slide, placeholders["BODY"], None)

    if slide.contents is None:
        slide.contents = []

    if remaining_blocks:
        scale = min(size.width / 1920.0, size.height / 1080.0)
        counter = mermaid_counter if mermaid_counter is not None else [0]
        extra_objs, _ = _render_block_sequence(
            remaining_blocks,
            slide_index=spec.index,
            x=120.0 * scale,
            y_start=260.0 * scale,
            width=size.width - 240.0 * scale,
            max_bottom=size.height - 80.0 * scale,
            scale=scale,
            text_color=_RGBA_INK,
            muted_color=_RGBA_MUTED,
            slide_ids=slide_ids,
            warnings=warnings,
            resources=doc.resources,
            base_dir=base_dir,
            mermaid_mode=mermaid_mode,
            mermaid_renderer=mermaid_renderer,
            mermaid_counter=counter,
        )
        slide.contents.extend(extra_objs)

    return slide


def compile_deck(
    deck: Deck,
    output_path: Path,
    *,
    base_dir: Path,
    template_override: Path | None = None,
    mermaid_mode: str | None = None,
    mermaid_renderer: MermaidRenderer | None = None,
) -> CompileResult:
    """Compile a parsed Deck into a validated .keyx file."""
    warnings = list(deck.warnings)
    slide_ids = {s.identifier for s in deck.slides if s.identifier}
    effective_mermaid = (mermaid_mode or deck.config.mermaid or "auto").strip().lower()
    if effective_mermaid not in MERMAID_MODES:
        raise ValueError(
            f"Unsupported mermaid mode {effective_mermaid!r}; expected one of {sorted(MERMAID_MODES)}."
        )
    mermaid_counter = [0]

    template_path = template_override
    if template_path is None and deck.config.template:
        raw_t = Path(deck.config.template)
        template_path = raw_t if raw_t.is_absolute() else (base_dir / raw_t).resolve()

    if template_path is not None:
        doc = KeyxDocument.open(template_path)
        existing_preset_ids = {p.identifier for p in doc.presets}
        for preset in _default_presets():
            if preset.identifier not in existing_preset_ids:
                doc.presets.append(preset)
        doc.presentation.slides = [
            _compile_template_slide(
                spec,
                doc=doc,
                deck=deck,
                slide_ids=slide_ids,
                warnings=warnings,
                base_dir=base_dir,
                mermaid_mode=effective_mermaid,
                mermaid_renderer=mermaid_renderer,
                mermaid_counter=mermaid_counter,
            )
            for spec in deck.slides
        ]
    else:
        resources: dict[str, bytes] = {}
        compiled_slides = [
            _compile_standard_slide(
                spec,
                deck=deck,
                slide_ids=slide_ids,
                warnings=warnings,
                resources=resources,
                base_dir=base_dir,
                mermaid_mode=effective_mermaid,
                mermaid_renderer=mermaid_renderer,
                mermaid_counter=mermaid_counter,
            )
            for spec in deck.slides
        ]
        doc = KeyxDocument(
            payload=Presentation(
                theme=KeynoteTheme(name=deck.config.theme, locale=deck.config.locale),
                slide_size=Size(width=deck.config.width, height=deck.config.height),
                slides=compiled_slides,
            ),
            presets=_default_presets(),
            resources=resources,
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(output_path, pre_save_hooks=[validate_document])
    return CompileResult(
        output_path=output_path,
        slide_count=len(doc.presentation.slides),
        warnings=warnings,
    )
