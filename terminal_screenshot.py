#!/usr/bin/env python3
"""Generate a terminal-style screenshot from plain text.

The script renders a dark terminal canvas with light monospace output and a
Claude Code-like input area at the bottom. It is intentionally self-contained
except for Pillow.

Usage:
  python3 terminal_screenshot.py --text "Hello\n世界" --output terminal.png
  python3 terminal_screenshot.py --input content.txt --output terminal.png

User-editable inputs are intentionally limited to terminal output content and
the output file path. Canvas size, font, colors, status line, input box style,
and bottom model/token information are locked by this script for consistency.
"""

from __future__ import annotations

import argparse
import random
import textwrap
import unicodedata
from pathlib import Path
from typing import Iterable

from PIL import Image, ImageDraw, ImageFont


class TerminalTheme:
    def __init__(
        self,
        background: str = "#2b2b2b",
        foreground: str = "#d8d8d8",
        dim: str = "#777777",
        subtle: str = "#5d5d5d",
        accent: str = "#42dfe7",
        cursor: str = "#cfcfcf",
    ) -> None:
        self.background = background
        self.foreground = foreground
        self.dim = dim
        self.subtle = subtle
        self.accent = accent
        self.cursor = cursor


CANVAS_WIDTH = 1788
FONT_SIZE = 27
PADDING_X = 38
PADDING_TOP = 18
LINE_GAP = 7
PROMPT_SYMBOL = "›"

MODEL_CHOICES = (
    ("deepseek", "deepseek-v4-flash", 64_000),
    ("glm-5.1", "glm-5.1", 128_000),
)


FONT_CANDIDATES = (
    "/System/Library/Fonts/Menlo.ttc",
    "/System/Library/Fonts/SFNSMono.ttf",
    "/System/Library/Fonts/Supplemental/PTMono.ttc",
    "/Library/Fonts/Menlo.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
)

CJK_FONT_CANDIDATES = (
    "/System/Library/Fonts/STHeiti Medium.ttc",
    "/System/Library/Fonts/PingFang.ttc",
    "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
)


class TerminalMetadata:
    def __init__(
        self,
        *,
        status_line: str,
        model_line: str,
        model_name: str,
        tokens: int,
        rounds: int,
        tools: int,
        elapsed_seconds: float,
    ) -> None:
        self.status_line = status_line
        self.model_line = model_line
        self.model_name = model_name
        self.tokens = tokens
        self.rounds = rounds
        self.tools = tools
        self.elapsed_seconds = elapsed_seconds


def _format_token_count(tokens: int) -> str:
    return f"{tokens / 1000:.1f}k"


def generate_terminal_metadata(rng: random.Random | None = None) -> TerminalMetadata:
    """Generate realistic locked footer metadata for each screenshot."""

    random_source = rng or random.SystemRandom()
    model_name, model_label, context_limit = random_source.choice(MODEL_CHOICES)
    max_consumed = min(64_000, context_limit - 8_000)
    tokens = random_source.randint(8_000, max_consumed)
    rounds = random_source.randint(1, 6)
    tools = random_source.randint(1, 8)
    elapsed_seconds = round(random_source.uniform(8.0, 180.0), 1)
    status_tokens = random_source.randint(1_800, 9_800)

    status_line = f"✓ Shipped · {rounds} rounds · {tools} tools · {elapsed_seconds:.1f}s · {status_tokens} tokens"
    model_line = f"{model_label} · ~ · {_format_token_count(tokens)}/{context_limit // 1000}k tok"
    return TerminalMetadata(
        status_line=status_line,
        model_line=model_line,
        model_name=model_name,
        tokens=tokens,
        rounds=rounds,
        tools=tools,
        elapsed_seconds=elapsed_seconds,
    )


def _first_existing(paths: Iterable[str]) -> str | None:
    for path in paths:
        if Path(path).exists():
            return path
    return None


def load_font(size: int, font_path: str | None = None) -> tuple[ImageFont.FreeTypeFont, ImageFont.FreeTypeFont]:
    """Load an ASCII monospace font and a CJK-capable fallback font."""

    ascii_path = font_path or _first_existing(FONT_CANDIDATES)
    cjk_path = _first_existing(CJK_FONT_CANDIDATES) or ascii_path

    if ascii_path is None:
        fallback = ImageFont.load_default(size=size)
        return fallback, fallback

    ascii_font = ImageFont.truetype(ascii_path, size=size)
    cjk_font = ImageFont.truetype(cjk_path, size=size) if cjk_path else ascii_font
    return ascii_font, cjk_font


def _is_cjk_or_wide(char: str) -> bool:
    if char == "\n":
        return False
    return unicodedata.east_asian_width(char) in {"W", "F"} or "\u4e00" <= char <= "\u9fff"


def _font_for_char(char: str, ascii_font: ImageFont.ImageFont, cjk_font: ImageFont.ImageFont) -> ImageFont.ImageFont:
    return cjk_font if _is_cjk_or_wide(char) else ascii_font


def _text_width(text: str, ascii_font: ImageFont.ImageFont, cjk_font: ImageFont.ImageFont) -> int:
    width = 0
    for char in text:
        font = _font_for_char(char, ascii_font, cjk_font)
        bbox = font.getbbox(char)
        width += bbox[2] - bbox[0]
    return width


def _line_height(ascii_font: ImageFont.ImageFont, cjk_font: ImageFont.ImageFont, line_gap: int) -> int:
    ascii_bbox = ascii_font.getbbox("Ag")
    cjk_bbox = cjk_font.getbbox("国")
    font_height = max(ascii_bbox[3] - ascii_bbox[1], cjk_bbox[3] - cjk_bbox[1])
    return font_height + line_gap


def wrap_terminal_text(
    text: str,
    max_width: int,
    ascii_font: ImageFont.ImageFont,
    cjk_font: ImageFont.ImageFont,
    tab_size: int = 4,
) -> list[str]:
    """Wrap text by rendered pixel width while preserving explicit newlines."""

    wrapped: list[str] = []
    for raw_line in text.replace("\t", " " * tab_size).splitlines() or [""]:
        line = ""
        line_width = 0

        for char in raw_line:
            font = _font_for_char(char, ascii_font, cjk_font)
            bbox = font.getbbox(char)
            char_width = bbox[2] - bbox[0]

            if line and line_width + char_width > max_width:
                wrapped.append(line.rstrip())
                line = char
                line_width = char_width
            else:
                line += char
                line_width += char_width

        wrapped.append(line.rstrip())

    return wrapped


def draw_mixed_text(
    draw: ImageDraw.ImageDraw,
    xy: tuple[int, int],
    text: str,
    ascii_font: ImageFont.ImageFont,
    cjk_font: ImageFont.ImageFont,
    fill: str,
) -> None:
    """Draw text with a CJK fallback font on a per-character basis."""

    x, y = xy
    for char in text:
        font = _font_for_char(char, ascii_font, cjk_font)
        draw.text((x, y), char, font=font, fill=fill)
        bbox = font.getbbox(char)
        x += bbox[2] - bbox[0]


def render_terminal_screenshot(
    text: str,
    output: str | Path,
) -> Path:
    """Render terminal output to a PNG image and return the output path."""

    theme = TerminalTheme()
    metadata = generate_terminal_metadata()
    ascii_font, cjk_font = load_font(FONT_SIZE)
    small_ascii, small_cjk = load_font(max(18, FONT_SIZE - 3))
    line_height = _line_height(ascii_font, cjk_font, LINE_GAP)
    small_line_height = _line_height(small_ascii, small_cjk, max(4, LINE_GAP - 2))

    content_width = CANVAS_WIDTH - PADDING_X * 2
    lines = wrap_terminal_text(text, content_width, ascii_font, cjk_font)

    output_height = len(lines) * line_height
    divider_gap = max(30, line_height)
    status_height = small_line_height + 36
    input_height = 58
    model_height = small_line_height + 26
    bottom_padding = 10
    computed_height = (
        PADDING_TOP
        + output_height
        + divider_gap
        + status_height
        + 18
        + input_height
        + model_height
        + bottom_padding
    )
    canvas_height = computed_height

    image = Image.new("RGB", (CANVAS_WIDTH, canvas_height), theme.background)
    draw = ImageDraw.Draw(image)

    y = PADDING_TOP
    visible_bottom = canvas_height - (status_height + 18 + input_height + model_height + bottom_padding)
    for line in lines:
        if y + line_height > visible_bottom:
            break
        draw_mixed_text(draw, (PADDING_X, y), line, ascii_font, cjk_font, theme.foreground)
        y += line_height

    footer_top = max(y + divider_gap, canvas_height - (status_height + 18 + input_height + model_height + bottom_padding))
    status_y = footer_top + 5
    status_width = _text_width(metadata.status_line, small_ascii, small_cjk)
    status_x = max(PADDING_X, (CANVAS_WIDTH - status_width) // 2)
    line_y = status_y + small_line_height // 2 + 3

    draw.line((PADDING_X, line_y, max(PADDING_X, status_x - 16), line_y), fill=theme.subtle, width=2)
    draw.line(
        (
            min(CANVAS_WIDTH - PADDING_X, status_x + status_width + 16),
            line_y,
            CANVAS_WIDTH - PADDING_X,
            line_y,
        ),
        fill=theme.subtle,
        width=2,
    )
    draw_mixed_text(draw, (status_x, status_y), metadata.status_line, small_ascii, small_cjk, theme.dim)

    input_top = footer_top + status_height + 18
    draw.line((8, input_top, CANVAS_WIDTH - 8, input_top), fill=theme.accent, width=2)
    draw.line((8, input_top + input_height, CANVAS_WIDTH - 8, input_top + input_height), fill=theme.accent, width=2)

    prompt_y = input_top + (input_height - line_height) // 2 - 1
    draw_mixed_text(draw, (10, prompt_y), PROMPT_SYMBOL, ascii_font, cjk_font, theme.accent)
    cursor_x = 38
    cursor_y = input_top + 15
    draw.rectangle((cursor_x, cursor_y, cursor_x + 15, cursor_y + 30), fill=theme.cursor)

    model_y = input_top + input_height + 16
    draw_mixed_text(draw, (PADDING_X, model_y), metadata.model_line, small_ascii, small_cjk, theme.dim)

    output_path = Path(output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    image.save(output_path)
    return output_path


def _read_text(args: argparse.Namespace) -> str:
    if args.input:
        return Path(args.input).read_text(encoding="utf-8")
    if args.text:
        return args.text
    raise SystemExit("Provide --text or --input.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate a dark terminal-style screenshot from text.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent(
            """\
            Examples:
              python terminal_screenshot.py --text "Hello\\n世界" --output terminal.png
              python terminal_screenshot.py --input output.txt --output terminal.png
            """
        ),
    )
    parser.add_argument("--text", help="Text to render. Use shell quoting for newlines when needed.")
    parser.add_argument("--input", help="UTF-8 text file to render.")
    parser.add_argument("--output", default="terminal_screenshot.png", help="Output PNG path.")
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    text = _read_text(args)
    output = render_terminal_screenshot(text, args.output)
    print(output)


if __name__ == "__main__":
    main()
