"""Compile Markdown decks into Apple Keynote Interchange Format (.keyx) files."""

from .compiler import CompileResult, compile_deck
from .parser import Deck, SlideSpec, parse_markdown

__all__ = [
    "CompileResult",
    "Deck",
    "SlideSpec",
    "compile_deck",
    "parse_markdown",
]
