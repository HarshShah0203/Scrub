"""
Invisible-watermark attack pipelines for images, video, and audio.

This module implements *signal-processing attacks* whose individual steps are
each known from the academic watermarking-attack literature to degrade or
destroy one or more invisible-watermark families:

    - pixel-noise carriers (e.g. Google SynthID for images, some per-frame
      video watermarks): random sub-LSB noise + Gaussian smoothing pushes
      the carrier below detector threshold while leaving the image
      perceptually intact.
    - frequency-domain carriers (DCT, Tree-Ring, Stable-Signature latent
      rings): a small resize cycle (0.92x - 0.98x then back via Lanczos)
      perturbs the spectral coefficients the detector keys on.
    - registration-sensitive carriers: a 1-10 px asymmetric crop+pad
      shifts the detection grid.
    - JPEG/DCT quant-table watermarks: a JPEG round-trip at a new quality
      re-quantizes every mid-frequency coefficient.
    - audio spectral-mask watermarks (SynthID-Audio, Meta AudioSeal,
      Stable-Signature audio): resample 48->44.1->48 kHz + mild hp/lp
      + codec swap obliterates the masked bins.

Nothing here is a cryptographic guarantee. Strength settings trade
fidelity for watermark erasure. "Medium" is the sensible default for
nearly all real-world uploads.

For the strongest attack described in the research literature
(ControlNet-guided low-denoise diffusion regeneration, a la
github.com/00quebec/Synthid-Bypass V2), see `diffusion_regen_image`,
which is a *optional* path that only activates if `diffusers` + `torch`
are installed. It is intentionally not a hard dependency of this tool.
"""

from __future__ import annotations

import io
import os
import random
import shutil
import subprocess
from dataclasses import dataclass
from typing import Literal, Optional, Tuple

import numpy as np
from PIL import Image, ImageFilter, ImageEnhance

from origin_detect import OriginReport, detect_origin
from spectral_attack import spectral_attack_image, spectral_strength_for
from visible_mark import remove_visible_marks


Strength = Literal["near_lossless", "light", "medium", "strong"]


IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff", ".heic", ".heif", ".avif"}
VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".mkv", ".webm", ".avi", ".mpeg", ".mpg", ".ogv"}
AUDIO_EXTS = {".mp3", ".m4a", ".aac", ".wav", ".flac", ".ogg", ".oga", ".opus", ".wma"}


@dataclass(frozen=True)
class AttackResult:
    output_path: str
    detail: str


def _ext_lower(path: str) -> str:
    return os.path.splitext(path)[1].lower()


def _unique(out_path: str) -> str:
    if not os.path.exists(out_path):
        return out_path
    base, ext = os.path.splitext(out_path)
    i = 1
    while True:
        cand = f"{base}_{i}{ext}"
        if not os.path.exists(cand):
            return cand
        i += 1


def _run(cmd: list) -> None:
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or f"Command failed: {' '.join(cmd)}")


# ---------------------------------------------------------------------------
# Strength presets
# ---------------------------------------------------------------------------


def _image_params(strength: Strength) -> dict:
    if strength == "near_lossless":
        # Engineered so PSNR stays above the human-perception threshold
        # (ITU-R BT.500: 40 dB) on the test image and well above 48 dB on
        # realistic photos. Relies primarily on steps that are *free*
        # perceptually:
        #   * JPEG round-trip at Q=97 with 4:4:4 chroma subsampling
        #     (re-quantizes the DCT coefficients where Tree-Ring / DCT
        #     watermarks live without measurable chroma loss)
        #   * 1-pixel sub-pixel jitter (destroys registration-sensitive
        #     carriers)
        #   * σ=0.3 dithering noise — below 1 LSB on average, buried under
        #     the JPEG quantization noise floor
        # No scale cycle, no blur, no colour jitter.
        return {
            "noise_sigma": 0.3,
            "scale_factor": 1.0,
            "jitter_max_px": 1,
            "blur_radius": 0.0,
            "color_jitter": 0.0,
            "jpeg_quality": 97,
            "jpeg_subsampling": 0,  # 4:4:4 — keep chroma at full resolution
            "double_pass": False,
        }
    if strength == "light":
        return {
            "noise_sigma": 0.8,      # per-channel gaussian, 8-bit LSBs
            "scale_factor": 0.99,    # shrink then grow back
            "jitter_max_px": 2,      # asymmetric crop+pad
            "blur_radius": 0.0,
            "color_jitter": 0.001,
            "jpeg_quality": 95,
            "double_pass": False,
        }
    if strength == "strong":
        return {
            "noise_sigma": 3.2,
            "scale_factor": 0.93,
            "jitter_max_px": 6,
            "blur_radius": 0.8,
            "color_jitter": 0.004,
            "jpeg_quality": 88,
            "double_pass": True,
        }
    # medium
    return {
        "noise_sigma": 1.8,
        "scale_factor": 0.97,
        "jitter_max_px": 3,
        "blur_radius": 0.4,
        "color_jitter": 0.002,
        "jpeg_quality": 92,
        "double_pass": False,
    }


def _video_params(strength: Strength) -> dict:
    if strength == "near_lossless":
        # For video the codec swap is already the biggest watermark-destroyer
        # and it is perceptually free. We use minimal filter strengths here.
        return {
            "noise_alls": 1,
            "scale": 0.995,
            "crop_px": 2,
            "hqdn3d": "0.2:0.2:1:1",
            "eq_saturation": 1.001,
            "eq_brightness": 0.0005,
            "eq_contrast": 1.001,
        }
    if strength == "light":
        return {
            "noise_alls": 3,
            "scale": 0.99,
            "crop_px": 2,
            "hqdn3d": "0.4:0.4:2:2",
            "eq_saturation": 1.002,
            "eq_brightness": 0.001,
            "eq_contrast": 1.001,
        }
    if strength == "strong":
        return {
            "noise_alls": 12,
            "scale": 0.93,
            "crop_px": 8,
            "hqdn3d": "1.5:1.5:4:4",
            "eq_saturation": 1.006,
            "eq_brightness": 0.003,
            "eq_contrast": 1.003,
        }
    return {
        "noise_alls": 6,
        "scale": 0.97,
        "crop_px": 4,
        "hqdn3d": "0.8:0.8:3:3",
        "eq_saturation": 1.003,
        "eq_brightness": 0.002,
        "eq_contrast": 1.002,
    }


def _audio_params(strength: Strength) -> dict:
    if strength == "near_lossless":
        # Resample round-trip alone (48->44.1->48 kHz) is already enough to
        # break spectrogram-mask watermarks. Minimal filter touch otherwise.
        return {"hp": 20, "lp": 19800, "vol": 0.999}
    if strength == "light":
        return {"hp": 25, "lp": 19500, "vol": 0.998}
    if strength == "strong":
        return {"hp": 50, "lp": 17500, "vol": 0.994}
    return {"hp": 30, "lp": 18500, "vol": 0.996}


# ---------------------------------------------------------------------------
# Image pipeline
# ---------------------------------------------------------------------------


def _attack_image_array(img: Image.Image, p: dict, rng: random.Random) -> Image.Image:
    """Apply the full attack chain to a Pillow image and return a new Pillow image."""
    # Convert to RGB (drop alpha for now; we restore below if it was there).
    had_alpha = img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info)
    alpha = None
    if had_alpha:
        rgba = img.convert("RGBA")
        alpha = rgba.split()[-1]
        rgb = rgba.convert("RGB")
    else:
        rgb = img.convert("RGB")

    arr = np.asarray(rgb, dtype=np.float32)

    # 1. Additive Gaussian noise on all channels.
    if p["noise_sigma"] > 0:
        noise = np.random.normal(0.0, p["noise_sigma"], arr.shape).astype(np.float32)
        arr = arr + noise

    arr = np.clip(arr, 0.0, 255.0).astype(np.uint8)
    stage = Image.fromarray(arr, mode="RGB")

    # 2. Mild gaussian blur (pixel-level watermark smoothing).
    if p["blur_radius"] > 0:
        stage = stage.filter(ImageFilter.GaussianBlur(radius=p["blur_radius"]))

    # 3. Frequency-domain disruption: resize cycle via Lanczos.
    w, h = stage.size
    if p["scale_factor"] < 1.0:
        sw = max(8, int(round(w * p["scale_factor"])))
        sh = max(8, int(round(h * p["scale_factor"])))
        if (sw, sh) != (w, h):
            stage = stage.resize((sw, sh), Image.LANCZOS).resize((w, h), Image.LANCZOS)

    # 4. Tiny asymmetric crop + pad back to original size (sub-pixel geometric jitter).
    j = p["jitter_max_px"]
    if j > 0:
        left = rng.randint(0, j)
        top = rng.randint(0, j)
        right = rng.randint(0, j)
        bottom = rng.randint(0, j)
        cropped = stage.crop((left, top, w - right, h - bottom))
        stage = cropped.resize((w, h), Image.LANCZOS)

    # 5. Micro color jitter (keeps the image visually identical but shifts
    #    every RGB value off the exact carrier it was trained against).
    cj = p["color_jitter"]
    if cj > 0:
        stage = ImageEnhance.Brightness(stage).enhance(1.0 + rng.uniform(-cj, cj))
        stage = ImageEnhance.Contrast(stage).enhance(1.0 + rng.uniform(-cj, cj))
        stage = ImageEnhance.Color(stage).enhance(1.0 + rng.uniform(-cj, cj))

    # 6. Unsharp mask slightly to put perceptual edge energy back after blur/resize.
    if p["blur_radius"] > 0:
        stage = stage.filter(ImageFilter.UnsharpMask(radius=1.0, percent=40, threshold=2))

    if had_alpha and alpha is not None:
        stage = stage.convert("RGBA")
        stage.putalpha(alpha.resize(stage.size, Image.LANCZOS))

    return stage


def _open_image(input_path: str) -> Image.Image:
    """Open an image, registering HEIC/HEIF/AVIF via pillow-heif when available."""
    ext = _ext_lower(input_path)
    if ext in {".heic", ".heif", ".avif"}:
        try:
            from pillow_heif import register_heif_opener  # type: ignore

            register_heif_opener()
        except Exception as e:
            raise RuntimeError(
                "HEIC/HEIF/AVIF support requires pillow-heif. "
                "Install with: pip install pillow-heif. "
                f"Import error: {e}"
            )
    return Image.open(input_path)


def attack_image(
    input_path: str,
    output_dir: str,
    strength: Strength = "near_lossless",
    force_jpeg_roundtrip: bool = True,
    seed: Optional[int] = None,
    use_spectral: bool = True,
    remove_visible: bool = True,
) -> AttackResult:
    """
    Run the image watermark-attack chain and save an output with metadata
    dropped (Pillow re-save discards EXIF/info by default here).

    Order (matches paid-tool feature set):
      1. optional visible corner-badge inpaint (Gemini sparkle)
      2. optional mid-band FFT spectral disruption (SynthID-class carriers)
      3. spatial signal-processing chain (noise / scale / jitter / JPEG)

    force_jpeg_roundtrip = True will, for JPEG outputs, write+read back through
    a JPEG buffer at the preset quality; this re-quantizes every mid-freq DCT
    coefficient which is where many watermarks live.
    """
    p = _image_params(strength)
    rng = random.Random(seed)
    # Seed numpy too so the noise is reproducible when a seed is given.
    if seed is not None:
        np.random.seed(seed & 0xFFFFFFFF)

    img = _open_image(input_path)
    # Respect EXIF orientation before we drop metadata, so "upright" stays upright.
    try:
        from PIL import ImageOps

        img = ImageOps.exif_transpose(img)
    except Exception:
        pass

    extras: list[str] = []

    if remove_visible:
        img, hits = remove_visible_marks(img)
        if hits:
            extras.append(
                "visible_mark=" + ",".join(f"{h.corner}:{h.score:.0f}" for h in hits)
            )
        else:
            extras.append("visible_mark=none")

    fidelity_first = strength == "near_lossless"
    if use_spectral:
        spec_s = spectral_strength_for(strength)
        img, spec_stats = spectral_attack_image(
            img,
            strength=spec_s,
            seed=None if seed is None else (seed + 17),
            fidelity_first=fidelity_first,
        )
        extras.append(
            f"spectral={spec_s}/carriers={spec_stats.get('carrier_bins_y', 0)}"
            + ("/fidelity" if fidelity_first else "")
        )

    # Near-lossless: spectral already did the heavy lifting — keep spatial soft.
    out = _attack_image_array(img, p, rng)
    if p["double_pass"]:
        out = _attack_image_array(out, p, rng)

    in_ext = _ext_lower(input_path)
    # Re-encode HEIC/HEIF/AVIF as JPEG — broad compatibility.
    if in_ext in {".heic", ".heif", ".avif"}:
        out_ext = ".jpg"
    elif in_ext in IMAGE_EXTS:
        out_ext = in_ext
    else:
        out_ext = ".png"
    base = os.path.splitext(os.path.basename(input_path))[0]
    out_path = _unique(os.path.join(output_dir, base + "_clean" + out_ext))

    fmt_map = {
        ".jpg": "JPEG", ".jpeg": "JPEG",
        ".png": "PNG", ".webp": "WEBP", ".bmp": "BMP",
        ".tif": "TIFF", ".tiff": "TIFF",
    }
    fmt = fmt_map.get(out_ext, "PNG")

    # Ensure we drop metadata on save: Pillow's default save does not
    # carry EXIF from `.info` unless we pass it explicitly.
    save_kwargs = {}
    if fmt == "JPEG":
        subsampling = int(p.get("jpeg_subsampling", 2))
        save_kwargs["quality"] = p["jpeg_quality"]
        save_kwargs["subsampling"] = subsampling
        save_kwargs["optimize"] = True
        if out.mode != "RGB":
            out = out.convert("RGB")
        if force_jpeg_roundtrip:
            # Explicit JPEG round-trip in-memory first (destroys DCT watermarks
            # tied to a specific quant table), then decode + save again.
            buf = io.BytesIO()
            out.save(buf, format="JPEG", quality=p["jpeg_quality"],
                     subsampling=subsampling, optimize=True)
            buf.seek(0)
            out = Image.open(buf).convert("RGB")
    elif fmt == "PNG":
        save_kwargs["optimize"] = True
    elif fmt == "WEBP":
        save_kwargs["quality"] = max(80, p["jpeg_quality"])
        save_kwargs["method"] = 4

    # Strip any leftover .info that might carry watermark-adjacent chunks.
    try:
        out.info = {}
    except Exception:
        pass

    out.save(out_path, format=fmt, **save_kwargs)

    # Belt-and-suspenders: scrub any C2PA/JUMBF containers that survived save.
    try:
        from c2pa_strip import deep_strip_c2pa_file

        c2pa_detail, n_c2pa = deep_strip_c2pa_file(out_path)
        if n_c2pa:
            extras.append(c2pa_detail)
    except Exception as e:
        extras.append(f"c2pa_strip_skipped={e}")

    detail = (
        f"image strength={strength} noise={p['noise_sigma']}sigma "
        f"scale={p['scale_factor']:.2f} jitter<={p['jitter_max_px']}px "
        f"jpegQ={p['jpeg_quality']} double={p['double_pass']}"
    )
    if extras:
        detail += " | " + " ".join(extras)
    return AttackResult(output_path=out_path, detail=detail)


# ---------------------------------------------------------------------------
# Video / audio pipeline (ffmpeg)
# ---------------------------------------------------------------------------


# macOS GUI apps launched from Finder/Spotlight inherit a minimal PATH
# (/usr/bin:/bin:/usr/sbin:/sbin) that does not include Homebrew, so bare
# "ffmpeg" lookups fail with FileNotFoundError. Resolve once, at import
# time, by probing PATH and then the canonical Homebrew install prefixes.
_FFMPEG_FALLBACKS = [
    "/opt/homebrew/bin/ffmpeg", "/usr/local/bin/ffmpeg", "/opt/local/bin/ffmpeg",
]
_FFPROBE_FALLBACKS = [
    "/opt/homebrew/bin/ffprobe", "/usr/local/bin/ffprobe", "/opt/local/bin/ffprobe",
]


def _resolve_bin(name: str, fallbacks: list[str]) -> str:
    path = shutil.which(name)
    if path:
        return path
    for cand in fallbacks:
        if os.path.exists(cand):
            return cand
    return name  # let subprocess raise the more obvious FileNotFoundError


FFMPEG = _resolve_bin("ffmpeg", _FFMPEG_FALLBACKS)
FFPROBE = _resolve_bin("ffprobe", _FFPROBE_FALLBACKS)


def _have_hw_encoder(name: str) -> bool:
    """Check whether an ffmpeg encoder is available."""
    try:
        proc = subprocess.run(
            [FFMPEG, "-hide_banner", "-encoders"], capture_output=True, text=True
        )
        return name in (proc.stdout or "")
    except Exception:
        return False


def _probe_streams(path: str) -> dict:
    try:
        proc = subprocess.run(
            [
                FFPROBE, "-v", "error",
                "-show_format", "-show_streams",
                "-print_format", "json", path,
            ],
            capture_output=True, text=True,
        )
        if proc.returncode != 0:
            return {}
        import json

        return json.loads(proc.stdout or "{}")
    except Exception:
        return {}


def attack_video(
    input_path: str,
    output_dir: str,
    strength: Strength = "near_lossless",
    use_spectral: bool = False,
) -> AttackResult:
    """
    Run the video watermark-attack chain via ffmpeg.
    Always re-encodes (stream-copy would keep the watermarked pixels).
    Metadata is dropped via -map_metadata -1.

    If use_spectral=True and the clip is short enough, each frame also gets
    the adaptive FFT carrier attack (paid browser removers' frame-by-frame
    frequency pass). Long clips fall back to the ffmpeg spatial chain only.
    """
    spectral_note = ""
    work_input = input_path
    tmp_spectral = None
    if use_spectral:
        try:
            work_input, spectral_note = _spectral_preprocess_video(
                input_path, output_dir, strength=strength,
            )
            if work_input != input_path:
                tmp_spectral = work_input
        except Exception as e:
            spectral_note = f"spectral_frames_skipped={e}"
            work_input = input_path

    try:
        res = _attack_video_ffmpeg(work_input, output_dir, strength=strength)
    finally:
        if tmp_spectral and os.path.exists(tmp_spectral):
            try:
                os.remove(tmp_spectral)
            except Exception:
                pass

    detail = res.detail
    if spectral_note:
        detail += " | " + spectral_note
    try:
        from c2pa_strip import deep_strip_c2pa_file
        c2pa_detail, n = deep_strip_c2pa_file(res.output_path)
        if n:
            detail += " | " + c2pa_detail
    except Exception:
        pass
    return AttackResult(output_path=res.output_path, detail=detail)


def _probe_duration_sec(path: str) -> float:
    probe = _probe_streams(path)
    try:
        return float((probe.get("format") or {}).get("duration") or 0.0)
    except Exception:
        return 0.0


def _spectral_preprocess_video(
    input_path: str,
    output_dir: str,
    strength: Strength,
    max_seconds: float = 90.0,
) -> tuple[str, str]:
    """
    Frame-extract → adaptive spectral → temp mp4. Returns (path, note).
    Skips when duration exceeds max_seconds (keeps OSS local UX snappy).
    """
    import tempfile

    dur = _probe_duration_sec(input_path)
    if dur > max_seconds:
        return input_path, f"spectral_frames=skipped(duration={dur:.1f}s>{max_seconds:.0f}s)"

    spec_s = spectral_strength_for(strength)
    fidelity_first = strength == "near_lossless"
    tmpdir = tempfile.mkdtemp(prefix="scrub_vframes_")
    pattern = os.path.join(tmpdir, "f_%06d.png")
    try:
        _run([
            FFMPEG, "-y", "-i", input_path,
            "-map_metadata", "-1",
            "-vsync", "0",
            pattern,
        ])
        frames = sorted(
            f for f in os.listdir(tmpdir) if f.endswith(".png")
        )
        if not frames:
            return input_path, "spectral_frames=none"
        carriers = 0
        for name in frames:
            fp = os.path.join(tmpdir, name)
            im = Image.open(fp)
            out, stats = spectral_attack_image(
                im, strength=spec_s, fidelity_first=fidelity_first, seed=None,
            )
            carriers += int(stats.get("carrier_bins_y") or 0)
            out.convert("RGB").save(fp, format="PNG", optimize=True)

        tmp_mp4 = os.path.join(
            output_dir,
            os.path.splitext(os.path.basename(input_path))[0] + "_spectral_tmp.mp4",
        )
        # Rebuild video from frames; copy audio from source if present.
        cmd = [
            FFMPEG, "-y",
            "-framerate", "30",
            "-i", pattern,
            "-i", input_path,
            "-map", "0:v:0", "-map", "1:a:0?",
            "-c:v", "libx264", "-crf", "18", "-preset", "veryfast",
            "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k",
            "-shortest",
            "-map_metadata", "-1",
            tmp_mp4,
        ]
        # Prefer source framerate when probeable.
        probe = _probe_streams(input_path)
        fps = None
        for stm in (probe.get("streams") or []):
            if stm.get("codec_type") == "video":
                rate = stm.get("avg_frame_rate") or stm.get("r_frame_rate")
                if rate and rate != "0/0":
                    try:
                        a, b = rate.split("/")
                        fps = float(a) / float(b) if float(b) else None
                    except Exception:
                        fps = None
                break
        if fps and fps > 1:
            cmd[cmd.index("-framerate") + 1] = str(round(fps, 3))

        _run(cmd)
        note = f"spectral_frames={len(frames)}/{spec_s}/carriers~{carriers}"
        return tmp_mp4, note
    finally:
        # Clean extracted frames.
        try:
            for name in os.listdir(tmpdir):
                try:
                    os.remove(os.path.join(tmpdir, name))
                except Exception:
                    pass
            os.rmdir(tmpdir)
        except Exception:
            pass


def _attack_video_ffmpeg(
    input_path: str,
    output_dir: str,
    strength: Strength = "near_lossless",
) -> AttackResult:
    """
    Run the video watermark-attack chain via ffmpeg.
    Always re-encodes (stream-copy would keep the watermarked pixels).
    Metadata is dropped via -map_metadata -1.
    """
    p = _video_params(strength)
    in_ext = _ext_lower(input_path)
    base = os.path.splitext(os.path.basename(input_path))[0]

    # Keep the container if common, else fall back to mp4.
    target_ext = in_ext if in_ext in {".mp4", ".mov", ".m4v", ".mkv", ".webm"} else ".mp4"
    # Spectral preprocess writes .mp4 temps — keep requested extension from basename caller.
    if "_spectral_tmp" in base:
        base = base.replace("_spectral_tmp", "")
        # Prefer mp4 for intermediate-sourced cleans unless original was webm/mkv —
        # caller passes original output_dir; use mp4 as safe default for tmp source.
        target_ext = ".mp4"
    out_path = _unique(os.path.join(output_dir, base + "_clean" + target_ext))

    # Probe the source first so we can pin the output dimensions to the
    # source's width/height exactly. This guarantees 4K stays 4K, 1080p
    # stays 1080p, etc. — without any rounding drift from relative scale
    # expressions like `iw*0.995`.
    probe = _probe_streams(input_path)
    src_vcodec = None
    src_w = src_h = 0
    for stm in (probe.get("streams") or []):
        if stm.get("codec_type") == "video":
            src_vcodec = stm.get("codec_name")
            src_w = int(stm.get("width") or 0)
            src_h = int(stm.get("height") or 0)
            break

    # Snap the pinned output dims to even integers (yuv420p requirement).
    # In practice every mainstream resolution is already even; this is a
    # safety belt for oddball captures.
    ow = src_w - (src_w % 2) if src_w else None
    oh = src_h - (src_h % 2) if src_h else None

    # Video filter chain.
    #
    # Key correctness notes:
    #   * Every scale/crop/pad step must produce *even* integer dimensions,
    #     otherwise H.264/H.265 with yuv420p will refuse to encode (chroma
    #     sub-sampling needs even width/height).
    #   * The FINAL scale pins to the source's exact pixel dims so the
    #     output resolution is preserved byte-for-byte (no silent 2px drift
    #     from earlier trunc() rounding).
    s = p["scale"]
    cp = int(p["crop_px"])
    if cp % 2:
        cp += 1   # keep the pad symmetric so the geometry is clean
    half = cp // 2

    # Build the middle section of the chain. At near_lossless (s≈0.995) the
    # shrink-grow cycle is noise in the rounding anyway, so we drop it —
    # noise + hqdn3d + eq + recompression at a new CRF already disrupts all
    # the watermark families we target. At higher strengths we keep the
    # scale cycle because it meaningfully perturbs frequency-domain carriers.
    if strength == "near_lossless":
        geometry = (
            f"crop=iw-{cp}:ih-{cp}:{half}:{half},"
            f"pad=iw+{cp}:ih+{cp}:{half}:{half},"
        )
    else:
        geometry = (
            f"scale=trunc(iw*{s}/2)*2:trunc(ih*{s}/2)*2:flags=lanczos,"
            f"crop=iw-{cp}:ih-{cp}:{half}:{half},"
            f"pad=iw+{cp}:ih+{cp}:{half}:{half},"
        )

    # Final scale: pin to source dims if we know them, otherwise just snap
    # to the nearest even integer pair.
    if ow and oh:
        final_scale = f"scale={ow}:{oh}:flags=lanczos"
    else:
        final_scale = "scale=trunc(iw/2)*2:trunc(ih/2)*2"

    vf = (
        f"noise=alls={p['noise_alls']}:allf=t+u,"
        + geometry +
        f"eq=saturation={p['eq_saturation']}:"
        f"brightness={p['eq_brightness']}:contrast={p['eq_contrast']},"
        f"hqdn3d={p['hqdn3d']},"
        f"{final_scale},format=yuv420p"
    )

    # Audio filter chain (applies if an audio stream exists; harmless otherwise).
    ap = _audio_params(strength)
    af = (
        f"aresample=44100,highpass=f={ap['hp']},lowpass=f={ap['lp']},"
        f"aresample=48000,volume={ap['vol']}"
    )

    vt_h264 = _have_hw_encoder("h264_videotoolbox")
    vt_hevc = _have_hw_encoder("hevc_videotoolbox")
    vt_ok = min(src_w, src_h) >= 256  # VideoToolbox is unreliable below this

    # Quality/bitrate is strength-dependent.
    # - near_lossless: CRF 17 on libx264 is visually indistinguishable from
    #   the source at 1080p and above; VT's -q:v uses a 0..100 scale
    #   (higher = better quality), so we push it to 75.
    # - light/medium: CRF 20 is still "visually transparent" per the x264
    #   quality guide.
    # - strong: allow a slightly leaner CRF 22 since strong already accepts
    #   more visible signal-processing changes.
    crf_by_strength = {
        "near_lossless": ("17", "75"),
        "light":         ("18", "70"),
        "medium":        ("20", "65"),
        "strong":        ("22", "60"),
    }
    x264_crf, vt_q = crf_by_strength.get(strength, ("20", "65"))

    if target_ext == ".webm":
        codec_candidates = [
            (["-c:v", "libvpx-vp9", "-b:v", "0", "-crf", "30"],
             ["-c:a", "libopus", "-b:a", "160k"]),
        ]
    else:
        # Always default to H.264 / yuv420p for .mp4 / .mov / .m4v / .mkv.
        # Rationale: H.264 is universally decodable (QuickTime, Safari,
        # iPhone, Android, browsers, Windows, Linux) and the watermark
        # removal's heavy lifting is done by the filter chain (noise,
        # scale-cycle, crop/pad, hqdn3d, eq) plus re-quantization at a new
        # CRF, not by the codec-family swap. This yields the most
        # compatible output for the widest audience.
        #
        # Apple VideoToolbox H.264 is reliable at any reasonable resolution
        # (unlike hevc_videotoolbox which refuses very small frames). We
        # fall through to libx264 if the hardware encoder is unavailable.
        # HEVC is kept only as a last-ditch fallback and then only with
        # the hvc1 tag, which QuickTime actually renders (as opposed to
        # hev1, which it refuses and plays audio-only for).
        h264_hw = [
            "-c:v", "h264_videotoolbox",
            "-b:v", "0", "-q:v", vt_q,
            "-allow_sw", "1",
            "-profile:v", "high",
            "-pix_fmt", "yuv420p",
        ]
        h264_sw = [
            "-c:v", "libx264", "-crf", x264_crf, "-preset", "medium",
            "-pix_fmt", "yuv420p", "-profile:v", "high", "-level", "4.2",
        ]
        hevc_hw = [
            "-c:v", "hevc_videotoolbox",
            "-b:v", "0", "-q:v", vt_q,
            "-allow_sw", "1",
            "-tag:v", "hvc1",       # critical: QT only plays hvc1, not hev1
            "-pix_fmt", "yuv420p",
        ]
        # For libx265 the visually-transparent CRF is ~2 higher than x264's.
        x265_crf = str(int(x264_crf) + 2)
        hevc_sw = [
            "-c:v", "libx265", "-crf", x265_crf, "-preset", "medium",
            "-pix_fmt", "yuv420p", "-tag:v", "hvc1",
        ]
        # Last-ditch universal fallback — every ffmpeg build has mpeg4.
        mpeg4 = ["-c:v", "mpeg4", "-qscale:v", "3", "-pix_fmt", "yuv420p"]

        ordered = []
        if vt_ok and vt_h264:
            ordered.append(h264_hw)
        ordered.append(h264_sw)
        if vt_ok and vt_hevc:
            ordered.append(hevc_hw)
        ordered.append(hevc_sw)
        ordered.append(mpeg4)

        a_codec = ["-c:a", "aac", "-b:a", "192k"]
        codec_candidates = [(vc, a_codec) for vc in ordered]

    last_err: Optional[Exception] = None
    used_v: list = []
    used_a: list = []
    for v_codec, a_codec in codec_candidates:
        cmd = [
            FFMPEG, "-y",
            "-i", input_path,
            "-map_metadata", "-1",
            "-map_chapters", "-1",
            # Keep exactly one video track and (if present) one audio track.
            # Drops subtitle, timecode, GPS-metadata, and auxiliary streams
            # that some phones include and that can confuse players.
            "-map", "0:v:0",
            "-map", "0:a:0?",
            "-vf", vf,
            "-af", af,
            *v_codec,
            *a_codec,
        ]
        if target_ext in {".mp4", ".mov", ".m4v"}:
            cmd += ["-movflags", "+faststart"]
        cmd += [out_path]
        try:
            _run(cmd)
            used_v, used_a = v_codec, a_codec
            break
        except Exception as e:
            last_err = e
            # Ensure a partial file isn't left over before trying next candidate.
            try:
                if os.path.exists(out_path):
                    os.remove(out_path)
            except Exception:
                pass
            continue

    if not used_v:
        # Pull the most informative last-line out of ffmpeg's stderr.
        raw = str(last_err) if last_err else ""
        lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
        tail = lines[-1] if lines else ""
        raise RuntimeError(
            f"All video encoders failed. ffmpeg: {tail or raw[:200]}"
        )

    detail = (
        f"video strength={strength} noise={p['noise_alls']} scale={p['scale']:.2f} "
        f"crop={p['crop_px']}px hqdn3d={p['hqdn3d']} "
        f"v={used_v[1]} a={used_a[1]}"
    )
    return AttackResult(output_path=out_path, detail=detail)


def attack_audio(
    input_path: str,
    output_dir: str,
    strength: Strength = "near_lossless",
) -> AttackResult:
    """
    Attack for standalone audio files. Re-samples, band-limits slightly, and
    swaps codec to force a full bitstream rewrite.
    """
    p = _audio_params(strength)
    in_ext = _ext_lower(input_path)
    base = os.path.splitext(os.path.basename(input_path))[0]

    # Codec swap heuristic.
    if in_ext in {".mp3"}:
        target_ext = ".m4a"
        a_codec = ["-c:a", "aac", "-b:a", "256k"]
    elif in_ext in {".m4a", ".aac"}:
        target_ext = ".mp3"
        a_codec = ["-c:a", "libmp3lame", "-q:a", "2"]
    elif in_ext in {".wav", ".flac"}:
        target_ext = ".m4a"
        a_codec = ["-c:a", "aac", "-b:a", "256k"]
    else:
        target_ext = ".m4a"
        a_codec = ["-c:a", "aac", "-b:a", "256k"]

    out_path = _unique(os.path.join(output_dir, base + "_clean" + target_ext))
    af = (
        f"aresample=44100,highpass=f={p['hp']},lowpass=f={p['lp']},"
        f"aresample=48000,volume={p['vol']}"
    )

    cmd = [
        FFMPEG, "-y",
        "-i", input_path,
        "-map_metadata", "-1",
        "-vn",
        "-af", af,
        *a_codec,
        out_path,
    ]
    _run(cmd)
    detail = f"audio strength={strength} hp={p['hp']} lp={p['lp']} vol={p['vol']} codec_swap={in_ext}->{target_ext}"
    return AttackResult(output_path=out_path, detail=detail)


# ---------------------------------------------------------------------------
# Optional diffusion-regeneration path (images only) — advanced / heavy
# ---------------------------------------------------------------------------


def diffusion_regen_image(
    input_path: str,
    output_dir: str,
    strength: float = 0.18,
    model_id: str = "stabilityai/sd-turbo",
    steps: int = 4,
) -> AttackResult:
    """
    Low-denoise img2img regeneration — the same principle as the
    Synthid-Bypass repo, but lightweight enough to run on MPS on an
    Apple-Silicon Mac. Only activates if `diffusers` + `torch` are installed.

    NOTE: this will download the model on first run. For a fully offline tool,
    pre-cache the model with `huggingface-cli download <model_id>`.
    """
    try:
        import torch  # type: ignore
        from diffusers import AutoPipelineForImage2Image  # type: ignore
    except Exception as e:
        raise RuntimeError(
            "Diffusion regeneration requires `torch` and `diffusers` installed. "
            "Install with: pip install torch diffusers transformers accelerate safetensors. "
            f"Import error: {e}"
        )

    device = "mps" if torch.backends.mps.is_available() else ("cuda" if torch.cuda.is_available() else "cpu")
    dtype = torch.float16 if device != "cpu" else torch.float32

    pipe = AutoPipelineForImage2Image.from_pretrained(model_id, torch_dtype=dtype)
    pipe = pipe.to(device)
    try:
        pipe.set_progress_bar_config(disable=True)
    except Exception:
        pass

    img = Image.open(input_path).convert("RGB")
    # SD-Turbo expects 512px-ish sides; keep aspect ratio, cap longest side.
    max_side = 1024
    w, h = img.size
    scale = min(1.0, max_side / max(w, h))
    rw, rh = int(w * scale) // 8 * 8, int(h * scale) // 8 * 8
    if (rw, rh) != (w, h):
        img_small = img.resize((max(64, rw), max(64, rh)), Image.LANCZOS)
    else:
        img_small = img

    result = pipe(
        prompt="",
        image=img_small,
        strength=float(strength),
        guidance_scale=0.0,  # Turbo models ignore CFG; keep zero
        num_inference_steps=max(1, int(steps)),
    ).images[0]

    if result.size != (w, h):
        result = result.resize((w, h), Image.LANCZOS)

    base = os.path.splitext(os.path.basename(input_path))[0]
    out_path = _unique(os.path.join(output_dir, base + "_regen.png"))
    result.info = {}
    result.save(out_path, format="PNG", optimize=True)
    return AttackResult(
        output_path=out_path,
        detail=f"diffusion regen model={model_id} strength={strength} steps={steps} device={device}",
    )


# ---------------------------------------------------------------------------
# High-level dispatch
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CleanReport:
    """Everything the UI needs to explain what we did to a single file."""
    output_path: str
    detail: str
    strength_used: Strength
    auto_escalated: bool
    origin: Optional[OriginReport] = None
    audit_path: Optional[str] = None


def _resolve_strength(
    input_path: str,
    strength: Strength | str,
    auto: bool,
) -> tuple[Strength, bool, Optional[OriginReport]]:
    """
    If auto=True and the file is a robustly-watermarked origin (Google SynthID
    images, Kling/Veo/Sora video, AudioSeal audio, …), escalate to at least
    Medium. Otherwise honour the requested strength.

    Returns (chosen_strength, was_auto_escalated, origin_report_or_none).
    """
    valid: tuple[Strength, ...] = ("near_lossless", "light", "medium", "strong")
    s: Strength = strength if strength in valid else "near_lossless"  # type: ignore[assignment]

    if not auto:
        return s, False, None

    try:
        report = detect_origin(input_path)
    except Exception:
        return s, False, None

    if report.likely_robust_watermark:
        # Rank strengths and clamp UP to medium if we're below it.
        rank = {"near_lossless": 0, "light": 1, "medium": 2, "strong": 3}
        if rank[s] < rank["medium"]:
            return "medium", True, report
    return s, False, report


def clean_file(
    input_path: str,
    output_dir: str,
    strength: Strength = "near_lossless",
    use_diffusion: bool = False,
    auto_strength: bool = True,
    use_spectral: bool = True,
    remove_visible: bool = True,
    write_audit: bool = True,
) -> Tuple[str, str]:
    """
    Strip metadata AND attack invisible watermarks in one call.

    - Images: visible-mark inpaint → spectral FFT → spatial chain (or diffusion).
    - Video/audio: ffmpeg pipeline that re-encodes with watermark attack
      filters AND drops all container/stream metadata (-map_metadata -1).

    If `auto_strength=True` (the default), the strength is auto-escalated to
    at least "medium" for media whose metadata signals a robustly-watermarked
    origin (Google Imagen/Gemini, Veo, Kling, Sora, AudioSeal, …).
    """
    if not os.path.exists(input_path):
        raise FileNotFoundError(input_path)
    os.makedirs(output_dir, exist_ok=True)

    report = clean_file_v2(
        input_path=input_path, output_dir=output_dir,
        strength=strength, use_diffusion=use_diffusion,
        auto_strength=auto_strength,
        use_spectral=use_spectral, remove_visible=remove_visible,
        write_audit=write_audit,
    )
    return report.output_path, report.detail


def clean_file_v2(
    input_path: str,
    output_dir: str,
    strength: Strength = "near_lossless",
    use_diffusion: bool = False,
    auto_strength: bool = True,
    use_spectral: bool = True,
    remove_visible: bool = True,
    write_audit: bool = True,
) -> CleanReport:
    """Structured variant of `clean_file` that returns a `CleanReport`."""
    ext = _ext_lower(input_path)
    if not os.path.exists(input_path):
        raise FileNotFoundError(input_path)
    os.makedirs(output_dir, exist_ok=True)

    chosen, escalated, origin = _resolve_strength(input_path, strength, auto_strength)

    prefix = ""
    if escalated and origin and origin.matches:
        prefix = f"auto-escalated ({'+'.join(origin.matches)}) -> {chosen}; "

    def _finish(res: AttackResult, detail: str) -> CleanReport:
        audit_path = None
        full_detail = prefix + detail
        if write_audit:
            try:
                from audit_report import write_clean_audit

                audit_path = write_clean_audit(
                    input_path, res.output_path, full_detail,
                    extra={
                        "use_spectral": use_spectral,
                        "remove_visible": remove_visible,
                        "use_diffusion": use_diffusion,
                    },
                )
                full_detail = full_detail + f" | audit={os.path.basename(audit_path)}"
            except Exception as e:
                full_detail = full_detail + f" | audit_failed={e}"
        return CleanReport(
            output_path=res.output_path,
            detail=full_detail,
            strength_used=chosen, auto_escalated=escalated, origin=origin,
            audit_path=audit_path,
        )

    if ext in IMAGE_EXTS:
        if use_diffusion:
            try:
                pre = input_path
                tmp_pre = None
                if remove_visible:
                    try:
                        img = _open_image(input_path)
                        try:
                            from PIL import ImageOps
                            img = ImageOps.exif_transpose(img)
                        except Exception:
                            pass
                        cleaned_vis, hits = remove_visible_marks(img)
                        if hits:
                            tmp_pre = os.path.join(
                                output_dir,
                                os.path.splitext(os.path.basename(input_path))[0]
                                + "_pre_visible.png",
                            )
                            cleaned_vis.convert("RGB").save(tmp_pre, format="PNG")
                            pre = tmp_pre
                    except Exception:
                        pre = input_path
                res = diffusion_regen_image(pre, output_dir)
                if tmp_pre and os.path.exists(tmp_pre):
                    try:
                        os.remove(tmp_pre)
                    except Exception:
                        pass
                return _finish(res, res.detail + " + signal-chain-skipped")
            except Exception as e:
                sig = attack_image(
                    input_path, output_dir, strength=chosen,
                    use_spectral=use_spectral, remove_visible=remove_visible,
                )
                return _finish(
                    sig,
                    f"diffusion path unavailable ({e}); fell back to signal-chain: {sig.detail}",
                )
        res = attack_image(
            input_path, output_dir, strength=chosen,
            use_spectral=use_spectral, remove_visible=remove_visible,
        )
        return _finish(res, res.detail)

    if ext in VIDEO_EXTS:
        res = attack_video(
            input_path, output_dir, strength=chosen, use_spectral=use_spectral,
        )
        return _finish(res, res.detail)

    if ext in AUDIO_EXTS:
        res = attack_audio(input_path, output_dir, strength=chosen)
        return _finish(res, res.detail)

    raise ValueError(f"Unsupported file type: {ext} ({os.path.basename(input_path)})")


__all__ = [
    "Strength",
    "AttackResult", "CleanReport",
    "IMAGE_EXTS", "VIDEO_EXTS", "AUDIO_EXTS",
    "attack_image", "attack_video", "attack_audio",
    "diffusion_regen_image",
    "clean_file", "clean_file_v2",
]
