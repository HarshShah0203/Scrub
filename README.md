# Scrub

[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/)
[![Platform](https://img.shields.io/badge/platform-macOS%20%7C%20Linux-lightgrey.svg)](#quick-start)

Local, offline toolkit for **metadata hygiene** and **watermark-robustness experiments** on media you own.

Scrub can:

- Strip common metadata (EXIF / IPTC / XMP) and attempt best-effort cleanup of C2PA / Content Credentials–style containers
- Heuristically inpaint small corner “AI badge” marks when they look like UI sparkles
- Apply public-research-style spatial / spectral transforms that may **degrade** invisible watermark-like signals

Nothing is uploaded. Originals are left untouched; outputs are written beside them.

> Use only on media you have rights to modify. See [NOTICE.md](NOTICE.md) for scope, trademarks, and limitations.

<p align="center">
  <img src="docs/before-after.jpg" alt="Illustrative before/after processing example" width="720" />
</p>

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

| Step | Behavior |
|---|---|
| Metadata | Clears EXIF / IPTC / XMP / many container tags; best-effort C2PA/JUMBF scrub |
| Visible badges | Optional heuristic inpaint of compact high-contrast corner marks |
| Invisible signals | Optional spectral + spatial transforms from public attack literature |
| Audit | Optional `*_audit.json` noting what was attempted |

Transforms are **best-effort**. Names of commercial watermark systems below are used only as shorthand for *publicly discussed signal families* — see the disclaimer.

| Signal family (public discussion) | Typical transforms here |
|---|---|
| Mid-band pixel carriers | FFT dampening, noise, mild JPEG |
| Frequency / latent-style marks | Resize cycle, crop/pad, FFT |
| Small corner UI badges | Detect + soft inpaint |
| Audio spectral masks | Resample, filters, codec swap |
| Short video | Per-frame spectral pass (≤90s) + ffmpeg re-encode |

## Using the app

1. Add images / video / audio you are allowed to modify (batch OK; HEIC/AVIF supported)
2. Pick an output folder
3. Choose **Metadata + signal disruption** or **Metadata only**
4. Defaults favor spectral disruption, badge inpaint, and an audit JSON
5. Optional: diffusion regeneration if you install `torch` + `diffusers`
6. Start — originals stay put; outputs use a `_clean` suffix

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

## Limitations

- No cryptographic guarantee; detectors are probabilistic and change over time
- No proprietary codebook or vendor API is used or reverse-engineered here
- Pixel-identical reconstruction is not a goal
- Video spectral pass skips clips longer than 90s (ffmpeg chain still runs)
- **Only process media you own or have explicit rights to modify**

## Layout

| File | Role |
|---|---|
| `watermark_remover.py` | Image / video / audio pipelines |
| `spectral_attack.py` | Adaptive FFT carrier disruption |
| `visible_mark.py` | Corner-badge inpaint heuristic |
| `c2pa_strip.py` | Best-effort C2PA / JUMBF scrub |
| `audit_report.py` | Before/after JSON |
| `tk_app.py` / `app.py` | Native + Gradio UIs |
| `NOTICE.md` | Intended use, trademarks, liability |

## Research lineage

Implementation ideas draw on public research and discussion, including:

1. Hu et al. (2024), *Stable Signature is Unstable* — [arXiv:2405.07145](https://arxiv.org/abs/2405.07145)
2. Broader regeneration / signal-processing watermark-robustness literature
3. Community analyses of mid-band carriers (including projects such as [Synthid-Bypass](https://github.com/00quebec/Synthid-Bypass))
4. Vendor documentation of watermarking concepts, e.g. [DeepMind SynthID](https://deepmind.google/models/synthid/) (reference only; no affiliation)

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Keep claims honest; do not add uploaders, telemetry, or “guaranteed bypass” language.

## License

MIT — see [LICENSE](LICENSE). Also read [NOTICE.md](NOTICE.md).
