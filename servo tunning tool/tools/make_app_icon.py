"""Builds sdr_tuner/data/app_icon.png - the app's window and Dock icon - from the company emblem, to macOS's own
app-icon geometry so it sits in the Dock like every other icon (user, 2026-09-26: "make sure logo match with other
icons in mac dock and also icon look like 3 dimensional"):

  * a 1024 x 1024 canvas with the tile in an 824 x 824 continuous-corner rounded square ("squircle", a superellipse
    of exponent 5, the shape Apple's icon template uses) centred with a 100 px transparent margin - an icon that fills
    the whole canvas renders larger than its neighbours in the Dock;
  * depth: a soft drop shadow under the tile, a top-to-bottom gradient on the tile, a glossy sheen across its upper
    third and a light rim along its top edge; the emblem shaded lighter at the top and deeper at the bottom, with a
    bevel (a highlight on its upper-left edges, a shade on its lower-right) and its own soft shadow onto the tile;
  * the look (user, 2026-09-26: "change background from gray to something else where logo looks more luxury and
    distinguish ... you can also change logo colour as well"): the app's own black + gold - an onyx tile (near-black,
    a faint warm glow behind the emblem), the emblem recoloured to a metallic gold (its white gears kept ivory), and
    a gold bezel just inside the tile's edge. The look went through the real Dock three times: onyx + red + bezel
    (the app's black + gold), then ivory + red + bezel (the red emblem's fine lines were lost on black), then - the
    user's own choice - a WHITE emblem on a dark grey ("slate") tile, still inside the gold bezel; the gears, white in
    the company's drawing, become a dark grey so they stay visible on the white leaf; then the user's own colour,
    #582040 (a deep plum), shipped on the ivory tile inside the gold bezel because on the dark grey it went muted and
    its fine lines faded; then a MIX of colours (the user: eye-catching) - six runs rendered, the "sunset" (plum at the
    base, magenta, red, gold at the tips) on ivory shipped, the same run on the dark tiles being dramatic but sinking
    its plum base; then, given a free hand ("use your own colour ... no limitation"), this file's own pick: the
    MIDNIGHT tile with the "electric" emblem - the circuit leaf teal-to-cyan like a live board, the gear leaf
    magenta-to-coral-to-amber - under a soft GLOW of its own colours (every dark tile gets one now: it is what keeps
    fine lines from sinking into the dark), inside the gold bezel; and finally the user's own "Plum Regent" palette
    card (Royal Plum #4A294B, Deep Wine #5F2147, Cream #F7F2EB, Gold #C7A05A, Warm Grey #9D9792): six pairings rendered,
    shipped the CREAM emblem with GOLD gears on the ROYAL PLUM tile inside the palette's gold bezel - the light emblem
    on the dark tile keeps every line legible. Then no emblem at all - the letters "SA" as a monogram (six faces
    rendered: Didot bold/italic, Snell Roundhand, Zapfino, Bodoni, a two-tone Didot), Didot Italic in brushed gold on
    the Deep Wine tile first; then, asked for calligraphic, Snell Roundhand Bold with a 6 px stroke added
    (MONOGRAM_WEIGHT - script hairlines vanish at Dock size without it), six calligraphic renders compared; then the
    emblem back in the monogram's brushed gold with cream gears on the wine tile ("replace letters with logo"), then the
    flat palette gold instead of the brushed sheet ("how about 4th logo" - of that strip); then a GLASS-finish cream tile
    (TILE_FINISH: a curved reflection, an inner shadow, a bright bottom edge) with the emblem in a classic burgundy
    ("change background to glass like cream and logo as burgundy"). TILE_STYLE /
    EMBLEM_SOURCE / EMBLEM_COLOUR / BEZEL / BEZEL_GOLD / EMBLEM_GEARS / MONOGRAM_* below switch between every one of these.

Needs Pillow and numpy (both in the project venv). Run: .venv/bin/python tools/make_app_icon.py
The colours are the theme's own (theme.BG / GOLD / GOLD_HI / GOLD_LO / TEXT).
"""
from pathlib import Path

import numpy as np
from PIL import Image, ImageChops, ImageFilter

ROOT = Path(__file__).resolve().parent.parent
EMBLEM = ROOT / "sdr_tuner" / "data" / "brand" / "stimita_emblem_svg_2000.png"   # the company's mark rendered from its SVG
                             # master (brand/stimita_emblem.svg, the user's, 2026-09-26) by tools/render_emblem.py: 2000 px
                             # tall, the three gears' cut-outs filled white. Earlier sources beside it: stimita_emblem_2000.png
                             # (keyed from a 1539x2000 WebP) and stimita_emblem.png (1024x1055)
OUT = ROOT / "sdr_tuner" / "data" / "app_icon.png"

SIZE = 1024                  # the canvas
TILE = 824                   # Apple's rounded square inside it (100 px margins)
MARGIN = (SIZE - TILE) // 2
SUPERSAMPLE = 4              # the squircle edge is rendered at 4x and downsampled: an anti-aliased curve
EXPONENT = 5.0               # |x|^n + |y|^n <= 1: Apple's continuous corner, ~22 % corner radius
TILE_STYLE = "onyx"          # "onyx": near-black with the glass finish, under the flat theme-gold emblem - chosen (user,
                             # 2026-09-26, pointing at that render of the strip: "can you make this logo"); "cream" (the
                             # Plum Regent palette's, the glass-cream looks before it); also "deep_wine", "royal_plum",
                             # "warm_grey" from that palette; "midnight" (the electric look); "ivory"; "slate";
                             # "champagne"; "graphite"
EMBLEM_COLOUR = "#D4AF37"    # theme.GOLD as one FLAT colour, white gears - chosen (user, 2026-09-26: "match colour with
                             # software gold colour", then "colour is still different": the brushed sheet's sheen band and
                             # top light had paled the gold to (219, 187, 86)); "gold": the brushed sheet
                             # (GOLD_SETS[EMBLEM_GOLD]); any "#RRGGBB"; "mix-<name>": a run (MIXES: theme-button, regent,
                             # regent-split, sunset, electric, aurora, diagonal, vertical, split); "red": as the company
                             # draws it; "white": white with dark-grey gears
BEZEL = True                 # a gold bezel just inside the tile's edge
TILE_FINISH = "glass"        # "matte": the gradient tile with its light sheen; "glass": a curved reflection across the top,
                             # an inner shadow for thickness and a bright inner edge along the bottom (user, 2026-09-26:
                             # "change background to glass like cream")
BURGUNDY = (122, 30, 51)     # a classic burgundy (#7A1E33); the palette's Deep Wine is the plum-leaning one
BEZEL_PX = 11
TILES = {"graphite": ((44, 47, 58), (12, 13, 16)),        # the theme's RAISED-ish grey down to BG
         "onyx": ((28, 28, 33), (4, 4, 6)),                # near-black, top to bottom
         "ivory": ((251, 247, 238), (226, 219, 203)),      # warm ivory (theme.TEXT's family): the logo as the company draws it, red on light
         "champagne": ((240, 226, 186), (206, 180, 122)),  # a pale metallic gold
         "slate": ((66, 68, 76), (30, 31, 36)),            # dark grey, top to bottom (user, 2026-09-26: "white logo, dark gray")
         "midnight": ((38, 40, 60), (7, 8, 14)),           # near-black with a deep indigo cast - a night sky for a glowing emblem
         # the user's "Plum Regent" palette (2026-09-26): Royal Plum #4A294B, Deep Wine #5F2147, Cream #F7F2EB,
         # Gold #C7A05A, Warm Grey #9D9792
         "cream": ((250, 246, 238), (232, 224, 211)),      # Cream, a touch warmer at the bottom
         "royal_plum": ((96, 56, 98), (58, 30, 58)),       # Royal Plum, lit at the top
         "deep_wine": ((122, 46, 92), (74, 24, 54)),       # Deep Wine, lit at the top
         "warm_grey": ((166, 160, 154), (128, 122, 117))}  # Warm Grey
DARK_TILES = ("graphite", "onyx", "slate", "midnight", "royal_plum", "deep_wine")
ROYAL_PLUM, DEEP_WINE, CREAM, REGENT_GOLD, WARM_GREY = (74, 41, 75), (95, 33, 71), (247, 242, 235), (199, 160, 90), (157, 151, 146)
GOLD_SETS = {"theme": None,                                                     # filled below, once GOLD_* exist
             "regent": ((228, 200, 140), REGENT_GOLD, (138, 106, 50)),          # the palette's gold as a brushed metal
             "antique": ((202, 166, 96), (156, 120, 44), (96, 70, 22))}         # a deeper, older gold: reads on cream
BEZEL_GOLD = "theme"         # which gold the bezel (and metallic_gold) uses: "theme" (the software's own gold - chosen, user 2026-09-26: "match colour with software gold colour"), "regent" (the palette's #C7A05A) or "antique"
EMBLEM_GOLD = "theme"        # which gold the emblem's brushed sheet uses when EMBLEM_COLOUR is "gold": "theme" = the software's own gold (theme.GOLD_HI / GOLD / GOLD_LO) - chosen; "antique" before it: the palette's own gold
                             # went soft on the cream at Dock size, the antique set keeps the metal and still reads
EMBLEM_SOURCE = "emblem"     # "emblem": the company's mark - chosen again (user, 2026-09-26: "how about replace letters with
                             # logo"); "monogram": MONOGRAM_TEXT set in MONOGRAM_FONT (the "SA" rounds)
MONOGRAM_TEXT = "SA"
MONOGRAM_FONT = ("/System/Library/Fonts/Supplemental/SnellRoundhand.ttc", 1)   # (file, face index) - Snell Roundhand Bold,
                             # calligraphic (user: "how about calligraphic"); 2 = Black; Didot.ttc (1 = Italic, 2 = Bold),
                             # "Apple Chancery.ttf", Zapfino.ttf, "Bodoni 72.ttc" (2 = Bold) - all shipped with macOS
MONOGRAM_STYLE = "gold"      # "gold": brushed metal (GOLD_SETS[BEZEL_GOLD]); "cream": flat Cream; "two-tone": 1st letter gold, 2nd cream
MONOGRAM_OVERLAP = 0.06      # the letters overlap by this fraction of a letter's width (0 = side by side; 0.16 for Didot)
MONOGRAM_WEIGHT = 6          # extra stroke, in px at the 760 px render size: a script face's hairlines need it at Dock size
MONOGRAM_WIDTH = 0.74        # the monogram's width as a share of the tile (0.66 for Didot)
EMBLEM_GEARS = None          # None: the gears as drawn (white) - chosen; or a (r, g, b) for the gear cut-outs
EMBLEM_SHADE = (1.0, 1.0)    # None: each colour branch's own top-to-bottom light (see build); or (top, bottom) multipliers to
                             # override it - (1.0, 1.0) keeps the flat theme gold EXACTLY as given, edge bevel aside (chosen)
GLOW_BLUR = 8                # the dark tiles' glow under the emblem: the blur radius of the emblem's own colours (px at 1024);
                             # 18 until 2026-09-26 ("logo sharpness can you increase") - the wide glow hazed the edges at Dock size
EMBLEM_SHARPEN = (2, 100)    # None, or (radius, percent): an unsharp mask on the emblem at the 1024 render, colour and alpha
ICON_SHARPEN = (1.0, 80)     # None, or (radius, percent): an unsharp mask (colour only) on every iconset size at or under
ICON_SHARPEN_MAX_PX = 256    # ICON_SHARPEN_MAX_PX after the LANCZOS downscale - the Dock's own sizes, where fine lines soften
GOLD, GOLD_HI, GOLD_LO = (212, 175, 55), (235, 203, 107), (143, 116, 32)     # theme.GOLD / GOLD_HI / GOLD_LO
GOLD_SETS["theme"] = (GOLD_HI, GOLD, GOLD_LO)
WHITE, GEAR_GREY = (246, 246, 248), (62, 64, 72)          # the white emblem, and its gears cut into the tile's grey
PLUM, BRAND_RED, MAGENTA = (88, 32, 64), (227, 22, 42), (196, 40, 110)      # the user's #582040, the company's red
MIXES = {                    # EMBLEM_COLOUR "mix-<name>": a colour run through the emblem (user, 2026-09-26: "a mix of
    "diagonal": [(0.0, PLUM), (0.45, BRAND_RED), (1.0, GOLD_HI)],          # colour ... eye catching"): bottom-left -> top-right
    "vertical": [(0.0, PLUM), (0.5, BRAND_RED), (1.0, GOLD_HI)],           # bottom -> top
    "sunset": [(0.0, PLUM), (0.35, MAGENTA), (0.7, BRAND_RED), (1.0, GOLD_HI)],
    "split": [(0.0, GOLD), (1.0, PLUM)],                                   # the circuit leaf gold, the solid leaf plum
    "regent": [(0.0, DEEP_WINE), (0.55, ROYAL_PLUM), (1.0, (118, 66, 118))],   # the palette: wine at the base, plum, lit tips
    "regent-split": [(0.0, REGENT_GOLD), (1.0, CREAM)],                        # the circuit leaf in the palette's gold, the solid leaf cream
    "aurora": [(0.0, (110, 60, 210)), (0.35, (225, 45, 135)), (0.7, (255, 110, 60)), (1.0, (255, 215, 95))],
    "theme-button": [(0.0, GOLD_LO), (1.0, GOLD_HI)],                     # the GUI's own primary-button gradient, top -> bottom
}
# "electric": two runs, one per leaf - the circuit leaf cool (a live board), the gear leaf warm - blended at the seam
ELECTRIC_LEFT = [(0.0, (18, 140, 165)), (0.55, (40, 205, 230)), (1.0, (130, 240, 255))]      # teal -> cyan, base -> tip
ELECTRIC_RIGHT = [(0.0, (190, 35, 115)), (0.5, (255, 92, 96)), (1.0, (255, 196, 84))]         # magenta -> coral -> amber
IVORY = (237, 231, 217)                                                       # theme.TEXT, for the gears
EMBLEM_HEIGHT = 560          # the emblem's height on the tile (about 68 % of it)
EMBLEM_CENTRE_Y = 504        # a little above the geometric centre: the optical centre


def squircle(size, exponent=EXPONENT, ss=SUPERSAMPLE):
    """An anti-aliased L mask of the continuous-corner rounded square filling size x size."""
    n = size * ss
    t = (np.arange(n) + 0.5) / n * 2.0 - 1.0
    x, y = np.meshgrid(t, t)
    inside = (np.abs(x) ** exponent + np.abs(y) ** exponent) <= 1.0
    return Image.fromarray((inside * 255).astype(np.uint8)).resize((size, size), Image.LANCZOS)


def vertical_gradient(size, top, bottom):
    rows = np.linspace(0.0, 1.0, size)[:, None, None]
    rgb = np.array(top, dtype=float)[None, None, :] * (1 - rows) + np.array(bottom, dtype=float)[None, None, :] * rows
    return Image.fromarray(np.repeat(rgb, size, axis=1).astype(np.uint8), "RGB")


def layer(size, colour, alpha_mask):
    """An RGBA layer of one colour whose alpha is the given L mask."""
    out = Image.new("RGBA", (size, size), colour + (0,))
    out.putalpha(alpha_mask)
    return out


def shifted(mask, dx, dy):
    return ImageChops.offset(mask, dx, dy)


def metallic_gold(width, height, gold_set=None):
    """A brushed-gold RGB sheet: a diagonal run from the set's light gold (upper left) through its gold, a bright sheen
    band, the gold again and its dark gold (lower right) - the light of a metal surface, not a flat paint."""
    hi, mid, lo = GOLD_SETS[gold_set or BEZEL_GOLD]
    stops = [(0.0, hi), (0.30, mid), (0.48, hi), (0.62, mid), (1.0, lo)]
    xs = np.arange(width)[None, :]
    ys = np.arange(height)[:, None]
    u = ((xs + ys) / float(width + height)).astype(float)
    out = np.zeros((height, width, 3))
    for (u0, c0), (u1, c1) in zip(stops, stops[1:]):
        t = np.clip((u - u0) / (u1 - u0), 0.0, 1.0)[..., None]
        band = (u >= u0)[..., None] & (u <= u1)[..., None] if u1 < 1.0 else (u >= u0)[..., None]
        out = np.where(band, np.array(c0) * (1 - t) + np.array(c1) * t, out)
    return out


def recolour_gold(emblem):
    """The emblem's red becomes metallic gold, its white (the gears) ivory; anti-aliased edges follow their own alpha."""
    px = np.array(emblem).astype(float)
    r, g, b = px[..., 0], px[..., 1], px[..., 2]
    redness = np.clip((r - np.maximum(g, b)) / 110.0, 0.0, 1.0)[..., None]         # 1 on the red, 0 on the white
    gold = metallic_gold(emblem.width, emblem.height, EMBLEM_GOLD)
    ivory = np.array(IVORY, dtype=float)[None, None, :]
    px[..., :3] = redness * gold + (1.0 - redness) * ivory
    return Image.fromarray(np.clip(px, 0, 255).astype(np.uint8), "RGBA")


def recolour_white(emblem):
    """The emblem's red becomes white; its gears, white in the original, become a dark grey so they still read."""
    px = np.array(emblem).astype(float)
    r, g, b = px[..., 0], px[..., 1], px[..., 2]
    redness = np.clip((r - np.maximum(g, b)) / 110.0, 0.0, 1.0)[..., None]
    px[..., :3] = redness * np.array(WHITE, dtype=float) + (1.0 - redness) * np.array(GEAR_GREY, dtype=float)
    return Image.fromarray(np.clip(px, 0, 255).astype(np.uint8), "RGBA")


def recolour_flat(emblem, rgb):
    """The emblem's red becomes one given colour (a "#RRGGBB" string); its white gears stay white."""
    px = np.array(emblem).astype(float)
    r, g, b = px[..., 0], px[..., 1], px[..., 2]
    redness = np.clip((r - np.maximum(g, b)) / 110.0, 0.0, 1.0)[..., None]
    px[..., :3] = redness * np.array(rgb, dtype=float) + (1.0 - redness) * px[..., :3]
    return Image.fromarray(np.clip(px, 0, 255).astype(np.uint8), "RGBA")


def gradient_colours(u, stops):
    """Per-pixel colours for a parameter u in [0, 1] along the given (position, rgb) stops."""
    out = np.zeros(u.shape + (3,))
    for (u0, c0), (u1, c1) in zip(stops, stops[1:]):
        t = np.clip((u - u0) / (u1 - u0), 0.0, 1.0)[..., None]
        band = ((u >= u0) & (u <= u1))[..., None] if u1 < 1.0 else (u >= u0)[..., None]
        out = np.where(band, np.array(c0, dtype=float) * (1 - t) + np.array(c1, dtype=float) * t, out)
    return out


def recolour_mix(emblem, name):
    """The emblem's red becomes a run of colours across it (MIXES[name]); its white gears stay white."""
    px = np.array(emblem).astype(float)
    r, g, b = px[..., 0], px[..., 1], px[..., 2]
    redness = np.clip((r - np.maximum(g, b)) / 110.0, 0.0, 1.0)[..., None]
    h, w = r.shape
    ys, xs = np.mgrid[0:h, 0:w]
    fx, fy = xs / max(w - 1, 1), ys / max(h - 1, 1)
    if name == "electric":                                            # each leaf its own run over its height
        seam = np.clip((fx - 0.5) / 0.10 + 0.5, 0.0, 1.0)[..., None]
        colours = (1 - seam) * gradient_colours(1.0 - fy, ELECTRIC_LEFT) + seam * gradient_colours(1.0 - fy, ELECTRIC_RIGHT)
    else:
        if name in ("diagonal", "aurora"):
            u = 0.5 * fx + 0.5 * (1.0 - fy)
        elif name in ("split", "regent-split"):                       # a soft seam down the middle
            u = np.clip((fx - 0.5) / 0.10 + 0.5, 0.0, 1.0)
        else:                                                         # vertical, sunset: bottom -> top
            u = 1.0 - fy
        colours = gradient_colours(u, MIXES[name])
    px[..., :3] = redness * colours + (1.0 - redness) * px[..., :3]
    return Image.fromarray(np.clip(px, 0, 255).astype(np.uint8), "RGBA")


def recolour_gears(emblem, rgb):
    """The emblem's white parts - the gears - in a given colour (the red parts untouched)."""
    px = np.array(emblem).astype(float)
    r, g, b = px[..., 0], px[..., 1], px[..., 2]
    redness = np.clip((r - np.maximum(g, b)) / 110.0, 0.0, 1.0)
    whiteness = np.clip((np.minimum(np.minimum(r, g), b) - 150.0) / 80.0, 0.0, 1.0) * (1.0 - redness)
    px[..., :3] = whiteness[..., None] * np.array(rgb, dtype=float) + (1.0 - whiteness[..., None]) * px[..., :3]
    return Image.fromarray(np.clip(px, 0, 255).astype(np.uint8), "RGBA")


def render_monogram(text=None, font=None, style=None, overlap=None, gold_set=None, size_px=760, weight=None):
    """The letters of `text` set in `font` (a (file, face) pair), each glyph drawn on its own so they can overlap,
    filled with brushed gold, flat cream or gold-then-cream - an RGBA image cropped to the letters."""
    from PIL import ImageFont
    text = text or MONOGRAM_TEXT
    path, face = font or MONOGRAM_FONT
    style = style or MONOGRAM_STYLE
    overlap = MONOGRAM_OVERLAP if overlap is None else overlap
    weight = MONOGRAM_WEIGHT if weight is None else weight
    fnt = ImageFont.truetype(path, size_px, index=face)
    boxes = [fnt.getbbox(ch) for ch in text]
    widths = [b[2] - b[0] for b in boxes]
    step = [w * (1.0 - overlap) for w in widths]
    total_w = int(sum(step[:-1]) + widths[-1] + 40 + 2 * (weight or 0))
    top = min(b[1] for b in boxes)
    bottom = max(b[3] for b in boxes)
    total_h = int(bottom - top + 40 + 2 * (weight or 0))
    canvas = Image.new("RGBA", (total_w, total_h), (0, 0, 0, 0))
    gold_sheet = Image.fromarray(np.clip(metallic_gold(total_w, total_h, gold_set), 0, 255).astype(np.uint8), "RGB")
    x = 20
    for i, (ch, box) in enumerate(zip(text, boxes)):
        mask = Image.new("L", (total_w, total_h), 0)
        from PIL import ImageDraw
        ImageDraw.Draw(mask).text((x - box[0], 20 - top), ch, font=fnt, fill=255)
        if weight:                                                    # thicken every stroke by `weight` px each side
            mask = mask.filter(ImageFilter.MaxFilter(2 * int(weight) + 1))
        if style == "gold" or (style == "two-tone" and i == 0):
            fill = gold_sheet.convert("RGBA")
        else:
            fill = Image.new("RGBA", (total_w, total_h), CREAM + (255,))
        fill.putalpha(mask)
        canvas.alpha_composite(fill)
        x += step[i]
    return canvas.crop(canvas.getchannel("A").getbbox())


def hex_rgb(text):
    text = text.lstrip("#")
    return tuple(int(text[i:i + 2], 16) for i in (0, 2, 4))


def glass_layers(canvas, tile_mask):
    """The glass finish: a curved reflection over the top of the tile (bright, with a crisp lower edge), an inner shadow
    around the inside of the edge (thickness) and a bright inner line along the bottom (light caught in the glass)."""
    from PIL import ImageDraw
    # the reflection: an ellipse wider than the tile, its lower edge a curve across the tile's upper third
    reflect = Image.new("L", (SIZE, SIZE), 0)
    ImageDraw.Draw(reflect).ellipse((MARGIN - 260, MARGIN - 520, SIZE - MARGIN + 260, MARGIN + 400), fill=255)
    rows = np.linspace(0.0, 1.0, SIZE)[:, None]
    fade = np.clip(1.0 - (rows - (MARGIN / SIZE)) / 0.42, 0.0, 1.0) ** 0.9 * 0.55 + 0.10       # 65 % at the top, 10 % low
    fade_img = Image.fromarray((np.repeat(fade, SIZE, axis=1) * 255).astype(np.uint8), "L")
    reflect = ImageChops.multiply(ImageChops.multiply(reflect, fade_img), tile_mask)
    canvas.alpha_composite(layer(SIZE, (255, 255, 255), reflect))
    # the inner shadow: the tile's own edge, inset and blurred, dark on the inside
    inner = Image.new("L", (SIZE, SIZE), 0)
    inner.paste(squircle(TILE - 36), (MARGIN + 18, MARGIN + 18))
    inner_shadow = ImageChops.subtract(tile_mask, inner).filter(ImageFilter.GaussianBlur(14))
    inner_shadow = ImageChops.multiply(inner_shadow, tile_mask).point(lambda v: int(v * 0.28))
    canvas.alpha_composite(layer(SIZE, (60, 40, 30), inner_shadow))
    # the bright inner edge along the bottom: the edge band, shifted up 6 px, only its lower half
    lip = ImageChops.subtract(tile_mask, shifted(tile_mask, 0, -6)).filter(ImageFilter.GaussianBlur(2.0))
    lower = Image.fromarray((np.repeat(np.clip((rows - 0.55) / 0.35, 0.0, 1.0), SIZE, axis=1) * 255).astype(np.uint8), "L")
    lip = ImageChops.multiply(ImageChops.multiply(lip, lower), tile_mask).point(lambda v: int(v * 0.8))
    canvas.alpha_composite(layer(SIZE, (255, 255, 255), lip))


def build(tile_style=None, emblem_colour=None, bezel=None, glow=None, gold=None, gears=None, source=None,
          mono_font=None, mono_style=None, mono_overlap=None, mono_text=None, mono_weight=None, mono_width=None,
          finish=None):
    tile_style = tile_style or TILE_STYLE
    finish = finish or TILE_FINISH
    emblem_colour = emblem_colour or EMBLEM_COLOUR
    bezel = BEZEL if bezel is None else bezel
    glow = (tile_style in DARK_TILES) if glow is None else glow        # a dark tile gets a glow under the emblem
    gold = gold or BEZEL_GOLD
    gears = EMBLEM_GEARS if gears is None else gears
    source = source or EMBLEM_SOURCE
    canvas = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    tile_mask = Image.new("L", (SIZE, SIZE), 0)
    tile_mask.paste(squircle(TILE), (MARGIN, MARGIN))

    # the drop shadow into the margin, under everything
    shadow = shifted(tile_mask, 0, 14).filter(ImageFilter.GaussianBlur(20)).point(lambda v: int(v * 0.55))
    canvas.alpha_composite(layer(SIZE, (0, 0, 0), shadow))

    # the tile: a vertical gradient cut by the squircle; onyx adds a faint warm glow behind the emblem
    top, bottom = TILES[tile_style]
    tile = vertical_gradient(SIZE, top, bottom).convert("RGBA")
    tile.putalpha(tile_mask)
    canvas.alpha_composite(tile)
    if tile_style in ("onyx", "champagne"):
        yy, xx = np.mgrid[0:SIZE, 0:SIZE]
        dist = np.hypot(xx - SIZE / 2, yy - EMBLEM_CENTRE_Y) / 400.0
        glow = Image.fromarray((np.clip(1.0 - dist, 0.0, 1.0) ** 2 * 0.14 * 255).astype(np.uint8), "L")
        canvas.alpha_composite(layer(SIZE, GOLD, ImageChops.multiply(glow, tile_mask)))
    if finish == "glass":
        glass_layers(canvas, tile_mask)
    if bezel:                                                        # a brushed-gold band just inside the edge
        inner = Image.new("L", (SIZE, SIZE), 0)
        inner.paste(squircle(TILE - 2 * BEZEL_PX), (MARGIN + BEZEL_PX, MARGIN + BEZEL_PX))
        band = ImageChops.subtract(tile_mask, inner)
        gold_sheet = Image.fromarray(np.clip(metallic_gold(SIZE, SIZE, gold), 0, 255).astype(np.uint8), "RGB").convert("RGBA")
        gold_sheet.putalpha(band)
        canvas.alpha_composite(gold_sheet)
        edge = ImageChops.subtract(inner, shifted(inner, 1, 1)).filter(ImageFilter.GaussianBlur(0.6))    # the bezel's
        canvas.alpha_composite(layer(SIZE, (0, 0, 0), edge.point(lambda v: int(v * 0.55))))            # inner lip

    # a glossy sheen over the upper third (white, fading out by the middle), and a rim light on the top edge
    rows = np.linspace(0.0, 1.0, SIZE)
    sheen_profile = np.clip(1.0 - (rows - 0.10) / 0.40, 0.0, 1.0) ** 1.6 * 0.16      # 16 % at the top, 0 by mid-tile
    sheen_profile[rows < 0.10] = 0.16
    sheen = Image.fromarray((np.repeat(sheen_profile[:, None], SIZE, axis=1) * 255).astype(np.uint8), "L")
    sheen = ImageChops.multiply(sheen, tile_mask)
    canvas.alpha_composite(layer(SIZE, (255, 255, 255), sheen))
    rim = ImageChops.subtract(tile_mask, shifted(tile_mask, 0, 3))                    # the top 3 px of the tile's edge
    rim = rim.filter(ImageFilter.GaussianBlur(0.8)).point(lambda v: int(v * 0.45))
    canvas.alpha_composite(layer(SIZE, (255, 255, 255), rim))
    dark_rim = ImageChops.subtract(tile_mask, shifted(tile_mask, 0, -3))              # the bottom edge, a shade darker
    dark_rim = dark_rim.filter(ImageFilter.GaussianBlur(0.8)).point(lambda v: int(v * 0.5))
    canvas.alpha_composite(layer(SIZE, (0, 0, 0), dark_rim))

    # the emblem: cropped to its own alpha, scaled, shaded top-to-bottom, placed on the optical centre
    if source == "monogram":
        emblem = render_monogram(mono_text, mono_font, mono_style, mono_overlap, gold, weight=mono_weight)
        scale = min((mono_width or MONOGRAM_WIDTH) * TILE / emblem.width, EMBLEM_HEIGHT / emblem.height)
        emblem = emblem.resize((max(1, round(emblem.width * scale)), max(1, round(emblem.height * scale))), Image.LANCZOS)
        emblem_colour = "as-is"                                       # already coloured
    else:
        emblem = Image.open(EMBLEM).convert("RGBA")
        emblem = emblem.crop(emblem.getchannel("A").getbbox())
        if gears:
            emblem = recolour_gears(emblem, gears)                    # before the red is recoloured: the gears keep their tone
        scale = EMBLEM_HEIGHT / emblem.height
        emblem = emblem.resize((round(emblem.width * scale), EMBLEM_HEIGHT), Image.LANCZOS)
    if emblem_colour == "as-is":
        top_shade, bottom_shade = 1.05, 0.93
    elif emblem_colour == "gold":
        emblem = recolour_gold(emblem)
        top_shade, bottom_shade = 1.06, 0.90                                          # the metal sheet carries the light
    elif emblem_colour == "white":
        emblem = recolour_white(emblem)
        top_shade, bottom_shade = 1.0, 0.86                                           # a soft silver fall-off
    elif emblem_colour.startswith("#"):                                               # any one colour, e.g. "#582040"
        emblem = recolour_flat(emblem, hex_rgb(emblem_colour))
        top_shade, bottom_shade = 1.25, 0.85                                          # a dark colour needs more light at the top
    elif emblem_colour.startswith("mix-"):                                            # a run of colours, see MIXES
        emblem = recolour_mix(emblem, emblem_colour[4:])
        top_shade, bottom_shade = 1.08, 0.92                                          # the run carries its own light
    else:
        top_shade, bottom_shade = 1.12, 0.82
    if EMBLEM_SHADE is not None:
        top_shade, bottom_shade = EMBLEM_SHADE
    rgb = np.array(emblem).astype(float)
    shade = np.linspace(top_shade, bottom_shade, emblem.height)[:, None, None]      # lighter at the top, deeper below
    rgb[..., :3] = np.clip(rgb[..., :3] * shade, 0, 255)
    emblem = Image.fromarray(rgb.astype(np.uint8), "RGBA")
    if EMBLEM_SHARPEN:
        emblem = sharpen(emblem, *EMBLEM_SHARPEN, alpha=True)
    ex = (SIZE - emblem.width) // 2
    ey = EMBLEM_CENTRE_Y - emblem.height // 2
    emblem_mask = Image.new("L", (SIZE, SIZE), 0)
    emblem_mask.paste(emblem.getchannel("A"), (ex, ey))

    # its shadow onto the tile (kept inside the tile), then the emblem itself
    emblem_shadow = shifted(emblem_mask, 0, 12).filter(ImageFilter.GaussianBlur(14)).point(lambda v: int(v * 0.6))
    emblem_shadow = ImageChops.multiply(emblem_shadow, tile_mask)
    canvas.alpha_composite(layer(SIZE, (0, 0, 0), emblem_shadow))
    placed = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    placed.paste(emblem, (ex, ey))
    if glow:                                                          # the emblem's own colours, blurred, under it:
        bloom = placed.filter(ImageFilter.GaussianBlur(GLOW_BLUR))    # fine lines no longer sink into a dark tile
        bloom.putalpha(ImageChops.multiply(bloom.getchannel("A").point(lambda v: int(v * 0.7)), tile_mask))
        canvas.alpha_composite(bloom)
    canvas.alpha_composite(placed)

    # the bevel: the upper-left edges lit, the lower-right edges shaded (each band is the emblem minus itself shifted)
    lit = ImageChops.subtract(emblem_mask, shifted(emblem_mask, 3, 3)).filter(ImageFilter.GaussianBlur(1.2))
    lit = ImageChops.multiply(lit, emblem_mask).point(lambda v: int(v * 0.6))
    canvas.alpha_composite(layer(SIZE, (255, 255, 255), lit))
    shaded = ImageChops.subtract(emblem_mask, shifted(emblem_mask, -3, -3)).filter(ImageFilter.GaussianBlur(1.2))
    shaded = ImageChops.multiply(shaded, emblem_mask).point(lambda v: int(v * 0.5))
    canvas.alpha_composite(layer(SIZE, (0, 0, 0), shaded))
    return canvas


def sharpen(image, radius, percent, alpha=False):
    """An unsharp mask on an RGBA image's colour (and, if asked, its alpha - the edges themselves), threshold 2."""
    rgb = image.convert("RGB").filter(ImageFilter.UnsharpMask(radius=radius, percent=percent, threshold=2))
    a = image.getchannel("A")
    if alpha:
        a = a.filter(ImageFilter.UnsharpMask(radius=radius, percent=percent, threshold=2))
    out = rgb.convert("RGBA")
    out.putalpha(a)
    return out


def icon_at(icon, px):
    """The icon at one iconset size: a LANCZOS downscale, sharpened at the Dock's own sizes when ICON_SHARPEN is set."""
    small = icon.resize((px, px), Image.LANCZOS)
    if ICON_SHARPEN and px <= ICON_SHARPEN_MAX_PX:
        small = sharpen(small, *ICON_SHARPEN)
    return small


def write_icns(icon, path):
    """macOS's icon container for the app bundle: an iconset of the standard sizes, packed by the system's iconutil
    (macOS only - skipped elsewhere, the PNG is what the running app uses)."""
    import shutil
    import subprocess
    import tempfile
    if shutil.which("iconutil") is None:
        print("iconutil not found - no .icns written (not macOS?)")
        return False
    with tempfile.TemporaryDirectory() as tmp:
        iconset = Path(tmp) / "app.iconset"
        iconset.mkdir()
        for size in (16, 32, 128, 256, 512):
            icon_at(icon, size).save(iconset / f"icon_{size}x{size}.png")
            icon_at(icon, size * 2).save(iconset / f"icon_{size}x{size}@2x.png")
        subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(path)], check=True)
    return True


if __name__ == "__main__":
    icon = build()
    icon.save(OUT, optimize=True)
    if write_icns(icon, OUT.with_suffix(".icns")):
        print(f"wrote {OUT.with_suffix('.icns').relative_to(ROOT)}")
    alpha = icon.getchannel("A")
    print(f"wrote {OUT.relative_to(ROOT)} {icon.size}, tile bbox (with shadow) {alpha.getbbox()}, "
          f"opaque tile bbox {alpha.point(lambda v: 255 if v > 250 else 0).getbbox()}")
