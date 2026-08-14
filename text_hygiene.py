"""Deterministic Unicode hygiene for text you own.

Targets *edit-based* hidden carriers that show up in LLM clipboard output and
some document pipelines: zero-width chars, bidi overrides, Unicode tag
characters, and space homoglyphs.

This does **not** rewrite wording and does **not** claim to defeat statistical
(token-sampling) watermarks. Those live in word choice, not in hidden bytes.

Stdlib only.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from typing import Dict, Iterable, List, Tuple

# Invisible / format controls commonly used as hidden channels.
# ZWJ/ZWNJ are *not* stripped by default — they are required for emoji ZWJ
# sequences and for correct shaping in several writing systems.
_DELETE = {
    "\u200b",  # ZERO WIDTH SPACE
    "\u2060",  # WORD JOINER
    "\ufeff",  # BOM / ZWNBSP
    "\u180e",  # MONGOLIAN VOWEL SEPARATOR
    "\u034f",  # COMBINING GRAPHEME JOINER
    "\u00ad",  # SOFT HYPHEN
    "\u2061",  # FUNCTION APPLICATION
    "\u2062",  # INVISIBLE TIMES
    "\u2063",  # INVISIBLE SEPARATOR
    "\u2064",  # INVISIBLE PLUS
    "\u200e",  # LRM
    "\u200f",  # RLM
    "\u061c",  # ALM
    "\u202a",  # LRE
    "\u202b",  # RLE
    "\u202c",  # PDF
    "\u202d",  # LRO
    "\u202e",  # RLO
    "\u2066",  # LRI
    "\u2067",  # RLI
    "\u2068",  # FSI
    "\u2069",  # PDI
    "\ufffe",
    "\uffff",
}
_DELETE.update(chr(c) for c in range(0xE0001, 0xE0002))
_DELETE.update(chr(c) for c in range(0xE0020, 0xE0080))  # language tag chars

_AGGRESSIVE_DELETE = {
    "\u200c",  # ZWNJ
    "\u200d",  # ZWJ
}

# Space lookalikes → ASCII space (layout-preserving enough for hygiene).
_SPACE_MAP = {
    "\u00a0": " ",  # NBSP
    "\u1680": " ",
    "\u2000": " ",
    "\u2001": " ",
    "\u2002": " ",
    "\u2003": " ",
    "\u2004": " ",
    "\u2005": " ",
    "\u2006": " ",
    "\u2007": " ",
    "\u2008": " ",
    "\u2009": " ",
    "\u200a": " ",
    "\u202f": " ",
    "\u205f": " ",
    "\u3000": " ",
}

_TEXT_EXTS = {
    ".txt", ".md", ".markdown", ".rst", ".csv", ".tsv",
    ".json", ".jsonl", ".xml", ".yaml", ".yml",
    ".py", ".js", ".ts", ".css", ".html", ".htm", ".svg",
}


def _name(ch: str) -> str:
    code = f"U+{ord(ch):04X}"
    try:
        import unicodedata

        n = unicodedata.name(ch, "")
        return f"{code} {n}" if n else code
    except Exception:
        return code


def inspect_text(text: str, *, aggressive: bool = False) -> Dict:
    delete = set(_DELETE)
    if aggressive:
        delete |= _AGGRESSIVE_DELETE
    counts: Counter[str] = Counter()
    for ch in text:
        if ch in delete or ch in _SPACE_MAP:
            counts[ch] += 1
    findings = [
        {"char": _name(ch), "count": n, "kind": "delete" if ch in delete else "space"}
        for ch, n in sorted(counts.items(), key=lambda kv: (-kv[1], ord(kv[0])))
    ]
    return {
        "hidden_count": sum(n for ch, n in counts.items() if ch in delete),
        "space_homoglyph_count": sum(n for ch, n in counts.items() if ch in _SPACE_MAP),
        "findings": findings,
        "chars": len(text),
    }


def clean_text(text: str, *, aggressive: bool = False) -> Tuple[str, Dict]:
    delete = set(_DELETE)
    if aggressive:
        delete |= _AGGRESSIVE_DELETE
    before = inspect_text(text, aggressive=aggressive)
    out: List[str] = []
    for ch in text:
        if ch in delete:
            continue
        out.append(_SPACE_MAP.get(ch, ch))
    cleaned = "".join(out)
    after = inspect_text(cleaned, aggressive=aggressive)
    return cleaned, {"before": before, "after": after, "changed": cleaned != text}


def looks_binary(data: bytes) -> bool:
    if data.startswith(b"PK\x03\x04") or data.startswith(b"%PDF") or data.startswith(b"\x89PNG"):
        return True
    if data[:3] in (b"\xff\xd8\xff",) or data[:4] == b"RIFF":
        return True
    sample = data[:8192]
    if not sample:
        return False
    # NUL bytes or a high ratio of C0 controls (excluding \t\n\r) ⇒ binary.
    if b"\x00" in sample:
        return True
    ctrl = sum(1 for b in sample if b < 9 or 11 <= b <= 12 or 14 <= b <= 31)
    return (ctrl / len(sample)) > 0.30


def read_text_file(path: str, *, force: bool = False) -> str:
    with open(path, "rb") as f:
        data = f.read()
    if not force and looks_binary(data):
        raise ValueError(
            f"refusing to treat {path} as text (looks binary). "
            "Use `python cli.py inspect/clean` which routes by format, "
            "or pass --force-text."
        )
    return data.decode("utf-8", errors="replace")


def main(argv: Iterable[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Inspect or strip hidden Unicode from text you own.")
    p.add_argument("path")
    p.add_argument("--clean", action="store_true")
    p.add_argument("-o", "--output")
    p.add_argument("--aggressive", action="store_true")
    p.add_argument("--force-text", action="store_true")
    args = p.parse_args(list(argv) if argv is not None else None)
    text = read_text_file(args.path, force=args.force_text)
    if args.clean:
        cleaned, stats = clean_text(text, aggressive=args.aggressive)
        if args.output:
            with open(args.output, "w", encoding="utf-8", newline="") as f:
                f.write(cleaned)
        else:
            sys.stdout.write(cleaned)
        print(json.dumps(stats, indent=2), file=sys.stderr)
        return 0
    print(json.dumps(inspect_text(text, aggressive=args.aggressive), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
