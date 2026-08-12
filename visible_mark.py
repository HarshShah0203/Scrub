"""
Visible AI-mark remover (Gemini / Nano Banana sparkle, corner badges).

Paid tools (e.g. deletesynthid.com) explicitly remove the small visible
"Made with Google AI" sparkle that Gemini stamps in a corner, in addition
to invisible SynthID. Scrub previously only handled invisible + metadata.

Heuristic (no ML model required):

  * Scan the four corners for a compact high-contrast blob on a relatively
    flat local background (typical 48×48 / 96×96 sparkle).
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
    # Badge is typically ≤ ~7% of the short side, inset a few px from the edge.
    short = min(w, h)
    box = max(48, min(160, int(short * 0.12)))
    inset = max(2, int(short * 0.01))
    return [
        ("br", (w - box - inset, h - box - inset, w - inset, h - inset)),
        ("bl", (inset, h - box - inset, inset + box, h - inset)),
        ("tr", (w - box - inset, inset, w - inset, inset + box)),
        ("tl", (inset, inset, inset + box, inset + box)),
    ]


def _score_badge(region: np.ndarray) -> float:
    """
    Higher score → more likely a small high-contrast logo on a quieter patch.
    region: HxWx3 uint8
    """
    if region.size == 0:
        return 0.0
    gray = (
        0.299 * region[:, :, 0]
        + 0.587 * region[:, :, 1]
        + 0.114 * region[:, :, 2]
    ).astype(np.float64)

    # Local contrast via Laplacian-ish energy.
    gy, gx = np.gradient(gray)
    edge = np.hypot(gx, gy)
    edge_mean = float(edge.mean())
    edge_p90 = float(np.percentile(edge, 90))

    # Badges tend to have a bright/dark cluster occupying a minority of pixels.
    lo, hi = np.percentile(gray, [10, 90])
    span = float(hi - lo)
    bright_frac = float((gray > (hi - 0.15 * max(span, 1))).mean())
    dark_frac = float((gray < (lo + 0.15 * max(span, 1))).mean())
    minority = min(bright_frac, 1.0 - bright_frac)

    # Prefer compact high-edge minority structures; penalize busy textures.
    if edge_mean < 2.0 or span < 18.0:
        return 0.0
    score = (edge_p90 / (edge_mean + 1e-3)) * span * (0.15 + minority)
    # Extra boost when a small bright minority sits on a mid/dark field
    # (classic white sparkle).
    if 0.02 <= bright_frac <= 0.28 and span > 40:
        score *= 1.35
    if 0.02 <= dark_frac <= 0.28 and span > 40:
        score *= 1.15
    return float(score)


def detect_visible_marks(
    img: Image.Image,
    min_score: float = 55.0,
) -> List[VisibleMarkHit]:
    rgb = np.asarray(img.convert("RGB"), dtype=np.uint8)
    h, w = rgb.shape[:2]
    hits: List[VisibleMarkHit] = []
    for name, (l, t, r, b) in _corner_rois(w, h):
        l, t = max(0, l), max(0, t)
        r, b = min(w, r), min(h, b)
        score = _score_badge(rgb[t:b, l:r])
        if score >= min_score:
            hits.append(VisibleMarkHit(corner=name, bbox=(l, t, r, b), score=score))
    # Keep at most the strongest hit — Gemini stamps one sparkle.
    hits.sort(key=lambda x: x.score, reverse=True)
    return hits[:1]


def _inpaint_bbox(rgb: np.ndarray, bbox: Tuple[int, int, int, int]) -> np.ndarray:
    """
    Soft-inpaint a bbox by expanding a mask around high-edge pixels and
    filling from surrounding ring statistics (no OpenCV dependency).
    """
    l, t, r, b = bbox
    h, w = rgb.shape[:2]
    out = rgb.astype(np.float64).copy()

    # Focus mask on the high-edge core inside the ROI rather than the whole box.
    roi = rgb[t:b, l:r].astype(np.float64)
    gray = 0.299 * roi[:, :, 0] + 0.587 * roi[:, :, 1] + 0.114 * roi[:, :, 2]
    gy, gx = np.gradient(gray)
    edge = np.hypot(gx, gy)
    thr = float(np.percentile(edge, 70))
    core = edge >= thr

    # Dilate core a few pixels.
    mask = core.copy()
    for _ in range(3):
        padded = np.pad(mask.astype(np.uint8), 1, mode="edge")
        dil = np.zeros_like(mask, dtype=bool)
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                dil |= padded[1 + dy : 1 + dy + mask.shape[0],
                              1 + dx : 1 + dx + mask.shape[1]].astype(bool)
        mask = dil

    if not mask.any():
        # Fallback: soft ellipse in the corner-most half of the ROI.
        yy, xx = np.mgrid[0:mask.shape[0], 0:mask.shape[1]]
        cy, cx = mask.shape[0] * 0.65, mask.shape[1] * 0.65
        # Bias ellipse toward the outer corner depending on bbox position.
        if l > w // 2:
            cx = mask.shape[1] * 0.7
        else:
            cx = mask.shape[1] * 0.3
        if t > h // 2:
            cy = mask.shape[0] * 0.7
        else:
            cy = mask.shape[0] * 0.3
        ry, rx = mask.shape[0] * 0.35, mask.shape[1] * 0.35
        mask = ((yy - cy) / max(ry, 1)) ** 2 + ((xx - cx) / max(rx, 1)) ** 2 <= 1.0

    # Ring just outside the mask for fill color.
    padded = np.pad(mask.astype(np.uint8), 2, mode="edge")
    ring = np.zeros_like(mask, dtype=bool)
    for dy in range(-2, 3):
        for dx in range(-2, 3):
            ring |= padded[2 + dy : 2 + dy + mask.shape[0],
                           2 + dx : 2 + dx + mask.shape[1]].astype(bool)
    ring &= ~mask
    if not ring.any():
        ring = ~mask

    fill = roi[ring].mean(axis=0) if ring.any() else roi.mean(axis=0)

    # Multi-pass pull toward neighbors outside the mask.
    work = roi.copy()
    for _ in range(12):
        padded = np.pad(work, ((1, 1), (1, 1), (0, 0)), mode="edge")
        avg = (
            padded[0:-2, 1:-1] + padded[2:, 1:-1]
            + padded[1:-1, 0:-2] + padded[1:-1, 2:]
        ) / 4.0
        # Also bias toward ring mean so busy interiors settle.
        avg = 0.7 * avg + 0.3 * fill
        work = np.where(mask[:, :, None], avg, work)

    # Feather: blur mask and lerp.
    mask_img = Image.fromarray((mask.astype(np.uint8) * 255), mode="L")
    mask_img = mask_img.filter(ImageFilter.GaussianBlur(radius=2.0))
    alpha = np.asarray(mask_img, dtype=np.float64) / 255.0
    blended = work * alpha[:, :, None] + roi * (1.0 - alpha[:, :, None])
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
