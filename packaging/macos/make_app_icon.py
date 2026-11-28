"""Make the macOS app icons (rounded square on Apple's icon grid) from the ST logos.

    python packaging/macos/make_app_icon.py            (all icons)
    python packaging/macos/make_app_icon.py special    (only the named ones)

writes
    packaging/macos/app-icon-1024.png       light icon — the build turns it into ST.icns
    packaging/macos/app-icon-dark-1024.png  dark icon
    packaging/macos/app-icon-special-1024.png   special icon (unlocked with a code)
    packaging/macos/app-icon-special2-1024.png  second special icon (another code)
    assets/app-icon-light.png, assets/app-icon-dark.png, assets/app-icon-special.png,
    assets/app-icon-special2.png
                                            512 px copies the app shows in the Dock / Finder
                                            (Settings › App Icon, ui/app_icon.py)

macOS icons are a rounded square of 824 x 824 inside a 1024 x 1024 canvas with a soft
shadow; a plain square image looks out of place in the Dock and macOS 26 puts it in a
grey frame.
"""
import os
import sys

from PIL import Image, ImageDraw, ImageFilter

HERE = os.path.dirname(os.path.abspath(__file__))
ASSETS = os.path.join(HERE, "..", "..", "assets")
PRIVATE = os.path.join(HERE, "private")

SIZE, BODY, RADIUS = 1024, 824, 185      # Apple's macOS icon grid
SS = 2                                    # supersampling for smooth corners

VARIANTS = {
    # name: (logo, hairline edge colour, 1024 output, 512 runtime copy, crop the ST layout).
    # The dark and special originals are large, so they live here, not in assets/, and are
    # not copied into every download.
    "light":   (os.path.join(ASSETS, "STsoftwareterminalLOGO.png.png"), (0, 0, 0, 38),
                "app-icon-1024.png", "app-icon-light.png", True),
    "dark":    (os.path.join(HERE, "STsoftwaretoolLOGOdark.png"), (255, 255, 255, 40),
                "app-icon-dark-1024.png", "app-icon-dark.png", True),
    # a full picture (no ST layout): used whole
    # Special icons live in packaging/macos/private/ (never committed): after making them, seal
    # each with its code: python scripts/pack_assets.py --special special2 '<code>' \
    #     packaging/macos/private/app-icon-special2.png
    "special": (os.path.join(PRIVATE, "STsoftwaretoolLOGOspecial.png"), (255, 255, 255, 46),
                "private/app-icon-special-1024.png", "../packaging/macos/private/app-icon-special.png", False),
    "special2": (os.path.join(PRIVATE, "STsoftwaretoolLOGOspecial2.png"), (255, 255, 255, 46),
                 "private/app-icon-special2-1024.png", "../packaging/macos/private/app-icon-special2.png", False),
}


def make_icon(logo_file: str, edge_colour, st_crop: bool = True) -> Image.Image:
    logo = Image.open(logo_file).convert("RGBA")
    logo = logo.resize((1024, 1024), Image.LANCZOS)
    if st_crop:
        # The ST logos share one layout (the red block sits at the same place on a square
        # canvas): zoom in a little so the mark fills the icon.
        crop = int(1024 * 0.13)
        logo = logo.crop((crop, crop - 10, 1024 - crop, 1024 - crop - 10))

    s = SIZE * SS
    body = BODY * SS
    off = (s - body) // 2
    canvas = Image.new("RGBA", (s, s), (0, 0, 0, 0))

    # soft drop shadow like system icons
    shadow = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle(
        [off, off + 14 * SS, off + body, off + body + 14 * SS], RADIUS * SS, fill=(0, 0, 0, 90))
    canvas.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(18 * SS)))

    mask = Image.new("L", (body, body), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, body - 1, body - 1], RADIUS * SS, fill=255)
    art = logo.resize((body, body), Image.LANCZOS)
    art.putalpha(mask)
    canvas.alpha_composite(art, (off, off))

    # hairline edge so the icon doesn't melt into a background of the same brightness
    edge = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    ImageDraw.Draw(edge).rounded_rectangle([off, off, off + body - 1, off + body - 1], RADIUS * SS,
                                           outline=edge_colour, width=2 * SS)
    canvas.alpha_composite(edge)
    return canvas.resize((SIZE, SIZE), Image.LANCZOS)


def main():
    names = sys.argv[1:] or list(VARIANTS)
    for name in names:
        logo_file, edge, out_1024, out_512, st_crop = VARIANTS[name]
        icon = make_icon(logo_file, edge, st_crop)
        icon.save(os.path.join(HERE, out_1024))
        icon.resize((512, 512), Image.LANCZOS).save(os.path.join(ASSETS, out_512), optimize=True)
        print("wrote", out_1024, "and assets/" + out_512)


if __name__ == "__main__":
    main()
