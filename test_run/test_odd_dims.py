"""
Reproduce + regression-check the "odd dimensions" encoder failure.

Generates a 1080x1920 (phone-portrait) synthetic MP4 — exactly the shape
that triggered "All video encoders failed" on real uploads — and runs it
through clean_file_v2 at every strength. Passes if every strength
produces a playable mp4.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from watermark_remover import clean_file_v2  # noqa: E402


def _make_mp4(path: str, w: int, h: int, seconds: int = 2) -> None:
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i",
        f"testsrc=size={w}x{h}:rate=24:duration={seconds}",
        "-f", "lavfi", "-i",
        f"sine=frequency=440:duration={seconds}",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "veryfast",
        "-c:a", "aac", "-b:a", "96k",
        path,
    ]
    subprocess.run(cmd, check=True, capture_output=True, text=True)


def _probe(path: str) -> dict:
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_streams", "-print_format", "json", path],
        capture_output=True, text=True,
    )
    return json.loads(r.stdout or "{}")


def run():
    passes = 0
    fails = []

    shapes = [(1080, 1920), (720, 1280), (640, 360), (1024, 768)]
    strengths = ["near_lossless", "light", "medium", "strong"]

    with tempfile.TemporaryDirectory() as tmp:
        in_dir = os.path.join(tmp, "in")
        out_dir = os.path.join(tmp, "out")
        os.makedirs(in_dir)
        os.makedirs(out_dir)

        for w, h in shapes:
            src = os.path.join(in_dir, f"src_{w}x{h}.mp4")
            _make_mp4(src, w, h)
            for s in strengths:
                try:
                    rep = clean_file_v2(src, out_dir, strength=s,
                                        use_diffusion=False,
                                        auto_strength=False)
                except Exception as e:
                    fails.append(f"{w}x{h} @ {s}: {type(e).__name__}: {e}")
                    continue

                if not (os.path.exists(rep.output_path) and
                        os.path.getsize(rep.output_path) > 1024):
                    fails.append(f"{w}x{h} @ {s}: missing/empty output")
                    continue

                # Must be decodable and have a video stream of even dims.
                info = _probe(rep.output_path)
                vs = [s for s in (info.get("streams") or [])
                      if s.get("codec_type") == "video"]
                if not vs:
                    fails.append(f"{w}x{h} @ {s}: output has no video stream")
                    continue
                vinfo = vs[0]
                ow = int(vinfo.get("width") or 0)
                oh = int(vinfo.get("height") or 0)
                if ow % 2 or oh % 2 or ow < 8 or oh < 8:
                    fails.append(f"{w}x{h} @ {s}: bad output dims {ow}x{oh}")
                    continue

                # Resolution preservation: output must be pixel-for-pixel the
                # same size as the source. "4K stays 4K, 1080p stays 1080p."
                if (ow, oh) != (w, h):
                    fails.append(
                        f"{w}x{h} @ {s}: output {ow}x{oh} does not match source"
                    )
                    continue

                # QuickTime compatibility: if the output is HEVC, its fourcc
                # tag must be `hvc1`. QuickTime refuses to render `hev1` and
                # falls back to audio-only playback -- which is the exact
                # bug the user hit.
                codec = vinfo.get("codec_name") or ""
                tag = vinfo.get("codec_tag_string") or ""
                if codec.lower() == "hevc" and tag.lower() != "hvc1":
                    fails.append(
                        f"{w}x{h} @ {s}: HEVC output has tag={tag!r} "
                        f"(must be 'hvc1' for QuickTime)"
                    )
                    continue
                # Pixel format must be yuv420p for QT / Safari / browsers.
                if (vinfo.get("pix_fmt") or "") not in ("yuv420p",):
                    fails.append(
                        f"{w}x{h} @ {s}: pix_fmt={vinfo.get('pix_fmt')!r} "
                        f"(need yuv420p for universal compat)"
                    )
                    continue

                passes += 1
                print(f"PASS  {w}x{h} @ {s:14s}  -> {ow}x{oh}  "
                      f"codec={codec}  tag={tag}  pix={vinfo.get('pix_fmt')}")

    print()
    print(f"Passes: {passes}  Fails: {len(fails)}")
    for f in fails:
        print(f"  - {f}")
    if fails:
        sys.exit(1)
    print("ALL GREEN")


if __name__ == "__main__":
    run()
