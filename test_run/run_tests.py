"""End-to-end test harness for the Metadata & Watermark Cleaner."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass

import numpy as np
from PIL import Image, ExifTags

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from watermark_remover import clean_file  # noqa: E402
from stripper import strip_file_metadata  # noqa: E402


IN_DIR = os.path.join(os.path.dirname(__file__), "in")
OUT_DIR = os.path.join(os.path.dirname(__file__), "out")


@dataclass
class Case:
    name: str
    input: str
    mode: str            # "clean" or "metadata_only"
    strength: str = "medium"


def image_exif(path: str) -> dict:
    im = Image.open(path)
    ex = im.getexif()
    return {ExifTags.TAGS.get(k, str(k)): str(v) for k, v in dict(ex).items()}


def image_info_keys(path: str) -> list:
    im = Image.open(path)
    return sorted(list((im.info or {}).keys()))


def ffprobe_tags(path: str) -> dict:
    proc = subprocess.run(
        ["ffprobe", "-v", "error", "-show_format", "-show_streams",
         "-print_format", "json", path],
        capture_output=True, text=True,
    )
    d = json.loads(proc.stdout or "{}")
    fmt_tags = (d.get("format") or {}).get("tags", {}) or {}
    stream_tags = [(s.get("tags", {}) or {}) for s in d.get("streams", [])]
    codecs = [s.get("codec_name") for s in d.get("streams", [])]
    return {"format_tags": fmt_tags, "stream_tags": stream_tags, "codecs": codecs}


def pixel_delta(a_path: str, b_path: str) -> dict | None:
    a = np.asarray(Image.open(a_path).convert("RGB"), dtype=np.int32)
    b = np.asarray(Image.open(b_path).convert("RGB"), dtype=np.int32)
    if a.shape != b.shape:
        return {"shape_match": False, "a": a.shape, "b": b.shape}
    d = np.abs(a - b)
    return {
        "shape_match": True,
        "shape": a.shape,
        "mean_abs_delta": round(float(d.mean()), 3),
        "max_delta": int(d.max()),
        "pct_pixels_changed": round(float((d.sum(-1) != 0).mean()) * 100, 2),
    }


def run_case(c: Case) -> dict:
    os.makedirs(OUT_DIR, exist_ok=True)
    src = os.path.join(IN_DIR, c.input)

    # Build sub-dir per case so _clean doesn't collide between runs.
    case_out = os.path.join(OUT_DIR, c.name)
    os.makedirs(case_out, exist_ok=True)

    if c.mode == "metadata_only":
        out_path, detail = strip_file_metadata(src, case_out, prefer_stream_copy=True)
    else:
        out_path, detail = clean_file(src, case_out, strength=c.strength)

    report = {"case": c.name, "mode": c.mode, "strength": c.strength, "detail": detail,
              "src": src, "out": out_path,
              "src_size": os.path.getsize(src), "out_size": os.path.getsize(out_path)}

    ext = os.path.splitext(src)[1].lower()
    if ext in {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}:
        report["before"] = {"exif": image_exif(src), "info_keys": image_info_keys(src)}
        report["after"] = {"exif": image_exif(out_path), "info_keys": image_info_keys(out_path)}
        report["pixel_delta"] = pixel_delta(src, out_path)
    else:
        report["before"] = ffprobe_tags(src)
        report["after"] = ffprobe_tags(out_path)

    return report


def check(report: dict) -> list[str]:
    issues = []
    name = report["case"]
    after = report["after"]
    before = report["before"]
    mode = report["mode"]

    if "exif" in before:
        # image
        if after.get("exif"):
            issues.append(f"{name}: EXIF not fully removed: {list(after['exif'].keys())}")
        if mode == "clean":
            pd = report.get("pixel_delta") or {}
            if not pd.get("shape_match"):
                issues.append(f"{name}: output resolution changed: {pd}")
            elif pd.get("pct_pixels_changed", 0) < 50:
                issues.append(f"{name}: <50% of pixels changed ({pd.get('pct_pixels_changed')}%)")
            elif pd.get("max_delta", 0) < 1:
                issues.append(f"{name}: pixels identical (max_delta=0)")
    else:
        # video / audio
        # Sensitive user tags that must be gone.
        sensitive_keys = {"title", "artist", "composer", "album", "comment",
                          "description", "copyright", "creation_time"}
        bad_format = sensitive_keys & set(after.get("format_tags") or {})
        if bad_format:
            issues.append(f"{name}: sensitive format tags survived: {bad_format}")
        for i, st in enumerate(after.get("stream_tags") or []):
            bad_stream = sensitive_keys & set(st or {})
            if bad_stream:
                issues.append(f"{name}: sensitive stream[{i}] tags survived: {bad_stream}")
        if mode == "clean":
            # For video at least, we expect codec swap when possible.
            pass
    return issues


def main():
    cases = [
        Case("image_light",    "sample.jpg", "clean", "light"),
        Case("image_medium",   "sample.jpg", "clean", "medium"),
        Case("image_strong",   "sample.jpg", "clean", "strong"),
        Case("image_metaonly", "sample.jpg", "metadata_only"),
        Case("video_medium",   "sample.mp4", "clean", "medium"),
        Case("video_metaonly", "sample.mp4", "metadata_only"),
        Case("audio_medium",   "sample.mp3", "clean", "medium"),
        Case("audio_metaonly", "sample.mp3", "metadata_only"),
    ]

    all_issues = []
    reports = []
    for c in cases:
        try:
            r = run_case(c)
        except Exception as e:
            all_issues.append(f"{c.name}: RAISED {type(e).__name__}: {e}")
            continue
        reports.append(r)
        issues = check(r)
        all_issues.extend(issues)

        print(f"[{c.name}] {c.mode}/{c.strength}")
        print(f"  src={r['src_size']:>7}B  out={r['out_size']:>7}B")
        print(f"  detail: {r['detail']}")
        if "pixel_delta" in r:
            pd = r["pixel_delta"] or {}
            print(f"  pixels: {pd.get('pct_pixels_changed')}% changed, "
                  f"max Δ {pd.get('max_delta')}/255, mean {pd.get('mean_abs_delta')}")
        if "exif" in r.get("before", {}):
            print(f"  exif before: {list(r['before']['exif'].keys()) or 'none'}")
            print(f"  exif after:  {list(r['after']['exif'].keys()) or 'none'}")
        else:
            print(f"  tags before: {r['before'].get('format_tags')}")
            print(f"  tags after:  {r['after'].get('format_tags')}")
            print(f"  codecs: {r['before'].get('codecs')} -> {r['after'].get('codecs')}")
        print()

    with open(os.path.join(OUT_DIR, "report.json"), "w") as f:
        json.dump({"cases": reports, "issues": all_issues}, f, indent=2)

    print("=" * 60)
    if all_issues:
        print(f"FAIL: {len(all_issues)} issue(s)")
        for i in all_issues:
            print("  -", i)
        sys.exit(1)
    else:
        print("PASS: all cases succeeded and verified.")


if __name__ == "__main__":
    main()
