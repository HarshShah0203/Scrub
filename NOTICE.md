# Notice

**Not legal advice.** This document states how Scrub is intended to be used and what it is (and is not). You are responsible for complying with the laws that apply to you.

## Intended use

Scrub is offered for:

- **Robustness research** — studying how metadata tags and invisible watermark *signals* behave under ordinary signal-processing transforms
- **Privacy / hygiene on media you own** — stripping EXIF and similar metadata from your own files before sharing
- **Evaluating your own generative outputs** — understanding what provenance signals remain in content you created or have rights to modify

## Not intended for

Do **not** use Scrub to:

- Misrepresent the origin, authorship, or authenticity of media
- Strip provenance or watermarks from content you do not own or lack permission to modify
- Commit fraud, evade platform rules you agreed to, or violate local law (including copyright and anti-circumvention rules where they apply)
- Harass, defame, or infringe others’ rights

If you are unsure whether a use is lawful, do not use the tool for that purpose.

## What Scrub actually does

Scrub applies **best-effort, imperfect** transforms:

- Metadata scrubbing (EXIF / IPTC / XMP / container tags, and best-effort C2PA/JUMBF cleanup)
- Heuristic inpainting of small corner badges that *look like* common AI UI marks
- Research-style spatial / spectral signal disruption aimed at *watermark-like* carriers discussed in public literature

It does **not**:

- Guarantee that any particular vendor detector will fail
- Decode proprietary watermark codebooks or claim an official “bypass”
- Produce cryptographically proven “clean” files
- Provide legal cover for misuse

Detection systems change. Results vary by file, model, and detector version.

## No affiliation / trademarks

Scrub is an independent open-source project. It is **not affiliated with, endorsed by, or sponsored by** Google, DeepMind, OpenAI, Meta, Midjourney, Kling, ByteDance, Stability AI, the C2PA, or any commercial “watermark remover” service.

Product and technology names (for example SynthID, Gemini, Veo, Sora, AudioSeal, Content Credentials) may appear only to describe publicly discussed signal families or metadata fields. Those names remain the property of their owners.

## Warranty and liability

The software is provided under the [MIT License](LICENSE) **“AS IS”**, without warranty of any kind. To the maximum extent permitted by law, the authors are not liable for damages or claims arising from use or misuse of this software. **You** decide whether a given use is appropriate and lawful.

## Reporting concern

If you believe this repository is being used in a way that causes you harm, or you need something corrected in the docs, open a GitHub issue or contact the maintainer through GitHub.
