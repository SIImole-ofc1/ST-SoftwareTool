"""Draw the background of the macOS install window (the .dmg window with
"Drag ST-SoftwareTool to Applications").

    python packaging/macos/make_dmg_background.py            # writes dmg-background.png + @2x
    python packaging/macos/make_dmg_background.py --preview  # also a preview with icons + labels

Layout constants here must match packaging/macos/dmg_settings.py (window size and icon
centres). Finder draws the real icons and their labels on top of this picture; with a
background picture it always draws the labels in black, so the design stays light.
Needs Pillow + numpy and a bold UI font (Segoe UI on Windows, Helvetica/SF on macOS).
"""
import math
import os
import sys

import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))

# ── layout (points; @2x doubles everything) ──────────────────────────────────
W, H = 800, 450
APP_XY = (190, 195)           # icon centres, same as dmg_settings.py
APPS_XY = (610, 195)
ICON = 128
PANEL_W, PANEL_H, PANEL_R = 184, 212, 34
CAPTION_Y = 388

RED = (226, 35, 26)           # logo red
INK = (20, 20, 22)
PAPER = (248, 246, 242)

FONT_CANDIDATES = [
    r"C:\Windows\Fonts\segoeuib.ttf",
    "/System/Library/Fonts/SFNS.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
    "/Library/Fonts/Arial Bold.ttf",
]


def font(size):
    for path in FONT_CANDIDATES:
        if os.path.exists(path):
            return ImageFont.truetype(path, size)
    return ImageFont.load_default(size)


def radial(w, h, cx, cy, radius, color, strength):
    """RGBA layer with a soft radial glow."""
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    d = np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2) / radius
    a = np.clip(1.0 - d, 0, 1) ** 2.2 * strength
    layer = np.zeros((h, w, 4), np.uint8)
    layer[..., 0], layer[..., 1], layer[..., 2] = color
    layer[..., 3] = (a * 255).astype(np.uint8)
    return Image.fromarray(layer, "RGBA")


def rounded_mask(w, h, box, r):
    m = Image.new("L", (w, h), 0)
    ImageDraw.Draw(m).rounded_rectangle(box, r, fill=255)
    return m


def arrow_points(s):
    """Wavy path from the app panel to the Applications panel."""
    x0 = APP_XY[0] + PANEL_W / 2 + 14
    x1 = APPS_XY[0] - PANEL_W / 2 - 24
    yc = APP_XY[1]
    pts = []
    n = 900
    for i in range(n + 1):
        t = i / n
        settle = 1 - (max(0.0, t - 0.72) / 0.28) ** 2      # waves calm down into a straight finish
        y = yc + 22 * math.sin(2 * math.pi * 1.5 * t) * settle
        pts.append(((x0 + (x1 - x0) * t) * s, y * s))
    return pts


def draw_arrow(base, s, ss):
    """Red arrow with a black outline, fading in from the left (supersampled)."""
    k = s * ss
    w, h = W * k, H * k
    pts = arrow_points(k)
    red_w, outline = 8 * k, 3 * k
    black_w = red_w + 2 * outline

    # arrowhead (chevron) aligned with the end of the path
    (xa, ya), (xb, yb) = pts[-12], pts[-1]
    ang = math.atan2(yb - ya, xb - xa)
    arm = 26 * k
    tip = (xb + 2 * k * math.cos(ang), yb + 2 * k * math.sin(ang))
    wings = [(tip[0] - arm * math.cos(ang + sgn * 0.78), tip[1] - arm * math.sin(ang + sgn * 0.78))
             for sgn in (1, -1)]

    def brush(path_pts, width, d, color):
        r = width / 2
        for x, y in path_pts:
            d.ellipse([x - r, y - r, x + r, y + r], fill=color)

    def line_pts(a, b, step):
        n = max(2, int(math.dist(a, b) / step))
        return [(a[0] + (b[0] - a[0]) * i / n, a[1] + (b[1] - a[1]) * i / n) for i in range(n + 1)]

    def layer_for(width, color):
        lay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        d = ImageDraw.Draw(lay)
        step = max(1.0, width / 6)
        dense = []
        for a, b in zip(pts, pts[1:]):
            dense.extend(line_pts(a, b, step)[:-1])
        dense.append(pts[-1])
        brush(dense, width, d, color)
        for wing in wings:
            brush(line_pts(wing, tip, step), width, d, color)
        return lay

    def ramp_mask(start_frac, full_frac):
        x_start = pts[0][0] + (pts[-1][0] - pts[0][0]) * start_frac
        x_full = pts[0][0] + (pts[-1][0] - pts[0][0]) * full_frac
        ramp = np.clip((np.arange(w, dtype=np.float32) - x_start) / (x_full - x_start), 0, 1) ** 1.4
        return Image.fromarray(np.tile((ramp * 255).astype(np.uint8), (h, 1)), "L")

    black = layer_for(black_w, INK + (255,))
    red = layer_for(red_w, RED + (255,))
    black.putalpha(ImageChops.multiply(black.getchannel("A"), ramp_mask(0.10, 0.45)))
    red.putalpha(ImageChops.multiply(red.getchannel("A"), ramp_mask(0.0, 0.32)))
    layer = Image.alpha_composite(black, red)

    # soft shadow under the arrow
    shadow = Image.new("RGBA", layer.size, (0, 0, 0, 0))
    shadow.putalpha(black.getchannel("A").point(lambda v: v * 0.16))   # only under the solid part
    shadow = shadow.filter(ImageFilter.GaussianBlur(7 * k)).transform(
        shadow.size, Image.AFFINE, (1, 0, 0, 0, 1, -5 * k))

    out = Image.alpha_composite(shadow, layer).resize((W * s, H * s), Image.LANCZOS)
    base.alpha_composite(out)


def glass_panel(base, center, s, rng):
    """Frosted glass: blurred backdrop, milky tint, light rim and a soft shadow."""
    cx, cy = center[0] * s, (center[1] + 18) * s
    box = [int(cx - PANEL_W * s / 2), int(cy - PANEL_H * s / 2),
           int(cx + PANEL_W * s / 2), int(cy + PANEL_H * s / 2)]
    r = PANEL_R * s
    w, h = base.size

    # shadow
    sh = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    ImageDraw.Draw(sh).rounded_rectangle([box[0], box[1] + 10 * s, box[2], box[3] + 10 * s], r,
                                         fill=(40, 30, 40, 46))
    base.alpha_composite(sh.filter(ImageFilter.GaussianBlur(18 * s)))

    # blurred backdrop + milky tint
    mask = rounded_mask(w, h, box, r)
    frosted = base.filter(ImageFilter.GaussianBlur(22 * s))
    tint = Image.new("RGBA", (w, h), (255, 255, 255, 120))
    frosted = Image.alpha_composite(frosted, tint)
    # subtle top-to-bottom sheen
    sheen = np.zeros((h, w, 4), np.uint8)
    rows = np.clip((np.arange(h) - box[1]) / max(1, box[3] - box[1]), 0, 1)
    sheen[..., :3] = 255
    sheen[..., 3] = ((1 - rows) ** 2 * 70).astype(np.uint8)[:, None]
    frosted = Image.alpha_composite(frosted, Image.fromarray(sheen, "RGBA"))
    base.paste(frosted, (0, 0), mask)

    # rims: bright inner line, faint dark outer line
    d = ImageDraw.Draw(base)
    d.rounded_rectangle(box, r, outline=(255, 255, 255, 250), width=max(1, int(2 * s)))


def background(s):
    w, h = W * s, H * s
    rng = np.random.default_rng(7)
    img = Image.new("RGBA", (w, h), PAPER + (255,))
    # soft colour glows (warm red left, cool slate-blue right, a little light in the middle)
    for cx, cy, rad, col, a in [
        (-0.08, 0.20, 0.75, (236, 70, 60), 0.30),
        (0.10, 1.05, 0.55, (255, 160, 120), 0.20),
        (1.08, 0.85, 0.80, (92, 130, 210), 0.30),
        (0.95, -0.10, 0.50, (150, 120, 220), 0.16),
        (0.50, 0.45, 0.45, (255, 255, 255), 0.55),
    ]:
        img.alpha_composite(radial(w, h, cx * w, cy * h, rad * w, col, a))
    # colour blobs behind the panels so the frosted glass has something to blur
    img.alpha_composite(radial(w, h, (APP_XY[0] - 40) * s, (APP_XY[1] - 60) * s, 120 * s, RED, 0.38))
    img.alpha_composite(radial(w, h, (APPS_XY[0] + 50) * s, (APPS_XY[1] + 70) * s, 130 * s,
                               (70, 120, 220), 0.38))

    glass_panel(img, APP_XY, s, rng)
    glass_panel(img, APPS_XY, s, rng)
    draw_arrow(img, s, ss=3)

    # caption: "Drag ST-SoftwareTool to Applications" (app name in red)
    f = font(22 * s)
    parts = [("Drag ", INK), ("ST-SoftwareTool", RED), (" to Applications", INK)]
    d = ImageDraw.Draw(img)
    total = sum(d.textlength(t, font=f) for t, _ in parts)
    x = (w - total) / 2
    for text, col in parts:
        d.text((x, CAPTION_Y * s), text, font=f, fill=col + (255,), anchor="lm")
        x += d.textlength(text, font=f)

    # paper grain
    grain = rng.normal(0, 1, (h, w)).astype(np.float32)
    grain_img = Image.fromarray(np.clip(128 + grain * 9, 0, 255).astype(np.uint8), "L")
    noise = Image.merge("RGBA", (grain_img, grain_img, grain_img,
                                 Image.new("L", (w, h), 22)))
    img = Image.alpha_composite(img, Image.blend(
        Image.new("RGBA", (w, h), (128, 128, 128, 0)), noise, 1.0))
    return img.convert("RGB")


def preview(bg, s, out):
    """Approximate what Finder shows: icons and black labels on top of the background."""
    img = bg.convert("RGBA")
    logo = Image.open(os.path.join(HERE, "..", "..", "assets", "STsoftwareterminalLOGO.png.png")).convert("RGBA")
    logo = logo.resize((ICON * s, ICON * s), Image.LANCZOS)
    m = rounded_mask(ICON * s, ICON * s, [0, 0, ICON * s - 1, ICON * s - 1], 28 * s)
    logo.putalpha(ImageChops.multiply(logo.getchannel("A"), m))
    img.alpha_composite(logo, (APP_XY[0] * s - ICON * s // 2, APP_XY[1] * s - ICON * s // 2))
    # stand-in for the blue Applications folder
    d = ImageDraw.Draw(img)
    fx, fy = APPS_XY[0] * s - 56 * s, APPS_XY[1] * s - 40 * s
    d.rounded_rectangle([fx, fy - 12 * s, fx + 50 * s, fy + 10 * s], 8 * s, fill=(82, 160, 235, 255))
    d.rounded_rectangle([fx, fy, fx + 112 * s, fy + 86 * s], 12 * s, fill=(104, 182, 245, 255))
    lf = font(13 * s)
    for (cx, cy), label in [(APP_XY, "ST-SoftwareTool"), (APPS_XY, "Applications")]:
        d.text((cx * s, (cy + ICON // 2 + 14) * s), label, font=lf, fill=(0, 0, 0, 255), anchor="mm")
    img.convert("RGB").save(out)


if __name__ == "__main__":
    for scale, name in ((1, "dmg-background.png"), (2, "dmg-background@2x.png")):
        bg = background(scale)
        bg.save(os.path.join(HERE, name), dpi=(72 * scale, 72 * scale))
        print("wrote", name, bg.size)
    if "--preview" in sys.argv:
        out = sys.argv[sys.argv.index("--preview") + 1] if len(sys.argv) > sys.argv.index("--preview") + 1 \
            else os.path.join(HERE, "preview.png")
        preview(background(2), 2, out)
        print("wrote preview", out)
