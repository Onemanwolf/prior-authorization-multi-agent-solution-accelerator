# /// script
# requires-python = ">=3.11"
# dependencies = ["resvg-py>=0.2", "pillow>=10"]
# ///
"""Render the Teams icon options and choose the active icon.

    uv run teams/icons/render_icons.py              # render every option + preview.png
    uv run teams/icons/render_icons.py --select 2   # also make option 2 the active icon

Each option folder holds color.svg (192x192) and outline.svg (32x32, white on
transparent). Rendering writes the matching PNGs next to them. --select copies
an option's PNGs to teams/icons/color.png and outline.png, which
build_package.py puts into the Teams app package.
"""

import argparse
import shutil
import sys
from io import BytesIO
from pathlib import Path

import resvg_py
from PIL import Image, ImageDraw, ImageFont

ICONS = Path(__file__).resolve().parent
OPTIONS = ICONS / "options"
SIZES = {"color": 192, "outline": 32}


def render(svg: Path, size: int) -> Image.Image:
    png = resvg_py.svg_to_bytes(svg_path=str(svg), width=size, height=size)
    return Image.open(BytesIO(bytes(png))).convert("RGBA")


def option_dirs() -> list[Path]:
    return sorted(p for p in OPTIONS.iterdir() if p.is_dir() and (p / "color.svg").exists())


def check_outline(img: Image.Image, name: str) -> None:
    """Teams rejects outline icons that are not white on transparent."""
    raw = img.tobytes()
    for i in range(0, len(raw), 4):
        r, g, b, a = raw[i:i + 4]
        if a and (r, g, b) != (255, 255, 255):
            sys.exit(f"{name}: outline icon has a non-white pixel ({r},{g},{b})")


def rounded(img: Image.Image, size: int, radius: int) -> Image.Image:
    img = img.resize((size, size), Image.LANCZOS)
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, size - 1, size - 1), radius, fill=255)
    img.putalpha(mask)
    return img


def font(size: int) -> ImageFont.ImageFont:
    for f in ("/System/Library/Fonts/Supplemental/Arial Bold.ttf",
              "/System/Library/Fonts/Helvetica.ttc",
              "/Library/Fonts/Arial Unicode.ttf"):
        try:
            return ImageFont.truetype(f, size)
        except OSError:
            continue
    return ImageFont.load_default()


def preview(rendered: list[tuple[str, Image.Image, Image.Image]]) -> Image.Image:
    """One column per option: full icon, Teams app-bar size on light and dark, outline."""
    col, pad = 300, 28
    sheet = Image.new("RGB", (col * len(rendered) + pad, 520), "#FFFFFF")
    draw = ImageDraw.Draw(sheet)
    title, label = font(22), font(15)
    for i, (name, color, outline) in enumerate(rendered):
        x = pad + i * col
        draw.text((x, 20), name, fill="#171717", font=title)
        sheet.paste(rounded(color, 192, 36), (x, 60), rounded(color, 192, 36))
        # App bar size in Teams (about 44 px), light and dark themes.
        for j, (bg, fg) in enumerate((("#F5F5F5", "#424242"), ("#1F1F1F", "#D6D6D6"))):
            y = 280 + j * 70
            draw.rounded_rectangle((x, y, x + 250, y + 60), 10, fill=bg)
            small = rounded(color, 44, 10)
            sheet.paste(small, (x + 8, y + 8), small)
            draw.text((x + 64, y + 20), "Supply Assistant", fill=fg, font=label)
        # Outline icon on the Teams purple it is shown against, at 1x and 2x.
        y = 430
        draw.rounded_rectangle((x, y, x + 250, y + 72), 10, fill="#464775")
        sheet.paste(outline, (x + 16, y + 20), outline)
        big = outline.resize((64, 64), Image.NEAREST)
        sheet.paste(big, (x + 70, y + 4), big)
        draw.text((x + 150, y + 28), "outline", fill="#FFFFFF", font=label)
    return sheet


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--select", type=int, help="option number to make the active Teams icon")
    args = parser.parse_args()

    rendered = []
    for opt in option_dirs():
        imgs = {}
        for kind, size in SIZES.items():
            img = render(opt / f"{kind}.svg", size)
            if kind == "outline":
                check_outline(img, opt.name)
            img.save(opt / f"{kind}.png", optimize=True)
            imgs[kind] = img
        rendered.append((opt.name, imgs["color"], imgs["outline"]))
        print(f"rendered {opt.name}")

    preview(rendered).save(OPTIONS / "preview.png", optimize=True)
    print(f"wrote {OPTIONS / 'preview.png'}")

    if args.select is not None:
        match = [o for o in option_dirs() if o.name.split("-", 1)[0] == str(args.select)]
        if not match:
            sys.exit(f"no option {args.select}; have: {', '.join(o.name for o in option_dirs())}")
        for kind in SIZES:
            shutil.copyfile(match[0] / f"{kind}.png", ICONS / f"{kind}.png")
        print(f"active icon is now {match[0].name}")


if __name__ == "__main__":
    main()
