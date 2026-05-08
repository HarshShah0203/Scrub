"""
Generate Scrub.icns — a macOS icon file — from a single 1024x1024 Pillow
rendering. Produces an .iconset folder with all required sizes, then runs
`iconutil -c icns` to assemble the final .icns.

No network, no design assets needed; just Pillow + the system iconutil.
"""

from __future__ import annotations

import os
import subprocess
import sys

from PIL import Image, ImageDraw, ImageFilter


HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
BUILD_DIR = os.path.join(ROOT, "build")
ICONSET = os.path.join(BUILD_DIR, "Scrub.iconset")
OUT_ICNS = os.path.join(BUILD_DIR, "Scrub.icns")


# macOS squircle corner ratio ~22% of the side.
CORNER_RATIO = 0.225

# Primary gradient — forest green → deep teal. Reads as "clean" without being
# clinical, and keeps strong contrast at tiny sizes.
GRAD_TOP    = (46, 125, 50, 255)     # #2E7D32
GRAD_BOT    = (20, 76, 70, 255)      # #144C46
GLYPH_FILL  = (255, 255, 255, 255)
GLYPH_SHADE = (255, 255, 255, 55)


def _rounded_rect_mask(size: int) -> Image.Image:
    mask = Image.new("L", (size, size), 0)
    d = ImageDraw.Draw(mask)
    r = int(size * CORNER_RATIO)
    d.rounded_rectangle((0, 0, size - 1, size - 1), radius=r, fill=255)
    return mask


def _gradient(size: int, top: tuple, bot: tuple) -> Image.Image:
    base = Image.new("RGBA", (size, size), top)
    for y in range(size):
        t = y / (size - 1)
        r = int(top[0] * (1 - t) + bot[0] * t)
        g = int(top[1] * (1 - t) + bot[1] * t)
        b = int(top[2] * (1 - t) + bot[2] * t)
        a = 255
        ImageDraw.Draw(base).line([(0, y), (size - 1, y)], fill=(r, g, b, a))
    return base


def draw_icon(size: int = 1024) -> Image.Image:
    """
    Icon = rounded squircle backdrop + a stylised "sparkle / scrub" glyph:
    a large soap-bubble circle with a checkmark slashed through it,
    representing "wipe clean & approved".
    """
    bg = _gradient(size, GRAD_TOP, GRAD_BOT)
    bg.putalpha(_rounded_rect_mask(size))

    # Highlight streak (top-left diagonal) to give the squircle a glassy feel.
    hl = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    hd = ImageDraw.Draw(hl)
    hd.polygon(
        [(0, 0),
         (size * 0.55, 0),
         (size * 0.0, size * 0.55)],
        fill=(255, 255, 255, 28),
    )
    bg = Image.alpha_composite(bg, hl)

    d = ImageDraw.Draw(bg)

    # Soap-bubble circle — heavy outer ring, thin inner highlight.
    cx, cy = size / 2, size * 0.48
    r = size * 0.30
    ring_w = int(size * 0.045)
    d.ellipse(
        [cx - r, cy - r, cx + r, cy + r],
        outline=GLYPH_FILL, width=ring_w,
    )
    # Sub-sparkle: small circle top-right of the main bubble (hint of shine).
    sr = size * 0.055
    d.ellipse(
        [cx + r * 0.55 - sr, cy - r * 0.75 - sr,
         cx + r * 0.55 + sr, cy - r * 0.75 + sr],
        fill=GLYPH_FILL,
    )
    # Soft highlight arc inside the bubble (thin).
    arc_w = int(size * 0.018)
    d.arc(
        [cx - r * 0.7, cy - r * 0.7, cx + r * 0.7, cy + r * 0.7],
        start=200, end=260, fill=GLYPH_SHADE, width=arc_w,
    )

    # Bold checkmark through the bubble — communicates "cleaned" / "OK".
    ck_w = int(size * 0.072)
    p1 = (cx - r * 0.55, cy + r * 0.05)
    p2 = (cx - r * 0.08, cy + r * 0.55)
    p3 = (cx + r * 0.70, cy - r * 0.45)
    d.line([p1, p2], fill=GLYPH_FILL, width=ck_w, joint="curve")
    d.line([p2, p3], fill=GLYPH_FILL, width=ck_w, joint="curve")
    # Round end-caps for the polyline.
    for pt in (p1, p2, p3):
        d.ellipse([pt[0] - ck_w / 2, pt[1] - ck_w / 2,
                   pt[0] + ck_w / 2, pt[1] + ck_w / 2], fill=GLYPH_FILL)

    # Wordmark band at the bottom to feel less abstract in the Dock.
    band = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    bd = ImageDraw.Draw(band)
    bw = int(size * 0.55)
    bh = int(size * 0.10)
    bx = int((size - bw) / 2)
    by = int(size * 0.82)
    bd.rounded_rectangle(
        [bx, by, bx + bw, by + bh],
        radius=int(bh / 2),
        fill=(255, 255, 255, 235),
    )
    # Word "SCRUB" drawn with geometric bars (avoids font dependency).
    _draw_wordmark(bd, "SCRUB", bx, by, bw, bh, color=(20, 76, 70, 255))
    bg = Image.alpha_composite(bg, band)

    # Subtle drop shadow baked into the alpha mask edge (only at large sizes
    # is this visible; macOS typically re-shadows anyway).
    if size >= 256:
        shadow = bg.split()[-1].filter(ImageFilter.GaussianBlur(size * 0.01))
        bg.putalpha(shadow)
        # Re-apply rounded mask so edges stay crisp.
        bg.putalpha(_rounded_rect_mask(size))

    return bg


def _draw_wordmark(d, text, bx, by, bw, bh, color):
    """
    Draw "SCRUB" with chunky geometric strokes so the wordmark reads at
    small sizes even without a bundled font.
    """
    # Very compact stylised letters — 5 letters across the band.
    n = len(text)
    cell_w = bw / n
    margin_x = cell_w * 0.18
    margin_y = bh * 0.22
    stroke = max(2, int(bh * 0.13))

    for i, ch in enumerate(text):
        x0 = bx + i * cell_w + margin_x
        y0 = by + margin_y
        x1 = bx + (i + 1) * cell_w - margin_x
        y1 = by + bh - margin_y
        _letter(d, ch, x0, y0, x1, y1, stroke, color)


def _letter(d, ch, x0, y0, x1, y1, w, color):
    midx = (x0 + x1) / 2
    midy = (y0 + y1) / 2
    if ch == "S":
        d.line([(x1, y0 + w), (x0, y0 + w)], fill=color, width=w)
        d.line([(x0, y0 + w), (x0, midy)], fill=color, width=w)
        d.line([(x0, midy), (x1, midy)], fill=color, width=w)
        d.line([(x1, midy), (x1, y1 - w)], fill=color, width=w)
        d.line([(x1, y1 - w), (x0, y1 - w)], fill=color, width=w)
    elif ch == "C":
        d.line([(x1, y0 + w), (x0, y0 + w)], fill=color, width=w)
        d.line([(x0, y0 + w), (x0, y1 - w)], fill=color, width=w)
        d.line([(x0, y1 - w), (x1, y1 - w)], fill=color, width=w)
    elif ch == "R":
        d.line([(x0, y0), (x0, y1)], fill=color, width=w)
        d.line([(x0, y0), (x1, y0)], fill=color, width=w)
        d.line([(x1, y0), (x1, midy)], fill=color, width=w)
        d.line([(x0, midy), (x1, midy)], fill=color, width=w)
        d.line([(x0, midy), (x1, y1)], fill=color, width=w)
    elif ch == "U":
        d.line([(x0, y0), (x0, y1 - w)], fill=color, width=w)
        d.line([(x1, y0), (x1, y1 - w)], fill=color, width=w)
        d.line([(x0, y1 - w), (x1, y1 - w)], fill=color, width=w)
    elif ch == "B":
        d.line([(x0, y0), (x0, y1)], fill=color, width=w)
        d.line([(x0, y0), (x1, y0)], fill=color, width=w)
        d.line([(x1, y0), (x1, midy)], fill=color, width=w)
        d.line([(x0, midy), (x1, midy)], fill=color, width=w)
        d.line([(x1, midy), (x1, y1)], fill=color, width=w)
        d.line([(x0, y1), (x1, y1)], fill=color, width=w)
    _ = midx  # kept for symmetry


def build_icns() -> str:
    # iconutil accepts ONLY this exact set of filenames.
    if os.path.exists(ICONSET):
        for fn in os.listdir(ICONSET):
            os.remove(os.path.join(ICONSET, fn))
    os.makedirs(ICONSET, exist_ok=True)
    master = draw_icon(1024)

    spec = [
        ("icon_16x16.png",       16),
        ("icon_16x16@2x.png",    32),
        ("icon_32x32.png",       32),
        ("icon_32x32@2x.png",    64),
        ("icon_128x128.png",     128),
        ("icon_128x128@2x.png",  256),
        ("icon_256x256.png",     256),
        ("icon_256x256@2x.png",  512),
        ("icon_512x512.png",     512),
        ("icon_512x512@2x.png",  1024),
    ]
    for name, s in spec:
        im = master.resize((s, s), Image.LANCZOS) if s != 1024 else master
        im.save(os.path.join(ICONSET, name))

    # iconutil (macOS) → .icns
    if sys.platform == "darwin":
        try:
            subprocess.run(
                ["iconutil", "-c", "icns",
                 "-o", OUT_ICNS, ICONSET],
                check=True, capture_output=True, text=True,
            )
            return OUT_ICNS
        except FileNotFoundError:
            raise RuntimeError(
                "iconutil not found. This must run on macOS."
            )
        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"iconutil failed: {e.stderr}")
    # Non-mac fallback: just ship the 1024 PNG.
    alt = os.path.join(BUILD_DIR, "Scrub.png")
    master.save(alt)
    return alt


if __name__ == "__main__":
    path = build_icns()
    print(f"wrote {path}")
