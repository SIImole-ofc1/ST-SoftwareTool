"""Make the macOS app icons (rounded square on Apple's icon grid) from the ST logos.

    python packaging/macos/make_app_icon.py

writes
    packaging/macos/app-icon-1024.png       light icon — the build turns it into ST.icns
    packaging/macos/app-icon-dark-1024.png  dark icon
    assets/app-icon-light.png, assets/app-icon-dark.png   512 px copies the app shows in the
                                            Dock / Finder (Settings › App Icon, ui/app_icon.py)

macOS icons are a rounded square of 824 x 824 inside a 1024 x 1024 canvas with a soft
shadow; a plain square image looks out of place in the Dock and macOS 26 puts it in a
grey frame.
"""
import os

from PIL import Image, ImageDraw, ImageFilter

HERE = os.path.dirname(os.path.abspath(__file__))
ASSETS = os.path.join(HERE, "..", "..", "assets")

SIZE, BODY, RADIUS = 1024, 824, 185      # Apple's macOS icon grid
SS = 2                                    # supersampling for smooth corners

VARIANTS = {
    # name: (logo, hairline edge colour, 1024 output, 512 runtime copy). The dark logo's large
    # original lives here, not in assets/, so it isn't copied into every download.
    "light": (os.path.join(ASSETS, "STsoftwareterminalLOGO.png.png"), (0, 0, 0, 38),
              "app-icon-1024.png", "app-icon-light.png"),
    "dark":  (os.path.join(HERE, "STsoftwaretoolLOGOdark.png"), (255, 255, 255, 40),
              "app-icon-dark-1024.png", "app-icon-dark.png"),
}


def make_icon(logo_file: str, edge_colour) -> Image.Image:
    logo = Image.open(logo_file).convert("RGBA")
    # Both logos share one layout (the red block sits at the same place on a square canvas):
    # normalise to 1024, then zoom in a little so the mark fills the icon.
    logo = logo.resize((1024, 1024), Image.LANCZOS)
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
    for logo_file, edge, out_1024, out_512 in VARIANTS.values():
        icon = make_icon(logo_file, edge)
        icon.save(os.path.join(HERE, out_1024))
        icon.resize((512, 512), Image.LANCZOS).save(os.path.join(ASSETS, out_512), optimize=True)
        print("wrote", out_1024, "and assets/" + out_512)


if __name__ == "__main__":
    main()
