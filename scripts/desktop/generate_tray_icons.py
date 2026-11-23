#!/usr/bin/env python3
"""Generate the NeuroCade tray / menu-bar icons from the application icon.

The full logo's network lines turn to noise at 16-22 px, so this derives a
simplified glyph: the solid head silhouette with the rising arrow cut through
it. Outputs, next to the source icon:

  trayTemplate.png / trayTemplate@2x.png  black + alpha, macOS template images
                                           (the menu bar recolours them)
  tray-linux.png / tray-linux@2x.png      white + alpha for Ubuntu's dark top bar

Run from the repository root:
  uv run --no-project --with pillow python -I scripts/desktop/generate_tray_icons.py
"""

from __future__ import annotations

import math
from pathlib import Path

# Pillow is not a project dependency; `uv run --with pillow` supplies it.
from PIL import Image, ImageChops, ImageDraw, ImageFilter  # pyright: ignore[reportMissingImports]

ASSETS = Path(__file__).resolve().parents[2] / "client" / "electron" / "assets"
SCALE = 4  # Work at 4x the 512 px source for smooth edges.
OUTPUTS = (
    ("trayTemplate.png", 18, (0, 0, 0)),
    ("trayTemplate@2x.png", 36, (0, 0, 0)),
    ("tray-linux.png", 22, (255, 255, 255)),
    ("tray-linux@2x.png", 44, (255, 255, 255)),
)

# Arrow geometry in source-icon pixels.
ARROW_TAIL = (205.0, 392.0)
ARROW_TIP = (428.0, 112.0)
ARROW_HEAD_LENGTH = 84.0
ARROW_HEAD_HALF_WIDTH = 48.0
ARROW_SHAFT_WIDTH = 24.0
ARROW_GAP = 15.0
NECK_CUTOFF_Y = 352  # Neck fragments below the jaw blur into noise.

Point = tuple[float, float]


def _odd(value: int) -> int:
    return value if value % 2 else value + 1


def _scaled(point: Point) -> Point:
    return (point[0] * SCALE, point[1] * SCALE)


def _logo_mask(source: Image.Image) -> Image.Image:
    """Coloured (logo) pixels of the icon; the white tile is excluded."""
    rgba = source.convert("RGBA")
    mask = Image.new("L", rgba.size, 0)
    src, dst = rgba.load(), mask.load()
    width, height = rgba.size
    for y in range(height):
        for x in range(width):
            r, g, b, a = src[x, y]
            if a > 128 and max(r, g, b) - min(r, g, b) > 60:
                dst[x, y] = 255
    return mask


def _solid_head(mask: Image.Image) -> Image.Image:
    """Close the network's gaps, fill its interior and trim the neck."""
    kernel = _odd(15 * SCALE)
    closed = mask.filter(ImageFilter.MaxFilter(kernel))
    ImageDraw.floodfill(closed, (0, 0), 128)
    solid = closed.point(lambda value: 0 if value == 128 else 255)
    solid = solid.filter(ImageFilter.MinFilter(kernel))
    ImageDraw.Draw(solid).rectangle([0, NECK_CUTOFF_Y * SCALE, solid.width, solid.height], fill=0)
    return solid


class _Arrow:
    def __init__(self) -> None:
        dx, dy = ARROW_TIP[0] - ARROW_TAIL[0], ARROW_TIP[1] - ARROW_TAIL[1]
        length = math.hypot(dx, dy)
        self.ux, self.uy = dx / length, dy / length
        self.nx, self.ny = -self.uy, self.ux
        self.base = (
            ARROW_TIP[0] - self.ux * ARROW_HEAD_LENGTH,
            ARROW_TIP[1] - self.uy * ARROW_HEAD_LENGTH,
        )

    def _side(self, point: Point, offset: float, along: float = 0.0) -> Point:
        return (
            point[0] + self.nx * offset + self.ux * along,
            point[1] + self.ny * offset + self.uy * along,
        )

    def shaft(self, half_width: float) -> list[Point]:
        corners = (
            self._side(ARROW_TAIL, half_width),
            self._side(ARROW_TAIL, -half_width),
            self._side(self.base, -half_width),
            self._side(self.base, half_width),
        )
        return [_scaled(corner) for corner in corners]

    def head(self, grow: float = 0.0) -> list[Point]:
        corners = (
            self._side(ARROW_TIP, 0.0, grow * 1.6),
            self._side(self.base, ARROW_HEAD_HALF_WIDTH + grow, -grow),
            self._side(self.base, -(ARROW_HEAD_HALF_WIDTH + grow), -grow),
        )
        return [_scaled(corner) for corner in corners]


def _glyph(source: Image.Image) -> Image.Image:
    solid = _solid_head(_logo_mask(source))
    arrow = _Arrow()
    knockout = Image.new("L", solid.size, 0)
    draw = ImageDraw.Draw(knockout)
    draw.polygon(arrow.shaft(ARROW_SHAFT_WIDTH / 2 + ARROW_GAP), fill=255)
    draw.polygon(arrow.head(ARROW_GAP), fill=255)
    glyph = ImageChops.subtract(solid, knockout)
    # Remove slivers left between the knockout and the silhouette's edge.
    opening = _odd(9 * SCALE)
    glyph = glyph.filter(ImageFilter.MinFilter(opening)).filter(ImageFilter.MaxFilter(opening))
    draw = ImageDraw.Draw(glyph)
    draw.polygon(arrow.shaft(ARROW_SHAFT_WIDTH / 2), fill=255)
    draw.polygon(arrow.head(), fill=255)
    glyph = glyph.crop(glyph.getbbox())
    side = int(max(glyph.size) * 1.06)
    square = Image.new("L", (side, side), 0)
    square.paste(glyph, ((side - glyph.width) // 2, (side - glyph.height) // 2))
    return square


def main() -> None:
    source = Image.open(ASSETS / "icon.png").convert("RGBA")
    source = source.resize((source.width * SCALE, source.height * SCALE), Image.LANCZOS)
    glyph = _glyph(source)
    for name, size, rgb in OUTPUTS:
        icon = Image.new("RGBA", (size, size), (*rgb, 0))
        icon.putalpha(glyph.resize((size, size), Image.LANCZOS))
        icon.save(ASSETS / name, optimize=True)
        print(f"wrote {ASSETS / name}")


if __name__ == "__main__":
    main()
