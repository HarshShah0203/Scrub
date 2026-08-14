#!/usr/bin/env python3
"""Scrub CLI — inspect / clean media and documents you own.

Examples:
  python cli.py inspect photo.jpg
  python cli.py inspect notes.md
  python cli.py clean photo.jpg -o ~/Desktop/Scrub
  python cli.py clean draft.md --metadata-only
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from document_strip import DOC_EXTS, clean_document, inspect_document
from text_hygiene import inspect_text, read_text_file

TEXTISH = {".txt", ".md", ".markdown", ".html", ".htm", ".svg", ".json", ".xml", ".csv"}
MEDIA_EXTS = {
    ".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff", ".heic", ".heif", ".avif",
    ".mp4", ".mov", ".m4v", ".mkv", ".webm", ".avi", ".mpeg", ".mpg", ".ogv",
    ".mp3", ".m4a", ".aac", ".wav", ".flac", ".ogg", ".oga", ".opus", ".wma",
}


def _ext(path: str) -> str:
    return os.path.splitext(path)[1].lower()


def cmd_inspect(path: str, force_text: bool) -> int:
    ext = _ext(path)
    if not os.path.exists(path):
        print(f"not found: {path}", file=sys.stderr)
        return 1
    if ext in DOC_EXTS:
        report = inspect_document(path)
    elif ext in MEDIA_EXTS:
        from stripper import inspect_file as inspect_media

        report = inspect_media(path)
        try:
            from origin_detect import detect_origin

            origin = detect_origin(path)
            report["origin_guess"] = {
                "matches": origin.matches,
                "likely_robust_watermark": origin.likely_robust_watermark,
            }
        except Exception:
            pass
    elif force_text or ext in TEXTISH:
        text = read_text_file(path, force=force_text)
        report = {
            "path": path,
            "filename": os.path.basename(path),
            "extension": ext,
            "kind": "text",
            "unicode": inspect_text(text),
        }
    else:
        print(f"unsupported type: {ext}", file=sys.stderr)
        return 2
    print(json.dumps(report, indent=2, default=str))
    return 0


def cmd_clean(
    path: str,
    output_dir: str,
    metadata_only: bool,
    strength: str,
    aggressive: bool,
) -> int:
    ext = _ext(path)
    os.makedirs(output_dir, exist_ok=True)
    if ext in DOC_EXTS:
        out, detail = clean_document(path, output_dir, aggressive=aggressive)
        print(f"{os.path.basename(path)} -> {out}\n{detail}")
        return 0
    if metadata_only:
        from stripper import strip_file_metadata

        out, detail = strip_file_metadata(path, output_dir)
        print(f"{os.path.basename(path)} -> {out}\n{detail}")
        return 0
    from watermark_remover import clean_file_v2

    report = clean_file_v2(
        input_path=path,
        output_dir=output_dir,
        strength=strength,  # type: ignore[arg-type]
        auto_strength=True,
        use_spectral=True,
        remove_visible=True,
        write_audit=True,
    )
    print(f"{os.path.basename(path)} -> {report.output_path}\n{report.detail}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="cli.py",
        description=(
            "Local offline hygiene for media and documents you own. "
            "See NOTICE.md. Best-effort only — no detector guarantees."
        ),
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    ins = sub.add_parser("inspect", help="JSON report of metadata / hidden Unicode")
    ins.add_argument("path")
    ins.add_argument("--force-text", action="store_true")

    cl = sub.add_parser("clean", help="Write a cleaned copy (original untouched)")
    cl.add_argument("path")
    cl.add_argument("-o", "--output-dir", default=os.path.expanduser("~/Desktop/Scrub"))
    cl.add_argument(
        "--metadata-only",
        action="store_true",
        help="Skip spectral/spatial image transforms (documents always metadata+unicode)",
    )
    cl.add_argument(
        "--strength",
        default="near_lossless",
        choices=["near_lossless", "light", "medium", "strong"],
    )
    cl.add_argument(
        "--aggressive",
        action="store_true",
        help="Also strip ZWJ/ZWNJ (can break emoji and some scripts)",
    )

    args = p.parse_args(argv)
    if args.cmd == "inspect":
        return cmd_inspect(args.path, args.force_text)
    return cmd_clean(
        args.path,
        args.output_dir,
        args.metadata_only,
        args.strength,
        args.aggressive,
    )


if __name__ == "__main__":
    raise SystemExit(main())
