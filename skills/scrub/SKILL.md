---
name: scrub
description: >-
  Local offline hygiene for media and documents the user owns. Strips EXIF/IPTC/XMP,
  best-effort C2PA/JUMBF, hidden Unicode (zero-width, bidi, tag chars), and document
  properties in PDF/DOCX/ODT/HTML/SVG/Markdown; optional spectral disruption for
  images/video/audio. Use when the user asks to scrub, clean metadata, strip C2PA,
  Content Credentials, EXIF, hidden Unicode, AI marks, SynthID-class research
  transforms, or inspect provenance on files they have rights to modify.
---

# Scrub

Run the repo CLI. Do not upload files. Do not claim a detector will fail.

## Rules

- Only process files the user owns or has explicit rights to modify.
- Originals stay untouched; outputs use a `_clean` suffix.
- Say **best-effort / degrade / strip tags** — never “guaranteed bypass” or “undetectable.”
- Statistical (token-sampling) text watermarks are **out of scope**. Unicode hygiene is not a paraphrase rewrite.

## Commands

From the Scrub repo root (prefer `.venv/bin/python` if present):

```bash
python cli.py inspect PATH
python cli.py clean PATH -o OUTPUT_DIR
python cli.py clean PATH -o OUTPUT_DIR --metadata-only
```

| Input | What `clean` does |
|---|---|
| `.jpg .png .webp .heic …` | Metadata + optional spectral/spatial chain |
| `.mp4 .mov .wav .mp3 …` | ffmpeg remux/re-encode, drop tags; optional spectral on short video |
| `.md .txt .html .svg` | Hidden Unicode + obvious generator/C2PA-ish meta |
| `.docx .odt` | Core/app props + XML text hygiene |
| `.pdf` | Info/XMP via pypdf or exiftool when installed |

## Workflow

1. Confirm the path and that the user wants a **copy**, not in-place overwrite.
2. `inspect` first if they asked what is in the file.
3. `clean` with `--metadata-only` unless they asked for image/video signal disruption.
4. Show the output path and quote the CLI `detail` line. Offer `NOTICE.md` if they ask about legality.

## Install (optional, user machine)

```bash
# Cursor
mkdir -p ~/.cursor/skills && ln -sfn "$(pwd)/skills/scrub" ~/.cursor/skills/scrub
# Claude Code
mkdir -p ~/.claude/skills && ln -sfn "$(pwd)/skills/scrub" ~/.claude/skills/scrub
```
