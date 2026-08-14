import os
import subprocess
import json
import hashlib
from dataclasses import dataclass
from typing import Optional, Tuple, Any, Dict

from document_strip import DOC_EXTS, clean_document, inspect_document


IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff", ".heic", ".heif", ".avif"}
VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".mkv", ".webm", ".avi", ".mpeg", ".mpg", ".ogv"}
AUDIO_EXTS = {".mp3", ".m4a", ".aac", ".wav", ".flac", ".ogg", ".oga", ".opus", ".wma"}


def _ext_lower(path: str) -> str:
    _, ext = os.path.splitext(path)
    return ext.lower()


def _unique_path(out_path: str) -> str:
    if not os.path.exists(out_path):
        return out_path
    base, ext = os.path.splitext(out_path)
    i = 1
    while True:
        candidate = f"{base}_{i}{ext}"
        if not os.path.exists(candidate):
            return candidate
        i += 1


@dataclass(frozen=True)
class StripResult:
    output_path: str
    detail: str


def _run(cmd: list) -> None:
    # Keep stderr for debugging; raise with useful text if ffmpeg fails.
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or f"Command failed: {' '.join(cmd)}")


def get_tool_versions() -> Dict[str, Any]:
    """
    Return versions of external tools used by this pipeline.

    Best-effort: if a tool isn't available, it returns an error field instead.
    """

    def _try(cmd: list) -> Dict[str, Any]:
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True)
            first_line = (proc.stdout or proc.stderr or "").strip().splitlines()[:1]
            return {
                "ok": proc.returncode == 0,
                "returncode": proc.returncode,
                "first_line": first_line[0] if first_line else None,
            }
        except Exception as e:
            return {"ok": False, "error": str(e)}

    # Python version: use the running interpreter.
    py_version = None
    try:
        import sys

        py_version = sys.version.splitlines()[0]
    except Exception:
        py_version = None

    return {
        "python": {"version": py_version},
        "ffmpeg": _try(["ffmpeg", "-version"]),
        "ffprobe": _try(["ffprobe", "-version"]),
    }


def _strip_image_with_pillow(input_path: str, output_dir: str) -> StripResult:
    from PIL import Image

    ext = _ext_lower(input_path)
    if ext in {".heic", ".heif", ".avif"}:
        try:
            from pillow_heif import register_heif_opener  # type: ignore
            register_heif_opener()
        except Exception as e:
            raise RuntimeError(
                "HEIC/HEIF/AVIF support requires pillow-heif. "
                f"pip install pillow-heif ({e})"
            )

    img = Image.open(input_path)

    # Copy pixel data into a new image container and drop info dict to avoid
    # carrying over EXIF/metadata-like fields on save.
    img2 = img.copy()
    try:
        img2.info = {}
    except Exception:
        # Best-effort; continue even if Pillow implementation differs.
        pass

    in_ext = _ext_lower(input_path)
    if in_ext in {".heic", ".heif", ".avif"}:
        out_ext = ".jpg"
    else:
        out_ext = in_ext if in_ext in {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"} else ".png"
    out_path = _unique_path(os.path.join(output_dir, os.path.splitext(os.path.basename(input_path))[0] + "_clean" + out_ext))

    fmt = None
    # Pillow expects format name without dot.
    if out_ext in {".jpg", ".jpeg"}:
        fmt = "JPEG"
    elif out_ext == ".png":
        fmt = "PNG"
    elif out_ext == ".webp":
        fmt = "WEBP"
    elif out_ext == ".bmp":
        fmt = "BMP"
    elif out_ext in {".tif", ".tiff"}:
        fmt = "TIFF"
    else:
        fmt = img.format

    if fmt == "JPEG" and img2.mode not in ("RGB", "L"):
        img2 = img2.convert("RGB")
    img2.save(out_path, format=fmt)
    detail = "Pillow re-save (metadata dropped)"
    try:
        from c2pa_strip import deep_strip_c2pa_file
        c2pa_detail, n = deep_strip_c2pa_file(out_path)
        if n:
            detail += f"; {c2pa_detail}"
    except Exception:
        pass
    return StripResult(output_path=out_path, detail=detail)


def _strip_media_with_ffmpeg(
    input_path: str,
    output_dir: str,
    prefer_stream_copy: bool,
) -> StripResult:
    in_ext = _ext_lower(input_path)
    base_name = os.path.splitext(os.path.basename(input_path))[0]

    # For best results, keep the container when it's common; otherwise fall back to MP4.
    target_ext = in_ext if in_ext in {".mp4", ".mov", ".m4v", ".mkv", ".webm", ".avi", ".ogg", ".ogv"} else ".mp4"
    out_path = _unique_path(os.path.join(output_dir, base_name + "_clean" + target_ext))

    # 1) Fast path: stream copy + drop container metadata.
    if prefer_stream_copy:
        cmd = [
            "ffmpeg",
            "-y",
            "-i",
            input_path,
            "-map_metadata",
            "-1",
            "-c",
            "copy",
            out_path,
        ]
        try:
            _run(cmd)
            return StripResult(output_path=out_path, detail="ffmpeg remux (stream copy, metadata dropped)")
        except Exception:
            # Fall back to re-encode.
            pass

    # 2) Safe path: re-encode with metadata dropped.
    # Choose codecs based on output container.
    if target_ext == ".webm":
        v_codec = "libvpx-vp9"
        a_codec = "libopus"
        a_bitrate = "128k"
    elif target_ext == ".ogg" or target_ext == ".ogv":
        v_codec = "libtheora"
        a_codec = "libvorbis"
        a_bitrate = "128k"
    else:
        # Most compatible: MP4/MOV.
        v_codec = "libx264"
        a_codec = "aac"
        a_bitrate = "192k"

    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        input_path,
        "-map_metadata",
        "-1",
        "-c:v",
        v_codec,
        "-crf",
        "18",
        "-preset",
        "veryfast",
        "-c:a",
        a_codec,
        "-b:a",
        a_bitrate,
        out_path,
    ]
    _run(cmd)
    return StripResult(output_path=out_path, detail="ffmpeg re-encode (metadata dropped)")


def strip_file_metadata(
    input_path: str,
    output_dir: str,
    prefer_stream_copy: bool = True,
) -> Tuple[str, str]:
    """
    Strip removable metadata (container tags / EXIF-like fields) from media.

    Security note: this function does NOT remove invisible provenance/protection
    watermarking schemes.
    """
    ext = _ext_lower(input_path)
    os.makedirs(output_dir, exist_ok=True)
    if ext in IMAGE_EXTS:
        res = _strip_image_with_pillow(input_path, output_dir)
        return res.output_path, res.detail

    if ext in VIDEO_EXTS or ext in AUDIO_EXTS:
        res = _strip_media_with_ffmpeg(
            input_path=input_path,
            output_dir=output_dir,
            prefer_stream_copy=prefer_stream_copy,
        )
        return res.output_path, res.detail

    if ext in DOC_EXTS:
        return clean_document(input_path, output_dir)

    raise ValueError(f"Unsupported file type: {ext} ({os.path.basename(input_path)})")


def _file_sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def inspect_file(
    input_path: str,
    include_sha256: bool = False,
) -> Dict[str, Any]:
    """
    Audit mode: extract visible/removable properties for reporting.

    Note: this does NOT attempt to decode or bypass invisible watermarking/provenance.
    """
    ext = _ext_lower(input_path)

    st = os.stat(input_path)
    base: Dict[str, Any] = {
        "path": input_path,
        "filename": os.path.basename(input_path),
        "extension": ext,
        "size_bytes": st.st_size,
        "mtime_unix": st.st_mtime,
    }
    if include_sha256:
        base["sha256"] = _file_sha256(input_path)

    if ext in IMAGE_EXTS:
        from PIL import Image, ExifTags

        img = Image.open(input_path)
        info = dict(getattr(img, "info", {}) or {})

        exif = {}
        try:
            ex = img.getexif()
            if ex:
                for k, v in dict(ex).items():
                    exif[ExifTags.TAGS.get(k, str(k))] = v
        except Exception:
            # Best-effort: some formats/IO paths may not expose EXIF.
            pass

        base["image"] = {
            "format": getattr(img, "format", None),
            "mode": getattr(img, "mode", None),
            "size": getattr(img, "size", None),
            "pillow_info_keys": sorted(list(info.keys())),
            "exif": exif,
        }
        return base

    if ext in VIDEO_EXTS or ext in AUDIO_EXTS:
        # `ffprobe` can be used offline, and returns container/stream properties.
        cmd = [
            "ffprobe",
            "-v",
            "error",
            "-show_format",
            "-show_streams",
            "-print_format",
            "json",
            input_path,
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr.strip() or "ffprobe failed")

        try:
            probe = json.loads(proc.stdout)
        except Exception as e:
            raise RuntimeError(f"ffprobe JSON parse failed: {e}")

        base["ffprobe"] = probe
        return base

    if ext in DOC_EXTS:
        return inspect_document(input_path)

    raise ValueError(f"Unsupported file type for audit: {ext} ({os.path.basename(input_path)})")


def summarize_inspection(report: Dict[str, Any]) -> Dict[str, Any]:
    """
    Create a compact, comparable summary for reports.

    This keeps the output small and focused on metadata-like properties.
    """
    out: Dict[str, Any] = {
        "filename": report.get("filename"),
        "extension": report.get("extension"),
        "size_bytes": report.get("size_bytes"),
    }
    if "sha256" in report:
        out["sha256"] = report["sha256"]

    if "image" in report:
        img = report.get("image", {}) or {}
        exif = img.get("exif", {}) or {}
        # Keep small evidence payload; values help with before/after diffs.
        max_items = 200
        if len(exif) <= max_items:
            exif_values = {str(k): str(v) for k, v in exif.items()}
        else:
            exif_values = None
        out["image"] = {
            "format": img.get("format"),
            "mode": img.get("mode"),
            "size": img.get("size"),
            "exif_keys": sorted(list(exif.keys())),
            "exif_values": exif_values,
        }
        return out

    fp = report.get("ffprobe")
    if fp:
        fmt = fp.get("format", {}) or {}
        streams = fp.get("streams", []) or []
        tags = fmt.get("tags", {}) or {}

        stream_summaries = []
        for s in streams:
            if not isinstance(s, dict):
                continue
            item: Dict[str, Any] = {
                "index": s.get("index"),
                "codec_type": s.get("codec_type"),
                "codec_name": s.get("codec_name"),
            }
            # Include a few common stream properties when present.
            for key in ("width", "height", "sample_rate", "channels", "duration"):
                if key in s:
                    item[key] = s.get(key)
            stream_summaries.append(item)

        tag_keys = sorted(list(tags.keys()))
        out["ffprobe"] = {
            "format": {
                "duration": fmt.get("duration"),
                "bit_rate": fmt.get("bit_rate"),
            },
            "format_tag_keys": tag_keys,
            "streams": stream_summaries,
        }
        return out

    return out


def diff_summaries(before: Dict[str, Any], after: Dict[str, Any]) -> Dict[str, Any]:
    """
    Compute a small diff between two summarized reports.
    """
    diff: Dict[str, Any] = {"changed": before != after}

    if before.get("extension") in IMAGE_EXTS and "image" in before and "image" in after:
        b_keys = set(before["image"].get("exif_keys", []))
        a_keys = set(after["image"].get("exif_keys", []))
        diff["exif_keys_added"] = sorted(list(a_keys - b_keys))
        diff["exif_keys_removed"] = sorted(list(b_keys - a_keys))

        # Add a capped before/after diff for changed EXIF values when we have them.
        b_vals = before["image"].get("exif_values")
        a_vals = after["image"].get("exif_values")
        if isinstance(b_vals, dict) and isinstance(a_vals, dict):
            changed_keys = []
            for k in sorted(list(set(b_vals.keys()) & set(a_vals.keys()))):
                if b_vals.get(k) != a_vals.get(k):
                    changed_keys.append(k)
            diff["exif_keys_changed"] = changed_keys[:50]
            if changed_keys:
                diff["exif_changed_values"] = {
                    k: {"before": b_vals.get(k), "after": a_vals.get(k)} for k in changed_keys[:25]
                }
        return diff

    # For media container/stream differences, focus on codec layout + some format fields.
    if "ffprobe" in before and "ffprobe" in after:
        b_fmt = before.get("ffprobe", {}).get("format", {}) or {}
        a_fmt = after.get("ffprobe", {}).get("format", {}) or {}
        diff["format_duration_before"] = b_fmt.get("duration")
        diff["format_duration_after"] = a_fmt.get("duration")
        diff["format_bit_rate_before"] = b_fmt.get("bit_rate")
        diff["format_bit_rate_after"] = a_fmt.get("bit_rate")

        b_tag_keys = before.get("ffprobe", {}).get("format_tag_keys", []) or []
        a_tag_keys = after.get("ffprobe", {}).get("format_tag_keys", []) or []
        diff["format_tag_keys_added"] = sorted(list(set(a_tag_keys) - set(b_tag_keys)))[:200]
        diff["format_tag_keys_removed"] = sorted(list(set(b_tag_keys) - set(a_tag_keys)))[:200]

        def codecs_by_type(summary: Dict[str, Any]) -> Dict[str, set]:
            res: Dict[str, set] = {}
            for s in summary.get("ffprobe", {}).get("streams", []) or []:
                if not isinstance(s, dict):
                    continue
                ct = s.get("codec_type")
                cn = s.get("codec_name")
                res.setdefault(ct, set()).add(cn)
            return res

        b_c = codecs_by_type(before)
        a_c = codecs_by_type(after)
        codec_types = sorted(set(list(b_c.keys()) + list(a_c.keys())))
        diff["codec_layout_diff"] = {}
        for t in codec_types:
            diff["codec_layout_diff"][t] = {
                "before": sorted(list(b_c.get(t, set()))),
                "after": sorted(list(a_c.get(t, set()))),
            }

    return diff

