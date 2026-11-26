#!/usr/bin/env python3
"""Split a 4x7 generated avatar sheet into the renderer's 27 PNG sprites."""

from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image


EMOTIONS = ("neutral", "happy", "angry", "laugh", "shock", "sad")
STATES = ("closed", "half", "open", "blink")
SPECIALS = ("neutral_wink", "flirty_wink_open", "brows_up_open")


def split_sheet(source: Path, output_dir: Path, *, size: int = 384) -> list[Path]:
    """Crop an exact 4x7 sheet and resize every used cell with nearest-neighbor."""
    with Image.open(source) as sheet:
        sheet = sheet.convert("RGB")
        width, height = sheet.size
        if width % 4 or height % 7:
            raise ValueError(
                f"sheet must divide evenly into 4x7 cells, got {width}x{height}"
            )
        cell_width = width // 4
        cell_height = height // 7
        if cell_width != cell_height:
            raise ValueError(
                f"sheet cells must be square, got {cell_width}x{cell_height}"
            )

        output_dir.mkdir(parents=True, exist_ok=True)
        written: list[Path] = []
        names = [
            f"{emotion}_{state}"
            for emotion in EMOTIONS
            for state in STATES
        ] + list(SPECIALS)

        for index, name in enumerate(names):
            row, column = divmod(index, 4)
            left = column * cell_width
            top = row * cell_height
            sprite = sheet.crop(
                (left, top, left + cell_width, top + cell_height)
            ).resize((size, size), Image.Resampling.NEAREST)
            target = output_dir / f"{name}.png"
            sprite.save(target, format="PNG", optimize=True)
            written.append(target)
    return written


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--size", type=int, default=384)
    args = parser.parse_args()
    written = split_sheet(args.source, args.output_dir, size=args.size)
    print(f"Wrote {len(written)} sprites to {args.output_dir}")


if __name__ == "__main__":
    main()
