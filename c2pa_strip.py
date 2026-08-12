"""
Deep C2PA / Content Credentials / JUMBF stripper.

Pillow re-save already drops most EXIF/XMP, but signed C2PA manifests can
live in format-specific boxes (JPEG APP segments, PNG `caBX`, ISOBMFF uuid
boxes). Paid tools advertise clearing these in one pass — this module does
the same offline.

Conservative: only remove segments/chunks/boxes that look like C2PA/JUMBF/
Content Credentials. Does not touch pixel data.
"""

from __future__ import annotations

import os
import re
import struct
from typing import Tuple


_C2PA_NEEDLES = (
    b"c2pa",
    b"C2PA",
    b"jumb",
    b"JUMB",
    b"c2ma",
    b"c2cs",
    b"c2cl",
    b"c2as",
    b"c2cm",
    b"c2nd",
    b"cacr",
    b"caBX",
    b"contentcredentials",
    b"ContentCredentials",
    b"http://c2pa.org",
    b"https://c2pa.org",
)


def _looks_c2pa(blob: bytes) -> bool:
    low = blob[: min(len(blob), 8192)]
    return any(n in low for n in _C2PA_NEEDLES)


def strip_jpeg_c2pa(data: bytes) -> Tuple[bytes, int]:
    """Remove JPEG APP / COM segments that carry C2PA/XMP-C2PA. Returns (data, n_removed)."""
    if len(data) < 4 or data[0:2] != b"\xff\xd8":
        return data, 0

    out = bytearray(data[0:2])
    i = 2
    removed = 0
    n = len(data)
    while i < n:
        if data[i] != 0xFF:
            # Entropy-coded scan — copy the rest.
            out.extend(data[i:])
            break
        # Skip fill bytes.
        while i < n and data[i] == 0xFF:
            i += 1
        if i >= n:
            break
        marker = data[i]
        i += 1
        # Standalone markers with no length.
        if marker in (0xD8, 0xD9) or 0xD0 <= marker <= 0xD7:
            out.append(0xFF)
            out.append(marker)
            if marker == 0xD9:
                out.extend(data[i:])
                break
            continue
        if i + 2 > n:
            break
        seglen = struct.unpack(">H", data[i : i + 2])[0]
        if seglen < 2 or i + seglen > n:
            # Corrupt — bail and keep remainder.
            out.append(0xFF)
            out.append(marker)
            out.extend(data[i:])
            break
        keep = True
        # APP0–APP15 (E0–EF) and COM (FE): drop if C2PA / JUMBF / Content Credentials.
        if marker in range(0xE0, 0xF0) or marker == 0xFE:
            chunk = data[i : i + seglen]
            if _looks_c2pa(chunk):
                keep = False
        if keep:
            out.append(0xFF)
            out.append(marker)
            out.extend(data[i : i + seglen])
        else:
            removed += 1
        i += seglen

        # After SOS (0xDA), remainder is scan data.
        if marker == 0xDA:
            out.extend(data[i:])
            break

    return bytes(out), removed


def strip_png_c2pa(data: bytes) -> Tuple[bytes, int]:
    """Drop PNG chunks that look like C2PA (caBX, and text with needles)."""
    if len(data) < 8 or data[:8] != b"\x89PNG\r\n\x1a\n":
        return data, 0
    out = bytearray(data[:8])
    i = 8
    removed = 0
    n = len(data)
    while i + 8 <= n:
        length = struct.unpack(">I", data[i : i + 4])[0]
        ctype = data[i + 4 : i + 8]
        chunk_end = i + 12 + length
        if chunk_end > n:
            out.extend(data[i:])
            break
        chunk = data[i:chunk_end]
        payload = data[i + 8 : i + 8 + length]
        drop = False
        if ctype in (b"caBX", b"c2pa"):
            drop = True
        elif ctype in (b"iTXt", b"tEXt", b"zTXt", b"eXIf") and _looks_c2pa(payload):
            drop = True
        if drop:
            removed += 1
        else:
            out.extend(chunk)
        i = chunk_end
        if ctype == b"IEND":
            break
    return bytes(out), removed


def strip_isobmff_c2pa(data: bytes) -> Tuple[bytes, int]:
    """
    Best-effort: remove top-level uuid / meta boxes that look like C2PA in
    MP4/MOV/HEIC. Nested boxes inside moov are left alone if rewriting would
    break offsets — for those, prefer ffmpeg -map_metadata -1 re-encode.
    """
    if len(data) < 12:
        return data, 0
    # Quick brand check.
    if data[4:8] not in (b"ftyp", b"wide", b"mdat", b"moov", b"free", b"uuid", b"meta"):
        # Still try — some files start with other boxes.
        pass

    out = bytearray()
    i = 0
    n = len(data)
    removed = 0
    while i + 8 <= n:
        size = struct.unpack(">I", data[i : i + 4])[0]
        btype = data[i + 4 : i + 8]
        if size == 1 and i + 16 <= n:
            size = struct.unpack(">Q", data[i + 8 : i + 16])[0]
            header = 16
        elif size == 0:
            size = n - i
            header = 8
        else:
            header = 8
        if size < header or i + size > n:
            out.extend(data[i:])
            break
        box = data[i : i + size]
        drop = False
        if btype in (b"uuid", b"meta", b"xml ", b"udta"):
            if _looks_c2pa(box[: min(len(box), 16384)]):
                drop = True
        if drop:
            removed += 1
        else:
            out.extend(box)
        i += size
    if removed == 0:
        return data, 0
    return bytes(out), removed


def deep_strip_c2pa_file(path: str) -> Tuple[str, int]:
    """
    In-place rewrite of `path` with C2PA-ish containers removed.
    Returns (detail, n_removed).
    """
    with open(path, "rb") as f:
        data = f.read()
    ext = os.path.splitext(path)[1].lower()
    removed = 0
    new = data
    if ext in {".jpg", ".jpeg"} or data[:2] == b"\xff\xd8":
        new, removed = strip_jpeg_c2pa(data)
    elif ext == ".png" or data[:8] == b"\x89PNG\r\n\x1a\n":
        new, removed = strip_png_c2pa(data)
    elif ext in {".mp4", ".mov", ".m4v", ".heic", ".heif", ".avif"}:
        new, removed = strip_isobmff_c2pa(data)
    else:
        # Generic byte scrub of XMP packets with c2pa — only if clearly packetized.
        if _looks_c2pa(data):
            # Remove xpacket blocks that mention c2pa.
            pattern = re.compile(
                br"<\?xpacket begin=.*?<\?xpacket end=[^?]*\?>",
                re.DOTALL,
            )
            def _filter(m: re.Match) -> bytes:
                blk = m.group(0)
                return b"" if _looks_c2pa(blk) else blk
            scrubbed, n = pattern.subn(_filter, data)
            if n:
                new, removed = scrubbed, n

    if removed and new != data:
        with open(path, "wb") as f:
            f.write(new)
    return (f"c2pa_segments_removed={removed}", removed)
