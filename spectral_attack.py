"""
Frequency-domain (spectral) attack for SynthID-class pixel watermarks.

Paid removers advertise "frequency-domain pattern" disruption. This module
implements a research-aligned *local* equivalent using only NumPy FFTs:

  1. Convert to YCbCr; attack luma hardest.
  2. Adaptive carrier detection: find mid-band magnitude peaks that look like
     fixed-resolution periodic watermarks (local z-score + conjugate pair).
  3. Multi-pass surgical dampening of those bins (open stand-in for a
     proprietary SpectralCodebook) + mid-band phase jitter.
  4. Inverse FFT, blend back for PSNR control.

No cloud. No fingerprint DB download. Carriers are estimated per-image.

Nothing here is a cryptographic guarantee.
"""

from __future__ import annotations

import math
from typing import Literal, Optional, Tuple

import numpy as np
from PIL import Image


SpectralStrength = Literal["light", "medium", "strong"]


def _params(strength: SpectralStrength) -> dict:
    if strength == "light":
        return {
            "phase_sigma": 0.08,
            "peak_dampen": 0.55,      # surgical damp on detected carriers
            "global_dampen": 0.08,    # soft damp on remaining mid-band outliers
            "blend": 0.62,
            "r_lo": 0.04,
            "r_hi": 0.55,
            "passes": 2,
            "z_thresh": 3.5,
            "max_carriers": 48,
        }
    if strength == "strong":
        return {
            "phase_sigma": 0.28,
            "peak_dampen": 0.85,
            "global_dampen": 0.30,
            "blend": 0.22,
            "r_lo": 0.03,
            "r_hi": 0.72,
            "passes": 4,
            "z_thresh": 2.6,
            "max_carriers": 160,
        }
    return {
        "phase_sigma": 0.16,
        "peak_dampen": 0.72,
        "global_dampen": 0.18,
        "blend": 0.40,
        "r_lo": 0.035,
        "r_hi": 0.62,
        "passes": 3,
        "z_thresh": 3.0,
        "max_carriers": 96,
    }


def _rgb_to_ycbcr(rgb: np.ndarray) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    r = rgb[:, :, 0].astype(np.float64)
    g = rgb[:, :, 1].astype(np.float64)
    b = rgb[:, :, 2].astype(np.float64)
    y = 0.299 * r + 0.587 * g + 0.114 * b
    cb = 128.0 - 0.168736 * r - 0.331264 * g + 0.5 * b
    cr = 128.0 + 0.5 * r - 0.418688 * g - 0.081312 * b
    return y, cb, cr


def _ycbcr_to_rgb(y: np.ndarray, cb: np.ndarray, cr: np.ndarray) -> np.ndarray:
    r = y + 1.402 * (cr - 128.0)
    g = y - 0.344136 * (cb - 128.0) - 0.714136 * (cr - 128.0)
    b = y + 1.772 * (cb - 128.0)
    out = np.stack([r, g, b], axis=-1)
    return np.clip(out, 0.0, 255.0).astype(np.uint8)


def _midband_mask(h: int, w: int, r_lo: float, r_hi: float) -> np.ndarray:
    yy, xx = np.mgrid[0:h, 0:w]
    cy, cx = h / 2.0, w / 2.0
    rr = np.sqrt(((yy - cy) / max(cy, 1)) ** 2 + ((xx - cx) / max(cx, 1)) ** 2)
    rr = rr / math.sqrt(2.0)
    return (rr >= r_lo) & (rr <= r_hi)


def _detect_carriers(
    mag: np.ndarray,
    mask: np.ndarray,
    z_thresh: float,
    max_carriers: int,
) -> np.ndarray:
    """
    Return a boolean mask of adaptive 'carrier' bins: mid-band peaks that are
    strong vs a local neighborhood and have a conjugate partner (real-image FFT).
    """
    h, w = mag.shape
    cy, cx = h // 2, w // 2
    carriers = np.zeros_like(mask, dtype=bool)

    # Local 5x5 median as background estimate (ignore DC neighborhood).
    from numpy.lib.stride_tricks import sliding_window_view

    # Pad so edge bins still get a neighborhood.
    pad = 2
    padded = np.pad(mag, pad, mode="edge")
    windows = sliding_window_view(padded, (5, 5))
    local_med = np.median(windows, axis=(-1, -2))
    local_mad = np.median(np.abs(windows - local_med[:, :, None, None]), axis=(-1, -2)) + 1e-6
    z = (mag - local_med) / local_mad

    cand = mask & (z >= z_thresh)
    # Rank by z-score; keep top-N, always include conjugate.
    ys, xs = np.where(cand)
    if ys.size == 0:
        return carriers

    scores = z[ys, xs]
    order = np.argsort(scores)[::-1]
    picked = 0
    for idx in order:
        if picked >= max_carriers:
            break
        y, x = int(ys[idx]), int(xs[idx])
        # Skip exact DC.
        if y == cy and x == cx:
            continue
        if carriers[y, x]:
            continue
        carriers[y, x] = True
        # Conjugate partner in fftshift layout: (2*cy - y) % h, (2*cx - x) % w
        y2 = (2 * cy - y) % h
        x2 = (2 * cx - x) % w
        carriers[y2, x2] = True
        picked += 1
    return carriers


def _attack_channel(
    channel: np.ndarray,
    rng: np.random.Generator,
    phase_sigma: float,
    peak_dampen: float,
    global_dampen: float,
    r_lo: float,
    r_hi: float,
    passes: int,
    z_thresh: float,
    max_carriers: int,
) -> Tuple[np.ndarray, int]:
    h, w = channel.shape
    work = channel.astype(np.float64)
    total_carriers = 0

    for _ in range(max(1, int(passes))):
        F = np.fft.fftshift(np.fft.fft2(work))
        mag = np.abs(F)
        phase = np.angle(F)
        mask = _midband_mask(h, w, r_lo, r_hi)

        carriers = _detect_carriers(mag, mask, z_thresh=z_thresh, max_carriers=max_carriers)
        total_carriers += int(carriers.sum())

        # Surgical damp on detected carriers (leave a small residual).
        if peak_dampen > 0 and carriers.any():
            mag = mag.copy()
            mag[carriers] = mag[carriers] * (1.0 - peak_dampen)

        # Soft damp remaining mid-band outliers.
        if global_dampen > 0:
            band = mag[mask]
            if band.size > 16:
                med = float(np.median(band))
                mad = float(np.median(np.abs(band - med))) + 1e-6
                thresh = med + 3.0 * mad
                hot = mask & (~carriers) & (mag > thresh)
                mag = mag.copy()
                mag[hot] = mag[hot] * (1.0 - global_dampen) + med * global_dampen

        if phase_sigma > 0:
            jitter = rng.normal(0.0, phase_sigma, size=phase.shape)
            # Heavier jitter on carrier bins.
            phase = phase + np.where(carriers, jitter * 1.8, np.where(mask, jitter, 0.0))

        F2 = mag * np.exp(1j * phase)
        work = np.fft.ifft2(np.fft.ifftshift(F2)).real

    return work, total_carriers


def spectral_attack_image(
    img: Image.Image,
    strength: SpectralStrength = "medium",
    seed: Optional[int] = None,
    fidelity_first: bool = False,
) -> Tuple[Image.Image, dict]:
    """
    Apply spectral disruption to a Pillow image. Preserves alpha if present.

    Returns (image, stats) where stats includes carrier bin counts etc.
    If fidelity_first=True, use a stronger surgical spectral pass with a
    higher blend-back (closer to paid "visually identical" claims).
    """
    p = _params(strength)
    if fidelity_first:
        # Push surgical damp up, keep more of the original spatial look.
        p = dict(p)
        p["peak_dampen"] = min(0.92, p["peak_dampen"] + 0.12)
        p["passes"] = max(p["passes"], 3)
        p["blend"] = min(0.78, p["blend"] + 0.18)
        p["phase_sigma"] = max(0.05, p["phase_sigma"] * 0.7)

    rng = np.random.default_rng(seed)

    had_alpha = img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info)
    alpha = None
    if had_alpha:
        rgba = img.convert("RGBA")
        alpha = rgba.split()[-1]
        rgb_img = rgba.convert("RGB")
    else:
        rgb_img = img.convert("RGB")

    rgb = np.asarray(rgb_img, dtype=np.uint8)
    y, cb, cr = _rgb_to_ycbcr(rgb)

    y2, c_y = _attack_channel(
        y, rng,
        phase_sigma=p["phase_sigma"],
        peak_dampen=p["peak_dampen"],
        global_dampen=p["global_dampen"],
        r_lo=p["r_lo"], r_hi=p["r_hi"],
        passes=p["passes"], z_thresh=p["z_thresh"],
        max_carriers=p["max_carriers"],
    )
    cb2, c_cb = _attack_channel(
        cb, rng,
        phase_sigma=p["phase_sigma"] * 0.45,
        peak_dampen=p["peak_dampen"] * 0.55,
        global_dampen=p["global_dampen"] * 0.5,
        r_lo=p["r_lo"], r_hi=p["r_hi"],
        passes=max(1, p["passes"] - 1),
        z_thresh=p["z_thresh"] + 0.4,
        max_carriers=max(16, p["max_carriers"] // 2),
    )
    cr2, c_cr = _attack_channel(
        cr, rng,
        phase_sigma=p["phase_sigma"] * 0.45,
        peak_dampen=p["peak_dampen"] * 0.55,
        global_dampen=p["global_dampen"] * 0.5,
        r_lo=p["r_lo"], r_hi=p["r_hi"],
        passes=max(1, p["passes"] - 1),
        z_thresh=p["z_thresh"] + 0.4,
        max_carriers=max(16, p["max_carriers"] // 2),
    )

    attacked = _ycbcr_to_rgb(y2, cb2, cr2).astype(np.float64)
    orig = rgb.astype(np.float64)
    blend = float(p["blend"])
    mixed = blend * orig + (1.0 - blend) * attacked
    out = Image.fromarray(np.clip(mixed, 0, 255).astype(np.uint8), mode="RGB")

    if had_alpha and alpha is not None:
        out = out.convert("RGBA")
        out.putalpha(alpha.resize(out.size, Image.LANCZOS))

    stats = {
        "strength": strength,
        "fidelity_first": fidelity_first,
        "carrier_bins_y": c_y,
        "carrier_bins_cb": c_cb,
        "carrier_bins_cr": c_cr,
        "passes": p["passes"],
        "blend": blend,
    }
    return out, stats


def spectral_strength_for(spatial_strength: str) -> SpectralStrength:
    """Map Scrub's spatial strength presets onto a spectral preset."""
    if spatial_strength in ("near_lossless", "light"):
        return "light"
    if spatial_strength == "strong":
        return "strong"
    return "medium"
