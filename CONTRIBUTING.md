# Contributing to Scrub

Thanks for helping keep a **free, local, offline** alternative to paid SynthID/C2PA removers alive.

## Ways to help

- **Star** the repo if Scrub is useful — discovery matters for open tools competing with paid SaaS.
- **Fork** and open PRs for fixes, packaging, or better attacks.
- File issues with sample *synthetic* files you own (never leak others’ private media).

## High-value contributions

1. Visible Gemini / Nano Banana badge detection edge cases  
2. Faster video spectral path for clips >90s  
3. Windows `.exe` / installer packaging  
4. README before/after demo images (content you generated)  
5. Tests for `c2pa_strip`, `spectral_attack`, `visible_mark`

## Dev setup

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python tk_app.py   # or app.py
```

## PR guidelines

- Keep the tool **fully offline by default** (no telemetry, no required cloud).  
- Prefer small, reviewable PRs.  
- Don’t add paid API keys or uploaders.  
- Be honest in docs: no “guaranteed detector bypass” claims.  
- Use only on media you have rights to modify when testing.

## Code of conduct (short)

Be respectful. This is dual-use research tooling — discuss attacks in the context of robustness research and user-owned content, not fraud.
