"""Make the macOS app icon (rounded square on Apple's icon grid) from the ST logo.

    python packaging/macos/make_app_icon.py   # writes packaging/macos/app-icon-1024.png

macOS icons are a rounded square of 824 x 824 inside a 1024 x 1024 canvas with a soft
shadow; a plain square image looks out of place in the Dock and macOS 26 puts it in a
grey frame. The build turns this PNG into ST.icns (sips + iconutil).
"""
import os

from PIL import Image, ImageDraw, ImageFilter

HERE = os.path.dirname(os.path.abspath(__file__))
LOGO = os.path.join(HERE, "..", "..", "assets", "STsoftwareterminalLOGO.png.png")

SIZE, BODY, RADIUS = 1024, 824, 185      # Apple's macOS icon grid
SS = 2                                    # supersampling for smooth corners


def main():
    logo = Image.open(LOGO).convert("RGBA")
    # zoom in a little so the red mark fills the icon (logo canvas has wide paper margins)
    w, h = logo.size
    crop = int(w * 0.13)
    logo = logo.crop((crop, crop - 10, w - crop, h - crop - 10))

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

    # hairline edge so the light icon doesn't melt into light backgrounds
    edge = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    ImageDraw.Draw(edge).rounded_rectangle([off, off, off + body - 1, off + body - 1], RADIUS * SS,
                                           outline=(0, 0, 0, 38), width=2 * SS)
    canvas.alpha_composite(edge)

    out = os.path.join(HERE, "app-icon-1024.png")
    canvas.resize((SIZE, SIZE), Image.LANCZOS).save(out)
    print("wrote", out)


if __name__ == "__main__":
    main()
