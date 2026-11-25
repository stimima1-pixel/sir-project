"""Renders the company emblem's SVG master (sdr_tuner/data/brand/stimita_emblem.svg) to the RGBA PNG the icon generator
reads, with the three gears' cut-outs filled white - the vector was traced from a bitmap and its tracer cut the gears
(white in the company's drawing) as holes. The gears are the enclosed transparent regions inside the solid right leaf;
the circuit leaf's own enclosed pockets (the ring centres) stay transparent, so the rule is by position, x > 55 %.

    .venv/bin/python tools/render_emblem.py          # writes sdr_tuner/data/brand/stimita_emblem_svg_2000.png
"""
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PIL import Image, ImageDraw, ImageOps
import numpy as np
from PySide6.QtCore import QSize
from PySide6.QtGui import QColor, QGuiApplication, QImage, QPainter
from PySide6.QtSvg import QSvgRenderer

ROOT = Path(__file__).resolve().parents[1]
SVG = ROOT / "sdr_tuner" / "data" / "brand" / "stimita_emblem.svg"
OUT = ROOT / "sdr_tuner" / "data" / "brand" / "stimita_emblem_svg_2000.png"
HEIGHT = 2000                # the render's height in px; the icon generator scales it to EMBLEM_HEIGHT on the tile
GEAR_SIDE = 0.55             # the gears live in the right leaf: transparent pockets right of this share of the width are gears
MIN_POCKET_PX = 50           # smaller enclosed pockets are tracing specks, left alone


def render(height=HEIGHT):
    app = QGuiApplication.instance() or QGuiApplication(sys.argv)
    renderer = QSvgRenderer(str(SVG))
    if not renderer.isValid():
        raise SystemExit(f"{SVG} is not a valid SVG")
    box = renderer.viewBoxF()
    width = round(box.width() / box.height() * height)
    image = QImage(QSize(width, height), QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(QColor(0, 0, 0, 0))
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    renderer.render(painter)
    painter.end()
    image = image.convertToFormat(QImage.Format.Format_RGBA8888)
    buf = image.constBits().tobytes() if hasattr(image.constBits(), "tobytes") else bytes(image.constBits())
    return Image.frombuffer("RGBA", (image.width(), image.height()), buf, "raw", "RGBA", image.bytesPerLine(), 1).copy()


def fill_gears(emblem):
    """Every transparent pocket that the outside cannot reach, in the right leaf, becomes opaque white."""
    alpha = np.array(emblem.getchannel("A"))
    # .copy(): Pillow's floodfill silently fills NOTHING on the read-only image fromarray() returns (Pillow 12.3)
    clear = Image.fromarray(np.where(alpha < 8, 255, 0).astype(np.uint8), "L").copy()
    # a 1 px transparent border, so the seed is background even when the crop puts a leaf tip in the very corner
    outside = ImageOps.expand(clear, border=1, fill=255)
    ImageDraw.floodfill(outside, (0, 0), 128)                          # the background, reachable from the corner
    reached = np.array(outside)[1:-1, 1:-1] == 128
    pockets = (np.array(clear) == 255) & ~reached                      # enclosed and transparent
    w = emblem.width
    pockets[:, : int(w * GEAR_SIDE)] = False                           # the circuit leaf's ring centres stay open
    # drop specks: label pockets by flood-filling each one and measuring it
    px = np.array(emblem).copy()
    mask = Image.fromarray(pockets.astype(np.uint8) * 255, "L").copy()
    filled = 0
    while True:
        ys, xs = np.nonzero(np.array(mask) == 255)
        if len(ys) == 0:
            break
        ImageDraw.floodfill(mask, (int(xs[0]), int(ys[0])), 128)
        comp = np.array(mask) == 128
        if comp.sum() >= MIN_POCKET_PX:
            px[comp] = (255, 255, 255, 255)
            filled += 1
        arr = np.array(mask)
        arr[comp] = 1
        mask = Image.fromarray(arr, "L").copy()
    return Image.fromarray(px, "RGBA"), filled


if __name__ == "__main__":
    emblem = render()
    emblem = emblem.crop(emblem.getchannel("A").point(lambda v: 255 if v > 8 else 0).getbbox())
    emblem, filled = fill_gears(emblem)
    emblem.save(OUT, optimize=True)
    white = ((np.array(emblem)[..., :3] > 230).all(axis=-1) & (np.array(emblem)[..., 3] > 200)).sum()
    print(f"wrote {OUT.relative_to(ROOT)} {emblem.size}, {filled} gear pockets filled, {int(white)} opaque white px")
