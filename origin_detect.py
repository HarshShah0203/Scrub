"""
Origin / provenance hints from *embedded metadata* (offline).

Goal: given a file, notice whether metadata suggests a known generative
toolchain, so the pipeline can optionally use a stronger disruption preset.

Signals inspected (no network):

    * Image EXIF tags: `Software`, `Make`, `Model`, `HostComputer`,
      `ProcessingSoftware`, `ImageUniqueID`.
    * XMP / XML-ish bytes: CreatorTool, claim_generator, jumbf, etc.
    * PNG `tEXt` / `iTXt` chunks.
    * ffprobe format + stream tags for video / audio.

We never trust any *single* signal; we combine them and return structured
reasons so the UI can explain itself.

Substring matches (case-insensitive) include common generator / encoder
strings that appear in public tooling (Imagen, Gemini, Veo, Sora, AudioSeal,
C2PA claim_generator issuers, etc.). These are metadata heuristics only —
not proof that a particular invisible watermark is present.

If upstream metadata was already stripped, detection will miss and the
pipeline stays on the near-lossless default.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from typing import Dict, List, Optional


# Homebrew-aware resolution of ffprobe (see watermark_remover.py for context).
_FFPROBE_FALLBACKS = [
    "/opt/homebrew/bin/ffprobe", "/usr/local/bin/ffprobe", "/opt/local/bin/ffprobe",
]


def _ffprobe_bin() -> str:
    path = shutil.which("ffprobe")
    if path:
        return path
    for cand in _FFPROBE_FALLBACKS:
        if os.path.exists(cand):
            return cand
    return "ffprobe"


FFPROBE = _ffprobe_bin()


# Ordered, most-specific first. "robust" indicates watermark families that
# are known to survive mild attacks, so we should escalate strength.
#
# Matching is case-insensitive, on concatenated metadata strings for the file.
PATTERNS: List[dict] = [
    # -- Google image / video / audio stack ---------------------------------
    {"label": "google_synthid_image", "kind": "image",
     "robust": True, "needles": ["imagen", "nano banana", "nano-banana",
                                 "gemini 2", "gemini-2", "geminihub",
                                 "google.com/synthid", "synthid-image",
                                 "google.com/gemini", "ai.studio/gemini",
                                 "google deepmind", "com.google.gemini",
                                 "vertex-ai-imagen", "vertexai-imagen"]},
    {"label": "google_synthid_video", "kind": "video",
     "robust": True, "needles": ["veo", "synthid-video", "com.google.veo",
                                 "googleveo", "deepmind/veo"]},
    {"label": "google_synthid_audio", "kind": "audio",
     "robust": True, "needles": ["lyria", "synthid-audio", "com.google.lyria",
                                 "music-ai", "musicfx"]},

    # -- OpenAI ------------------------------------------------------------
    {"label": "openai_sora_video", "kind": "video",
     "robust": True, "needles": ["sora.com", "openai sora", "c2pa.openai.com",
                                 "com.openai.sora", "sora-", "openai/sora"]},
    {"label": "openai_image",      "kind": "image",
     "robust": False, "needles": ["dall-e", "dall·e", "dalle 3", "dalle-3",
                                  "c2pa.openai.com", "openai/dall",
                                  "chatgpt image", "image.openai"]},

    # -- Meta --------------------------------------------------------------
    {"label": "meta_audioseal", "kind": "audio",
     "robust": True, "needles": ["audioseal", "meta/audioseal",
                                 "stable-signature-audio"]},
    {"label": "meta_movie_gen", "kind": "video",
     "robust": True, "needles": ["movie gen", "movie-gen", "metamoviegen",
                                 "meta-movie-gen"]},

    # -- Chinese video generators ------------------------------------------
    {"label": "kling_video",   "kind": "video",
     "robust": True, "needles": ["kling", "kuaishou-ai", "ks-ai", "kling-ai",
                                 "kolors-video"]},
    {"label": "hailuo_video",  "kind": "video",
     "robust": True, "needles": ["hailuo", "minimax video", "minimax-video"]},
    {"label": "wan_video",     "kind": "video",
     "robust": True, "needles": ["wan 2", "wan-2", "wan-video", "alibaba-wan"]},

    # -- Western video / image generators (mostly C2PA, less-robust marks) --
    {"label": "runway_video",  "kind": "video",
     "robust": True, "needles": ["runway", "runwayml", "gen-3", "gen-4",
                                 "runway-gen"]},
    {"label": "pika_video",    "kind": "video",
     "robust": False, "needles": ["pika labs", "pikalabs", "pika-video"]},
    {"label": "luma_video",    "kind": "video",
     "robust": False, "needles": ["luma ai", "lumalabs", "dream-machine"]},
    {"label": "midjourney",    "kind": "image",
     "robust": False, "needles": ["midjourney", "mj_version", "mj-version"]},
    {"label": "stable_diff",   "kind": "image",
     "robust": False, "needles": ["stable diffusion", "stablediffusion",
                                  "stable-diffusion", "comfyui", "automatic1111",
                                  "sdxl", "flux.1", "flux.1-dev", "black-forest-labs"]},
    {"label": "adobe_firefly", "kind": "image",
     "robust": False, "needles": ["firefly", "adobe stock", "adobe firefly"]},

    # -- Audio -------------------------------------------------------------
    {"label": "suno_audio",  "kind": "audio",
     "robust": True, "needles": ["suno.ai", "suno-ai", "suno v", "chirp"]},
    {"label": "udio_audio",  "kind": "audio",
     "robust": True, "needles": ["udio.com", "udio-ai", "udio v"]},

    # -- Generic C2PA claim-generator hints --------------------------------
    {"label": "c2pa_any", "kind": "any",
     "robust": False, "needles": ["c2pa", "jumbf", "contentcredentials",
                                  "content_credentials", "cai:",
                                  "x-contentcredentials", "claim_generator"]},
]


# Robustly matches any version-suffixed generator, e.g. "Imagen 3", "Veo 2".
PATTERNS_RE: List[dict] = [
    {"label": "google_synthid_image", "kind": "image", "robust": True,
     "regex": r"\b(imagen|gemini)[\s_-]?v?\d"},
    {"label": "google_synthid_video", "kind": "video", "robust": True,
     "regex": r"\bveo[\s_-]?v?\d"},
]


@dataclass
class OriginReport:
    file: str
    kind: str                       # "image" | "video" | "audio" | "unknown"
    likely_robust_watermark: bool   # True -> escalate strength
    matches: List[str] = field(default_factory=list)
    reasons: List[str] = field(default_factory=list)
    raw_metadata_bytes: int = 0     # total metadata bytes inspected

    def to_dict(self) -> dict:
        return {
            "file": self.file,
            "kind": self.kind,
            "likely_robust_watermark": self.likely_robust_watermark,
            "matches": list(self.matches),
            "reasons": list(self.reasons),
            "raw_metadata_bytes": self.raw_metadata_bytes,
        }


IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff", ".heic", ".heif", ".avif"}
VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".mkv", ".webm", ".avi", ".mpeg", ".mpg", ".ogv"}
AUDIO_EXTS = {".mp3", ".m4a", ".aac", ".wav", ".flac", ".ogg", ".oga", ".opus", ".wma"}


def _ext(path: str) -> str:
    return os.path.splitext(path)[1].lower()


def _file_kind(path: str) -> str:
    ext = _ext(path)
    if ext in IMAGE_EXTS:
        return "image"
    if ext in VIDEO_EXTS:
        return "video"
    if ext in AUDIO_EXTS:
        return "audio"
    return "unknown"


def _image_metadata_text(path: str) -> str:
    """Concatenate every piece of text-ish metadata we can pull from an image."""
    chunks: List[str] = []

    try:
        from PIL import Image, ExifTags  # noqa
    except Exception:
        return ""

    try:
        im = Image.open(path)
    except Exception:
        return ""

    # Pillow .info (PNG text, JFIF, ICC, XMP…)
    try:
        for k, v in (im.info or {}).items():
            if isinstance(v, (bytes, bytearray)):
                try:
                    chunks.append(v.decode("utf-8", errors="ignore"))
                except Exception:
                    pass
            else:
                chunks.append(str(v))
    except Exception:
        pass

    # EXIF
    try:
        ex = im.getexif()
        if ex:
            for k, v in dict(ex).items():
                chunks.append(f"{ExifTags.TAGS.get(k, str(k))}={v}")
            try:
                ifd = ex.get_ifd(0x8769)  # Exif IFD
                for k, v in (ifd or {}).items():
                    chunks.append(f"ExifIFD.{k}={v}")
            except Exception:
                pass
    except Exception:
        pass

    # Pull XMP + C2PA/JUMBF raw bytes directly from the file and scan as text.
    # This is cheap (few MB) and catches JPEG APP11 / PNG iTXt markers that
    # Pillow sometimes exposes under the "XML:com.adobe.xmp" key or buries.
    try:
        with open(path, "rb") as f:
            raw = f.read(min(os.path.getsize(path), 4 * 1024 * 1024))  # 4 MB cap
        # Heuristic: only pull the printable-ASCII runs so we don't treat
        # random JPEG bytes as text.
        import string
        printable = set(bytes(string.printable, "ascii"))
        run = []
        runs = []
        for b in raw:
            if b in printable and b not in (0x0b, 0x0c):
                run.append(b)
            else:
                if len(run) >= 8:
                    runs.append(bytes(run).decode("ascii", errors="ignore"))
                run = []
        if len(run) >= 8:
            runs.append(bytes(run).decode("ascii", errors="ignore"))
        chunks.extend(runs)
    except Exception:
        pass

    return "\n".join(chunks)


def _media_metadata_text(path: str) -> str:
    """ffprobe-based metadata text for video/audio."""
    try:
        proc = subprocess.run(
            [FFPROBE, "-v", "error",
             "-show_format", "-show_streams", "-show_chapters",
             "-print_format", "json", path],
            capture_output=True, text=True, timeout=30,
        )
    except Exception:
        return ""

    if proc.returncode != 0:
        return ""

    try:
        d = json.loads(proc.stdout or "{}")
    except Exception:
        return ""

    chunks: List[str] = []
    fmt = d.get("format") or {}
    chunks.append(json.dumps(fmt.get("tags") or {}))
    for s in d.get("streams") or []:
        chunks.append(json.dumps(s.get("tags") or {}))
        # Pull disposition/metadata-adjacent string keys.
        for k in ("codec_name", "codec_long_name", "profile", "color_primaries",
                  "color_space", "color_transfer"):
            if s.get(k):
                chunks.append(f"{k}={s.get(k)}")
    for ch in d.get("chapters") or []:
        chunks.append(json.dumps(ch.get("tags") or {}))

    # Also grep the first megabyte of the file for claim_generator / jumbf /
    # udta boxes that ffprobe doesn't surface.
    try:
        with open(path, "rb") as f:
            raw = f.read(min(os.path.getsize(path), 2 * 1024 * 1024))
        import string
        printable = set(bytes(string.printable, "ascii"))
        run = []
        for b in raw:
            if b in printable and b not in (0x0b, 0x0c):
                run.append(b)
            else:
                if len(run) >= 8:
                    chunks.append(bytes(run).decode("ascii", errors="ignore"))
                run = []
        if len(run) >= 8:
            chunks.append(bytes(run).decode("ascii", errors="ignore"))
    except Exception:
        pass

    return "\n".join(chunks)


def detect_origin(path: str) -> OriginReport:
    """Inspect the file offline and decide if it likely carries a robust watermark."""
    kind = _file_kind(path)
    if kind == "image":
        text = _image_metadata_text(path)
    elif kind in ("video", "audio"):
        text = _media_metadata_text(path)
    else:
        return OriginReport(file=path, kind="unknown",
                            likely_robust_watermark=False)

    haystack = text.lower()
    matches: List[str] = []
    reasons: List[str] = []
    robust = False

    for entry in PATTERNS:
        if entry["kind"] not in ("any", kind):
            continue
        for needle in entry["needles"]:
            if needle.lower() in haystack:
                matches.append(entry["label"])
                reasons.append(f"{entry['label']}: matched \"{needle}\"")
                if entry["robust"]:
                    robust = True
                break  # count this label once per file

    for entry in PATTERNS_RE:
        if entry["kind"] not in ("any", kind):
            continue
        if re.search(entry["regex"], haystack, re.IGNORECASE):
            if entry["label"] not in matches:
                matches.append(entry["label"])
                reasons.append(f"{entry['label']}: matched /{entry['regex']}/")
            if entry["robust"]:
                robust = True

    return OriginReport(
        file=path,
        kind=kind,
        likely_robust_watermark=robust,
        matches=list(dict.fromkeys(matches)),
        reasons=reasons,
        raw_metadata_bytes=len(text.encode("utf-8", errors="ignore")),
    )


__all__ = ["OriginReport", "detect_origin"]
