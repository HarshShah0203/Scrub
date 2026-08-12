# Scrub

**Free, local, offline** tool that strips removable metadata and degrades invisible AI watermarks from images, video, and audio.

Nothing leaves your Mac. No cloud. No credits. No $2/image.

People currently pay cloud services (e.g. [deletesynthid.com](https://deletesynthid.com/), [removesynthid.io](https://removesynthid.io/)) for single-purpose SynthID cleanup. Scrub does that locally — and also handles video, audio, C2PA/EXIF/XMP, and several other watermark families.

## What it does

1. **Strips removable metadata** — EXIF, IPTC, XMP, JPEG `info`, MP4/MOV container tags, C2PA manifests, etc.
2. **Attacks invisible watermarks** with a signal-processing chain whose steps are each documented in the watermark-attack literature.

| Family | Examples | Attack used here | Expected effect |
|---|---|---|---|
| Pixel-noise carriers | **Google SynthID** (Imagen, Gemini / Nano Banana) | Additive Gaussian noise + mild smoothing + resize cycle + JPEG round-trip | Detector confidence typically drops below threshold |
| Latent / frequency-domain | Stable-Signature, Tree-Ring, DCT watermarks | Scale-cycle via Lanczos + sub-pixel crop/pad | Disrupts spectral coefficients the detector keys on |
| JPEG/DCT quant-table | Older IPTC-style DCT watermarks | JPEG round-trip at a new quality + 4:2:0 | Re-quantizes mid-frequency coefficients |
| Audio spectral mask | **SynthID-Audio**, **Meta AudioSeal** | Resample 48→44.1→48 + mild filters + codec swap | Crushes masked bins; forces a clean bitstream |
| Per-frame video | **Kling**, **Veo**, **Sora** | ffmpeg noise + scale-cycle + crop/pad + re-encode | Destroys per-frame carriers and bitstream signatures |

## Honest limitations

- Detectors are probabilistic. “Effective” means confidence pushed below the threshold on current public detectors — not a cryptographic guarantee. Future detectors may be harder.
- Pixel changes are tiny (e.g. JPEG q≈92 + Gaussian σ≈2) but measurable with LPIPS.
- Optional **diffusion regeneration** (Stable-Diffusion-Turbo img2img on Apple Silicon MPS) is the strongest known local attack against SynthID. Off by default; ~2–3 GB weights on first use.
- Use only on content you own or have the right to modify. Do not use this to misrepresent origin or commit fraud.

## Install + run (macOS)

### Prerequisites

```bash
brew install ffmpeg python@3.12 python-tk@3.12
```

### Launch

Double-click `run.command` in Finder. First launch builds a `.venv`, installs deps, and opens the native app.

Or manually:

```bash
/opt/homebrew/bin/python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python tk_app.py          # native UI
# or
.venv/bin/python app.py             # browser UI → http://127.0.0.1:7860
```

### Optional: install as a Mac app

```bash
.venv/bin/python scripts/build_app.py --install
```

Then open via Spotlight: **Scrub**.

## Using the app

1. **Add files…** — images, video, or audio (batch OK).
2. Pick an **output folder**.
3. **Mode**: *Metadata + invisible watermarks* (recommended) or *Metadata only*.
4. **Strength**: Light / Medium / Strong (or leave auto on).
5. Optional: **diffusion regeneration** for images (needs `torch` + `diffusers`).
6. **Start** — cleaned copies land with a `_clean` suffix. Originals are never modified.

## Programmatic use

```python
from watermark_remover import clean_file

out_path, detail = clean_file(
    input_path="/path/to/image.jpg",
    output_dir="/path/to/out",
    strength="medium",      # "light" | "medium" | "strong"
    use_diffusion=False,
)
```

## Project layout

| File | Role |
|---|---|
| `watermark_remover.py` | Image / video / audio attack pipelines |
| `stripper.py` | Metadata-only pipeline |
| `origin_detect.py` | Heuristic origin / provenance hints |
| `tk_app.py` | Native Mac UI (default) |
| `app.py` | Gradio browser UI |
| `run.command` | Double-click Finder launcher |
| `scripts/build_app.py` | Builds `Scrub.app` |

## Why open source this?

Cloud “remove SynthID” products charge per image and upload your files to someone else’s servers. Scrub is the opposite: free, inspectable, and fully offline. If you were about to buy credits for a one-off cleanup, clone this instead.

## References

1. Hu et al. (2024), *Stable Signature is Unstable*. [arXiv:2405.07145](https://arxiv.org/abs/2405.07145)
2. Google DeepMind, [SynthID](https://deepmind.google/models/synthid/)
3. [00quebec/Synthid-Bypass](https://github.com/00quebec/Synthid-Bypass) (ComfyUI-based diffusion approach)

## License

MIT — see [LICENSE](LICENSE).

## Ethics

Released as defensive / research tooling so people can evaluate how robust “invisible” watermarks actually are. Don’t use it to commit fraud or strip marks from work you don’t own.
