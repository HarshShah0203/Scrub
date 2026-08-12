# Scrub

[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/)
[![Platform](https://img.shields.io/badge/platform-macOS%20%7C%20Linux-lightgrey.svg)](#install--run)
[![Offline](https://img.shields.io/badge/privacy-100%25%20offline-brightgreen.svg)](#why-scrub)
[![GitHub stars](https://img.shields.io/github/stars/HarshShah0203/Scrub?style=social)](https://github.com/HarshShah0203/Scrub/stargazers)
[![GitHub forks](https://img.shields.io/github/forks/HarshShah0203/Scrub?style=social)](https://github.com/HarshShah0203/Scrub/network/members)

### Free. Local. Offline. Remove AI watermarks & metadata — no $2/image cloud tax.

**Scrub** strips EXIF / C2PA / Content Credentials and degrades invisible AI watermarks (**Google SynthID**, Kling, Veo, Sora, AudioSeal, …) on **images, video, and audio**. Nothing leaves your machine.

> People pay [deletesynthid.com](https://deletesynthid.com/) and [removesynthid.io](https://removesynthid.io/) for this. Scrub does it **free, inspectable, and fully offline**.

**If this saves you money or teaches you how “invisible” watermarks actually fail — ⭐ star the repo and fork it.**

---

## Why Scrub

| | Paid removers | **Scrub** |
|---|---|---|
| SynthID-class disruption | Yes | Yes — adaptive FFT + spatial attacks |
| C2PA / EXIF / XMP / IPTC | Yes | Yes — deep C2PA/JUMBF scrub |
| Visible Gemini sparkle | Often | Yes |
| Video + audio | Sometimes | Yes |
| Your files uploaded? | Often yes | **Never** |
| Price | Credits / $ | **MIT / free** |

## Quick start

### macOS

```bash
brew install ffmpeg python@3.12 python-tk@3.12
git clone https://github.com/HarshShah0203/Scrub.git
cd Scrub
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python tk_app.py          # native UI
# or: .venv/bin/python app.py       # browser UI → http://127.0.0.1:7860
```

Optional Mac app: `.venv/bin/python scripts/build_app.py --install` → Spotlight **Scrub**.

### Linux

```bash
sudo apt install ffmpeg python3-venv python3-tk
git clone https://github.com/HarshShah0203/Scrub.git
cd Scrub
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python app.py             # Gradio web UI
# or: .venv/bin/python tk_app.py    # if your desktop supports Tk
```

## What it does

1. **Metadata** — EXIF, IPTC, XMP, container tags, C2PA / JUMBF / Content Credentials  
2. **Visible badges** — Gemini / Nano Banana corner sparkle inpaint  
3. **Invisible watermarks** — adaptive spectral carriers + literature signal-processing chain  
4. **Audit** — `*_audit.json` before/after provenance report  

| Family | Examples | Attack |
|---|---|---|
| Pixel-noise carriers | **Google SynthID** | Mid-band FFT damp + noise / JPEG |
| Frequency / latent | Tree-Ring, Stable-Signature | Scale-cycle + FFT + crop/pad |
| Visible corner badges | Gemini sparkle | Detect + soft inpaint |
| Audio masks | SynthID-Audio, AudioSeal | Resample + filters + codec swap |
| Video per-frame | Kling, Veo, Sora | Spectral frames (≤90s) + ffmpeg |

## Using the app

1. Add images / video / audio (batch OK; HEIC/AVIF supported)  
2. Pick an output folder  
3. Mode: **Metadata + watermarks** (recommended) or metadata-only  
4. Defaults on: **spectral**, **visible sparkle**, **audit JSON**  
5. Optional: diffusion regeneration (`torch` + `diffusers`)  
6. **Start** — originals untouched; outputs get `_clean` (+ audit sidecar)

## Programmatic API

```python
from watermark_remover import clean_file

out_path, detail = clean_file(
    input_path="/path/to/image.jpg",
    output_dir="/path/to/out",
    strength="medium",
    use_spectral=True,
    remove_visible=True,
    write_audit=True,
)
```

## Honest limitations

- Detectors are probabilistic — not a cryptographic guarantee.  
- Spectral carriers are estimated per file (no proprietary codebook download).  
- We do **not** claim byte-identical pixels.  
- Video spectral pass skips clips longer than 90s (ffmpeg chain still runs).  
- Use only on media you own or have the right to modify.

## Project layout

| File | Role |
|---|---|
| `watermark_remover.py` | Image / video / audio pipelines |
| `spectral_attack.py` | Adaptive FFT carrier attack |
| `visible_mark.py` | Gemini sparkle inpaint |
| `c2pa_strip.py` | Deep C2PA / JUMBF scrub |
| `audit_report.py` | Before/after JSON |
| `tk_app.py` / `app.py` | Native + Gradio UIs |

## Research lineage

1. Hu et al. (2024), *Stable Signature is Unstable* — [arXiv:2405.07145](https://arxiv.org/abs/2405.07145)  
2. Regeneration / diffusion watermark-removal literature  
3. [00quebec/Synthid-Bypass](https://github.com/00quebec/Synthid-Bypass)  
4. Community spectral analyses of SynthID carriers  
5. Google DeepMind, [SynthID](https://deepmind.google/models/synthid/)

## Contributing

Forks and PRs welcome — especially:

- Better visible-badge detectors  
- Longer-video spectral performance  
- Windows packaging  
- Before/after demo assets for the README  

See [CONTRIBUTING.md](CONTRIBUTING.md).

## Star & share

If Scrub replaces a paid “remove SynthID” tab for you:

1. **Star** the repo so others find a free offline option  
2. **Fork** if you want to extend or harden it  
3. Tell creators who are about to buy credits  

## License

MIT — see [LICENSE](LICENSE).

## Ethics

Defensive / research tooling so people can evaluate how robust “invisible” watermarks actually are. Don’t use it to commit fraud or strip marks from work you don’t own.
