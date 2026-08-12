"""
Visible AI-mark remover (Gemini / Nano Banana sparkle, corner badges).

Paid tools (e.g. deletesynthid.com) explicitly remove the small visible
"Made with Google AI" sparkle that Gemini stamps in a corner, in addition
to invisible SynthID. Scrub previously only handled invisible + metadata.

Heuristic (no ML model required):

  * Scan the four corners for a compact high-contrast blob on a relatively
    flat local background (typical 48×48 / 96×96 sparkle).
  * Prefer bright minority clusters near the outer edge of the corner.
  * Build a soft mask and inpaint via neighbor averaging + edge-aware fill.
  * Conservative: if nothing looks like a badge, the image is unchanged.

This will not remove large artistic overlays or stock-photo watermarks —
those are out of scope (same policy as the paid tools' responsible-use pages).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Tuple

import numpy as np
from PIL import Image, ImageFilter


@dataclass(frozen=True)
class VisibleMarkHit:
    corner: str
    bbox: Tuple[int, int, int, int]  # left, top, right, bottom
    score: float


def _corner_rois(w: int, h: int) -> List[Tuple[str, Tuple[int, int, int, int]]]:
    # Gemini sparkles sit near the extreme corner; keep ROI large enough
    # (≈15% of short side, clamped) so 48–96px badges are always inside.
    short = min(w, h)
    box = max(64, min(192, int(short * 0.15)))
    inset = max(1, int(short * 0.005))
    return [
        ("br", (w - box - inset, h - box - inset, w - inset, h - inset)),
        ("bl", (inset, h - box - inset, inset + box, h - inset)),
        ("tr", (w - box - inset, inset, w - inset, inset + box)),
        ("tl", (inset, inset, inset + box, inset + box)),
    ]


def _score_badge(region: np.ndarray, corner: str) -> float:
    """
    Higher score → more likely a small high-contrast logo on a quieter patch.
    region: HxWx3 uint8
    """
    if region.size == 0 or region.shape[0] < 8 or region.shape[1] < 8:
        return 0.0
    gray = (
        0.299 * region[:, :, 0]
        + 0.587 * region[:, :, 1]
        + 0.114 * region[:, :, 2]
    ).astype(np.float64)

    gy, gx = np.gradient(gray)
    edge = np.hypot(gx, gy)
    edge_mean = float(edge.mean())
    edge_p90 = float(np.percentile(edge, 90))
    edge_p99 = float(np.percentile(edge, 99))

    lo, hi = np.percentile(gray, [8, 92])
    span = float(hi - lo)
    bright = gray >= (hi - 0.12 * max(span, 1))
    dark = gray <= (lo + 0.12 * max(span, 1))
    bright_frac = float(bright.mean())
    dark_frac = float(dark.mean())

    # Reject near-flat or fully busy (photo texture / post-noise) corners.
    if span < 22.0 or edge_mean < 1.5:
        return 0.0
    if edge_mean > 28.0 and (edge_p90 / (edge_mean + 1e-3)) < 1.6:
        # Uniformly busy → likely noise/texture, not a compact badge.
        return 0.0

    # Compactness of the bright cluster (Gemini sparkles are small & bright).
    if bright_frac < 0.008 or bright_frac > 0.42:
        bright_compact = 0.0
    else:
        ys, xs = np.where(bright)
        if ys.size < 4:
            bright_compact = 0.0
        else:
            # Normalized bounding-box area vs ROI — badges are localized.
            bb = (ys.max() - ys.min() + 1) * (xs.max() - xs.min() + 1)
            fill = ys.size / max(bb, 1)
            area_frac = bb / gray.size
            bright_compact = fill * (1.0 - min(1.0, area_frac * 2.5))

            # Prefer clusters near the outer corner of this ROI.
            cy = float(ys.mean()) / max(gray.shape[0] - 1, 1)
            cx = float(xs.mean()) / max(gray.shape[1] - 1, 1)
            if corner == "br":
                corner_prox = 0.5 * (cy + cx)
            elif corner == "bl":
                corner_prox = 0.5 * (cy + (1.0 - cx))
            elif corner == "tr":
                corner_prox = 0.5 * ((1.0 - cy) + cx)
            else:
                corner_prox = 0.5 * ((1.0 - cy) + (1.0 - cx))
            bright_compact *= 0.55 + 0.45 * corner_prox

    structure = edge_p99 / (edge_mean + 1e-3)
    score = structure * span * (0.2 + bright_compact)
    if 0.015 <= bright_frac <= 0.30 and span > 35 and bright_compact > 0.08:
        score *= 1.45
    if 0.015 <= dark_frac <= 0.30 and span > 35:
        score *= 1.08
    return float(score)


def detect_visible_marks(
    img: Image.Image,
    min_score: float = 48.0,
) -> List[VisibleMarkHit]:
    rgb = np.asarray(img.convert("RGB"), dtype=np.uint8)
    h, w = rgb.shape[:2]
    hits: List[VisibleMarkHit] = []
    for name, (l, t, r, b) in _corner_rois(w, h):
        l, t = max(0, l), max(0, t)
        r, b = min(w, r), min(h, b)
        score = _score_badge(rgb[t:b, l:r], name)
        if score >= min_score:
            hits.append(VisibleMarkHit(corner=name, bbox=(l, t, r, b), score=score))
    hits.sort(key=lambda x: x.score, reverse=True)
    return hits[:1]


def _inpaint_bbox(rgb: np.ndarray, bbox: Tuple[int, int, int, int]) -> np.ndarray:
    """
    Replace a detected corner-badge bbox with exterior context.

    Once detection has already decided "this corner is a badge", be aggressive:
    fill the whole ROI from the surrounding ring and feather only a few pixels
    at the inner boundary so the seam isn't obvious.
    """
    l, t, r, b = bbox
    h, w = rgb.shape[:2]
    out = rgb.astype(np.float64).copy()

    pad = 8
    L, T = max(0, l - pad), max(0, t - pad)
    R, B = min(w, r + pad), min(h, b + pad)
    ring = out[T:B, L:R]
    exterior = np.ones(ring.shape[:2], dtype=bool)
    exterior[(t - T):(b - T), (l - L):(r - L)] = False
    if exterior.any():
        fill = ring[exterior].mean(axis=0)
    else:
        fill = out[t:b, l:r].mean(axis=0)

    rh, rw = b - t, r - l
    yy, xx = np.mgrid[0:rh, 0:rw].astype(np.float64)
    # Distance from the outer corner (badge sits there) → 0 at outer corner.
    if l > w // 2:
        dx = (rw - 1 - xx) / max(rw - 1, 1)
    else:
        dx = xx / max(rw - 1, 1)
    if t > h // 2:
        dy = (rh - 1 - yy) / max(rh - 1, 1)
    else:
        dy = yy / max(rh - 1, 1)
    # Solid replace across almost the entire ROI; feather only the innermost rim.
    radial = np.maximum(dx, dy)
    alpha = np.ones((rh, rw), dtype=np.float64)
    feather = np.clip((0.22 - radial) / 0.22, 0.0, 1.0)  # 1 at inner rim
    alpha = 1.0 - 0.15 * feather  # still ≥0.85 everywhere

    a_img = Image.fromarray((alpha * 255).astype(np.uint8), mode="L")
    a_img = a_img.filter(ImageFilter.GaussianBlur(radius=1.2))
    alpha = np.asarray(a_img, dtype=np.float64) / 255.0
    alpha = np.maximum(alpha, 0.97)

    roi = out[t:b, l:r]
    blended = fill * alpha[:, :, None] + roi * (1.0 - alpha[:, :, None])
    out[t:b, l:r] = blended
    return np.clip(out, 0, 255).astype(np.uint8)


def remove_visible_marks(
    img: Image.Image,
    hits: Optional[List[VisibleMarkHit]] = None,
) -> Tuple[Image.Image, List[VisibleMarkHit]]:
    """
    Detect (if needed) and inpaint visible corner AI badges.
    Returns (new_image, hits_applied).
    """
    if hits is None:
        hits = detect_visible_marks(img)
    if not hits:
        return img.copy(), []

    had_alpha = img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info)
    alpha = None
    if had_alpha:
        rgba = img.convert("RGBA")
        alpha = rgba.split()[-1]
        rgb_img = rgba.convert("RGB")
    else:
        rgb_img = img.convert("RGB")

    arr = np.asarray(rgb_img, dtype=np.uint8)
    for hit in hits:
        arr = _inpaint_bbox(arr, hit.bbox)

    out = Image.fromarray(arr, mode="RGB")
    if had_alpha and alpha is not None:
        out = out.convert("RGBA")
        out.putalpha(alpha.resize(out.size, Image.LANCZOS))
    return out, hits
