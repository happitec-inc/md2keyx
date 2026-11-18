"""Command-line interface for markdown2keyx."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from .compiler import compile_deck
from .parser import parse_markdown


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="md2keyx",
        description="Compile a Markdown deck into a validated Apple Keynote (.keyx) file.",
    )
    parser.add_argument("input", type=Path, help="Input Markdown (.md) file")
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=None,
        help="Output .keyx path (default: out/<input_stem>.keyx)",
    )
    parser.add_argument(
        "--template",
        type=Path,
        default=None,
        help="Optional template .keyx file whose theme layouts and styles are used",
    )
    parser.add_argument(
        "--open",
        action="store_true",
        help="Open the generated .keyx in Keynote Creator Studio",
    )
    args = parser.parse_args(argv)

    source = args.input.read_text(encoding="utf-8")
    deck = parse_markdown(source)

    output_path = args.output or (Path("out") / f"{args.input.stem}.keyx")
    result = compile_deck(
        deck,
        output_path,
        base_dir=args.input.resolve().parent,
        template_override=args.template,
    )

    for warning in result.warnings:
        print(f"warning: {warning}", file=sys.stderr)

    print(f"Compiled {result.slide_count} slides -> {result.output_path}")

    if args.open:
        subprocess.run(
            ["open", "-a", "Keynote Creator Studio", str(result.output_path)],
            check=True,
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
