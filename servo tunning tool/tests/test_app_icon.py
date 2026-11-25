"""The app icon (sdr_tuner/data/app_icon.png, built by tools/make_app_icon.py) sits in the Dock like every other
macOS icon: a 1024 canvas whose opaque tile is Apple's 824 px rounded square with a 100 px transparent margin, a shadow
in that margin, and the company emblem's red on it (user, 2026-09-26: "make sure logo match with other icons in mac
dock and also icon look like 3 dimensional"). The file was never committed before this - only the code loading it."""
from PySide6.QtGui import QImage

from sdr_tuner.app import APP_ICON_PATH


def _alpha_bbox(image, threshold):
    xs, ys = [], []
    for y in range(0, image.height(), 2):
        for x in range(0, image.width(), 2):
            if image.pixelColor(x, y).alpha() > threshold:
                xs.append(x)
                ys.append(y)
    return min(xs), min(ys), max(xs) + 1, max(ys) + 1


def test_the_app_icon_has_apples_dock_geometry_and_the_emblem_on_a_shaded_tile():
    assert APP_ICON_PATH.exists()
    icon = QImage(str(APP_ICON_PATH))
    assert icon.width() == icon.height() == 1024 and icon.hasAlphaChannel()
    left, top, right, bottom = _alpha_bbox(icon, 250)                          # the fully opaque tile
    assert abs(left - 100) <= 3 and abs(top - 100) <= 3 and abs(right - 924) <= 3 and abs(bottom - 924) <= 3
    assert icon.pixelColor(2, 2).alpha() == 0 and icon.pixelColor(1021, 1021).alpha() == 0     # the margin is clear
    shadow_left, shadow_top, shadow_right, shadow_bottom = _alpha_bbox(icon, 8)   # the drop shadow spills into it
    assert shadow_bottom > bottom + 20 and shadow_left < left and shadow_right > right
    tile_top, tile_bottom = icon.pixelColor(512, 130), icon.pixelColor(512, 900)  # the tile is shaded top to bottom
    assert tile_top.lightness() > tile_bottom.lightness() + 15
    import sys
    sys.path.insert(0, str(APP_ICON_PATH.parents[2] / "tools"))
    import make_app_icon as mai
    if mai.EMBLEM_SOURCE == "monogram":                                            # letters, not the emblem: find gold
        gold = [(x, y) for y in range(380, 660, 4) for x in range(260, 764, 4)
                if (c := icon.pixelColor(x, y)).red() > 150 and c.red() > c.green() > c.blue() and c.red() - c.blue() > 60]
        assert len(gold) > 300, len(gold)                                           # brushed-gold letters fill the middle band
        assert icon.pixelColor(*gold[0]).lightness() > tile_bottom.lightness() + 40
        return
    leaf = icon.pixelColor(600, 420)                                               # inside the emblem's right leaf
    stand_off, tile_ref = leaf.lightness(), tile_bottom.lightness()                # what must differ, and from what
    if mai.EMBLEM_COLOUR == "red":
        assert leaf.red() > 150 and leaf.red() > 2 * leaf.green() and leaf.red() > 2 * leaf.blue()
    elif mai.EMBLEM_COLOUR == "gold":
        assert leaf.red() > leaf.green() > leaf.blue() and leaf.red() > 150
        # the brushed sheet is lit at the top, where that pixel sits: judge the stand-off on the whole emblem's gold
        gold = sorted(icon.pixelColor(x, y).lightness() for y in range(230, 780, 4) for x in range(260, 764, 4)
                      if (c := icon.pixelColor(x, y)).red() > 150 and c.red() > c.green() > c.blue() and c.red() - c.blue() > 60)
        assert len(gold) > 1000, len(gold)
        stand_off = gold[len(gold) // 2]                                           # the median gold pixel...
        tile_ref = icon.pixelColor(512, 800).lightness()                           # ...against the tile just under the
                                                                                   # emblem, clear of the edge's inner shadow
    elif mai.EMBLEM_COLOUR.startswith("mix-"):                                     # a colour run: red-family at this height
        assert leaf.red() > 150 and leaf.red() > leaf.green() + 40 and leaf.red() > leaf.blue() + 20
    elif mai.EMBLEM_COLOUR.startswith("#"):                                        # one given colour, lit at the top
        want = mai.hex_rgb(mai.EMBLEM_COLOUR)
        got = (leaf.red(), leaf.green(), leaf.blue())
        assert all(0.8 * w <= g <= 1.3 * w + 6 for w, g in zip(want, got)), (want, got)
    else:                                                                          # white: bright and neutral
        assert min(leaf.red(), leaf.green(), leaf.blue()) > 190 and leaf.red() - leaf.blue() < 20
    assert abs(stand_off - tile_ref) > 60                                          # the emblem stands off the tile
