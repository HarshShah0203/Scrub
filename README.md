# Metadata & Invisible-Watermark Cleaner (Mac)

A local, offline Mac app that:

1. **Strips removable metadata** (EXIF, IPTC, XMP, JPEG `info`, MP4/MOV
   container tags, C2PA manifests, etc).
2. **Attacks invisible watermarks** using a signal-processing chain whose
   individual steps are each documented in the watermark-attack literature
   to degrade the main watermark families shipped by AI generators today.

Nothing ever leaves your Mac. No cloud, no telemetry.

## What it actually does to each watermark family

| Family | Examples | Attack used here | Expected effect |
|---|---|---|---|
| Pixel-noise carriers | **Google SynthID** for images (Imagen, Nano Banana Pro) | Additive Gaussian noise + mild smoothing + resize cycle + JPEG round-trip | Very likely detected confidence drops below threshold. Confirmed effective by published research and by `github.com/00quebec/Synthid-Bypass`. |
| Latent / frequency-domain | Stable-Signature, Tree-Ring, DCT watermarks | Scale-cycle via Lanczos + sub-pixel crop/pad | Disrupts the spectral coefficients the detector keys on. |
| JPEG/DCT quant-table | Older IPTC-style DCT watermarks | JPEG round-trip at a new quality + subsampling 4:2:0 | Re-quantizes every mid-frequency coefficient. |
| Audio spectral mask | **SynthID-Audio**, **Meta AudioSeal**, Stable-Signature-Audio | Resample 48→44.1→48 kHz + mild hp/lp + volume trim + codec swap | Crushes the masked bins and forces a clean bitstream. |
| Per-frame video | **Kling**, **Veo**, **Sora** invisible watermarks | ffmpeg `noise` + scale-cycle + crop/pad + `hqdn3d` + full re-encode with a *different* codec (h264 ↔ h265) | Destroys per-frame spatial carriers and wipes any motion-vector / bitstream-level signature. |

## Honest limitations

- Watermark detectors are probabilistic. "Effective" means "confidence pushed
  below the reported threshold on current public detectors". It does **not**
  mean "cryptographically unforgeable". Future detector versions may be
  more robust.
- Signal-processing attacks make *tiny* pixel changes. A JPEG output at
  quality 92 plus Gaussian σ≈2 is indistinguishable from an original to
  the eye, but high-end perceptual quality metrics (LPIPS) will see a
  difference.
- For the strongest known attack against SynthID (the one in the
  referenced repo), the app has an optional **diffusion regeneration**
  mode that runs a small Stable-Diffusion-Turbo img2img on Apple-Silicon
  MPS. It's slower and heavier (≈2-3 GB of model weights, downloaded on
  first use), but it runs locally. Off by default.
- This is AI-safety research tooling. Do not use it to misrepresent the
  origin of content you didn't create.

## Install + run (first time)

### Prerequisites

```bash
brew install ffmpeg python@3.12 python-tk@3.12
```

`ffmpeg` is required. Apple's bundled Python 3.9 ships a stale Tcl/Tk that
aborts on recent macOS, so we use Homebrew's Python 3.12 + its `python-tk`
companion package, which gives a working `tkinter`.

### Launch

Double-click `run.command` in Finder. First launch will:

1. Auto-pick Homebrew Python 3.12 (or 3.11 / 3.13 if present).
2. Build a venv at `.venv` and install deps.
3. Open the native Mac app.

If `tkinter` is missing, it falls back to the Gradio web UI automatically.

Manual alternative:

```bash
/opt/homebrew/bin/python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python tk_app.py          # native UI
# or
.venv/bin/python app.py             # browser UI at http://127.0.0.1:7860
```

## Using the app

1. Click **Add files…** to pick images, videos, or audio. Multiple
   files are supported.
2. Pick an **output folder**.
3. Choose a **mode**:
   - *Metadata + invisible watermarks (recommended)* — both passes.
   - *Metadata only (fast)* — no pixel changes at all.
4. Pick a **strength**:
   - *Light* — tiny pixel changes. Good when you care about fidelity.
   - *Medium* (default) — the sweet spot.
   - *Strong* — aggressive; recommended if you suspect a robust
     watermark and can tolerate a hair more visible change.
5. (Optional) Tick **Advanced: use diffusion regeneration for images**
   if you have `torch` + `diffusers` installed. This matches the core
   technique of `github.com/00quebec/Synthid-Bypass` (low-denoise
   img2img), adapted to a single local MPS pipeline.
6. Click **Clean files**. Cleaned copies land in your output folder
   with a `_clean` suffix.

## Web UI alternative

If you prefer a browser UI:

```bash
.venv/bin/python app.py
```

Opens `http://127.0.0.1:7860/`.

## Programmatic use

```python
from watermark_remover import clean_file

out_path, detail = clean_file(
    input_path="/path/to/image.jpg",
    output_dir="/path/to/out",
    strength="medium",      # "light" | "medium" | "strong"
    use_diffusion=False,    # True requires diffusers + torch on MPS
)
```

Returns `(output_path, detail)`.

## File layout

- `watermark_remover.py` — the attack pipelines (image / video / audio).
- `stripper.py` — the original metadata-only pipeline (kept for the
  "Metadata only (fast)" mode and for audit/compare features).
- `tk_app.py` — native Mac UI (default entry point).
- `app.py` — Gradio-based browser UI (alternative).
- `run.command` — double-click Finder launcher.

## References

1. Hu et al. (2024), *Stable Signature is Unstable: Removing Image
   Watermark from Diffusion Models*. arXiv:2405.07145.
2. Google DeepMind, *SynthID*. https://deepmind.google/models/synthid/
3. 00quebec, *SynthID-Bypass* (ComfyUI-based). https://github.com/00quebec/Synthid-Bypass

## Ethics

Released as defensive/research tooling so that people understand the
actual robustness of "invisible" watermarks. Don't use it to commit
fraud.
