# Scrub

**Free, local, offline** tool that strips removable metadata and degrades invisible AI watermarks from images, video, and audio.

Nothing leaves your machine. No cloud. No credits. No $2/image.

People currently pay services (e.g. [deletesynthid.com](https://deletesynthid.com/), [removesynthid.io](https://removesynthid.io/)) for SynthID + C2PA cleanup. Scrub covers that job **fully offline** — images, video, audio, visible Gemini sparkles, adaptive spectral attack, and before/after audit. Cloud SaaS is intentionally out of scope for an open-source local tool.

## Feature parity (local vs paid)

| Capability | Paid tools | Scrub (local) |
|---|---|---|
| Invisible SynthID-class disruption | Yes | Yes — adaptive FFT carrier detection + spatial chain |
| C2PA / JUMBF / EXIF / XMP strip | Yes | Yes — Pillow re-save **+ deep C2PA segment scrub** |
| Visible Gemini / Nano Banana sparkle | deletesynthid | Yes — corner badge detect + inpaint |
| Scan → clean → verify | Marketing UX | Yes — `*_audit.json` sidecar |
| Images (JPEG/PNG/WebP/HEIC/AVIF) | Yes | Yes |
| Video frame frequency pass | removesynthid | Yes on clips ≤90s (else ffmpeg spatial chain) |
| Audio (SynthID-Audio / AudioSeal) | Rare | Yes |
| Diffusion regeneration | Some cloud pipelines | Optional local SD-Turbo img2img |
| Upload to someone else’s servers | Often required | **Never** |
| Price | Credits / $ | Free (MIT) |

## What it does

1. **Strips removable metadata** — EXIF, IPTC, XMP, container tags, C2PA/JUMBF boxes.
2. **Removes visible AI badges** — Gemini / Nano Banana corner sparkle when detected.
3. **Attacks invisible watermarks** — adaptive spectral carrier dampening (open stand-in for proprietary “spectral codebooks”) + spatial literature attacks.
4. **Writes an audit** — origin tags + metadata on input vs output.

| Family | Examples | Attack used here |
|---|---|---|
| Pixel-noise carriers | **Google SynthID** | Adaptive mid-band carrier damp + phase jitter + spatial/JPEG |
| Latent / frequency-domain | Stable-Signature, Tree-Ring | Scale-cycle + FFT carriers + crop/pad |
| Visible corner badges | Gemini sparkle | Heuristic detect + soft inpaint |
| Audio spectral mask | SynthID-Audio, AudioSeal | Resample + filters + codec swap |
| Per-frame video | Kling, Veo, Sora | Optional per-frame spectral (≤90s) + ffmpeg chain |

## Honest limitations

- Detectors are probabilistic — not a cryptographic guarantee.
- Adaptive carriers are estimated **per file** (no downloaded fingerprint DB).
- We do not claim byte-identical pixels.
- Video spectral frame pass skips clips longer than 90s (still runs the ffmpeg attack).
- Use only on content you own or have the right to modify.

## Install + run (macOS)

```bash
brew install ffmpeg python@3.12 python-tk@3.12
```

Double-click `run.command`, or:

```bash
/opt/homebrew/bin/python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python tk_app.py
```

Optional Mac app: `.venv/bin/python scripts/build_app.py --install`

## Programmatic use

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

## Project layout

| File | Role |
|---|---|
| `watermark_remover.py` | Image / video / audio pipelines |
| `spectral_attack.py` | Adaptive FFT carrier detection + multi-pass dampening |
| `visible_mark.py` | Gemini / Nano Banana corner-badge inpaint |
| `c2pa_strip.py` | Deep JPEG/PNG/ISOBMFF C2PA·JUMBF scrub |
| `audit_report.py` | Before/after provenance JSON |
| `stripper.py` | Metadata-only pipeline |
| `origin_detect.py` | Origin / provenance heuristics |
| `tk_app.py` / `app.py` | Native + Gradio UIs |

## Research lineage

1. Hu et al. (2024), *Stable Signature is Unstable* — [arXiv:2405.07145](https://arxiv.org/abs/2405.07145)
2. Regeneration / diffusion removal attacks (Zhao et al. and follow-ons)
3. [00quebec/Synthid-Bypass](https://github.com/00quebec/Synthid-Bypass)
4. Community spectral analyses of SynthID carriers (motivate adaptive FFT dampening)
5. Google DeepMind, [SynthID](https://deepmind.google/models/synthid/)

## License

MIT — see [LICENSE](LICENSE).

## Ethics

Defensive / research tooling. Don’t use it to commit fraud or strip marks from work you don’t own.
