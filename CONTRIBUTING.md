# Contributing

Thanks for contributing to Scrub.

Please read [NOTICE.md](NOTICE.md). This project is for research and for processing media you have rights to modify — not for fraud or misrepresenting provenance.

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python tk_app.py   # or app.py
```

## Good first areas

- Corner-badge detection edge cases
- Faster video spectral path for clips >90s
- Windows packaging
- Tests for `c2pa_strip`, `spectral_attack`, `visible_mark`

When filing issues, use synthetic samples you own. Do not upload other people’s private media.

## Guidelines

- Stay fully offline by default (no telemetry, no required cloud APIs).
- Prefer small, reviewable PRs.
- Do not add uploaders, scrapers, or paid third-party “remover” API keys.
- Be precise in docs: say **degrade / disrupt / best-effort**, never “guaranteed bypass” or “undetectable.”
- Do not imply affiliation with Google, OpenAI, Meta, or any other vendor.
- Only test on media you have rights to modify.

## Conduct

Be respectful. Discuss techniques as robustness research, not as advice for committing fraud or violating others’ rights.
