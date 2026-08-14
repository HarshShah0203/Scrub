"""Best-effort provenance metadata strip for documents you own.

Formats: Markdown/text, HTML, SVG, DOCX, ODT, PDF (pypdf or exiftool if present).

Pixel data / document body is preserved. Hidden Unicode in text parts is
normalized via text_hygiene. Stdlib-first; PDF quality improves with optional
`pypdf` or system `exiftool`/`qpdf`.
"""

from __future__ import annotations

import io
import os
import re
import shutil
import subprocess
import tempfile
import zipfile
from typing import Dict, List, Optional, Tuple
from xml.etree import ElementTree as ET

from text_hygiene import clean_text, inspect_text, looks_binary, read_text_file

DOC_EXTS = {
    ".txt", ".md", ".markdown", ".html", ".htm", ".svg",
    ".docx", ".odt", ".pdf",
}

_NS_STRIP_TAGS = {
    "metadata", "rdf", "RDF", "xmpmeta", "xmp",
}


def _ext(path: str) -> str:
    return os.path.splitext(path)[1].lower()


def _unique(out_path: str) -> str:
    if not os.path.exists(out_path):
        return out_path
    base, ext = os.path.splitext(out_path)
    i = 1
    while True:
        cand = f"{base}_{i}{ext}"
        if not os.path.exists(cand):
            return cand
        i += 1


def _out_path(input_path: str, output_dir: str) -> str:
    os.makedirs(output_dir, exist_ok=True)
    base, ext = os.path.splitext(os.path.basename(input_path))
    return _unique(os.path.join(output_dir, f"{base}_clean{ext}"))


def _xml_local(tag: str) -> str:
    if "}" in tag:
        return tag.rsplit("}", 1)[-1]
    return tag


def _clean_xml_text_nodes(root: ET.Element, aggressive: bool = False) -> int:
    changed = 0
    for el in root.iter():
        if el.text:
            new, stats = clean_text(el.text, aggressive=aggressive)
            if stats["changed"]:
                el.text = new
                changed += 1
        if el.tail:
            new, stats = clean_text(el.tail, aggressive=aggressive)
            if stats["changed"]:
                el.tail = new
                changed += 1
    return changed


def _is_metaish_tag(tag: str) -> bool:
    loc = _xml_local(tag).lower()
    return loc in {t.lower() for t in _NS_STRIP_TAGS} or loc in {
        "creator", "contributor", "publisher", "description", "keywords",
        "producer", "author", "lastmodifiedby", "revision",
    }


def inspect_document(path: str) -> Dict:
    ext = _ext(path)
    st = os.stat(path)
    report: Dict = {
        "path": path,
        "filename": os.path.basename(path),
        "extension": ext,
        "size_bytes": st.st_size,
        "kind": "document",
    }
    if ext in {".txt", ".md", ".markdown"}:
        text = read_text_file(path)
        report["unicode"] = inspect_text(text)
        return report
    if ext in {".html", ".htm", ".svg"}:
        text = read_text_file(path)
        report["unicode"] = inspect_text(text)
        report["meta_hints"] = _html_meta_hints(text)
        return report
    if ext in {".docx", ".odt"}:
        report["zip_entries"] = _zip_names(path)
        report["unicode"] = _inspect_zip_xml_text(path)
        return report
    if ext == ".pdf":
        report["pdf"] = _inspect_pdf(path)
        return report
    raise ValueError(f"Unsupported document type: {ext}")


def clean_document(
    input_path: str,
    output_dir: str,
    *,
    aggressive: bool = False,
) -> Tuple[str, str]:
    ext = _ext(input_path)
    if ext in {".txt", ".md", ".markdown"}:
        return _clean_plaintext(input_path, output_dir, aggressive=aggressive)
    if ext in {".html", ".htm"}:
        return _clean_html(input_path, output_dir, aggressive=aggressive)
    if ext == ".svg":
        return _clean_svg(input_path, output_dir, aggressive=aggressive)
    if ext in {".docx", ".odt"}:
        return _clean_office(input_path, output_dir, aggressive=aggressive)
    if ext == ".pdf":
        return _clean_pdf(input_path, output_dir)
    raise ValueError(f"Unsupported document type: {ext}")


def _clean_plaintext(path: str, output_dir: str, *, aggressive: bool) -> Tuple[str, str]:
    text = read_text_file(path)
    cleaned, stats = clean_text(text, aggressive=aggressive)
    cleaned = _strip_md_frontmatter_keys(cleaned)
    out = _out_path(path, output_dir)
    with open(out, "w", encoding="utf-8", newline="") as f:
        f.write(cleaned)
    n = stats["before"]["hidden_count"] + stats["before"]["space_homoglyph_count"]
    return out, f"unicode hygiene ({n} hidden/space marks); markdown frontmatter keys scrubbed"


_FRONTMATTER_DROP = re.compile(
    r"^(c2pa|content.?credentials|generator|ai.?generated)\s*:.*$",
    re.I | re.M,
)


def _strip_md_frontmatter_keys(text: str) -> str:
    if not text.startswith("---"):
        return text
    parts = text.split("---", 2)
    if len(parts) < 3:
        return text
    fm = _FRONTMATTER_DROP.sub("", parts[1])
    return "---" + fm + "---" + parts[2]


def _html_meta_hints(text: str) -> List[str]:
    hints = []
    for m in re.finditer(
        r"<meta\b[^>]*(?:name|property)\s*=\s*[\"']([^\"']+)[\"'][^>]*>",
        text,
        re.I,
    ):
        hints.append(m.group(1))
    return hints[:50]


_META_DROP = re.compile(
    r"<meta\b[^>]*(?:name|property)\s*=\s*[\"']("
    r"generator|author|creator|producer|c2pa|content.?credentials|"
    r"ai-generated|dcterms\.(?:creator|publisher)"
    r")[\"'][^>]*>",
    re.I,
)
_COMMENT_C2PA = re.compile(r"<!--.*?c2pa.*?-->", re.I | re.S)


def _clean_html(path: str, output_dir: str, *, aggressive: bool) -> Tuple[str, str]:
    text = read_text_file(path)
    n_meta = len(_META_DROP.findall(text))
    text = _META_DROP.sub("", text)
    text = _COMMENT_C2PA.sub("", text)
    cleaned, stats = clean_text(text, aggressive=aggressive)
    out = _out_path(path, output_dir)
    with open(out, "w", encoding="utf-8", newline="") as f:
        f.write(cleaned)
    n = stats["before"]["hidden_count"]
    return out, f"html meta/comments stripped ({n_meta}); unicode hygiene ({n} hidden)"


def _clean_svg(path: str, output_dir: str, *, aggressive: bool) -> Tuple[str, str]:
    tree = ET.parse(path)
    root = tree.getroot()
    removed = 0
    for parent in list(root.iter()):
        drop = []
        for child in list(parent):
            loc = _xml_local(child.tag).lower()
            if loc in {"metadata", "rdf", "xmpmeta"} or "c2pa" in loc:
                drop.append(child)
        for child in drop:
            parent.remove(child)
            removed += 1
    n_text = _clean_xml_text_nodes(root, aggressive=aggressive)
    out = _out_path(path, output_dir)
    tree.write(out, encoding="utf-8", xml_declaration=True)
    return out, f"svg metadata nodes removed ({removed}); text nodes cleaned ({n_text})"


def _zip_names(path: str) -> List[str]:
    with zipfile.ZipFile(path, "r") as z:
        return z.namelist()


def _inspect_zip_xml_text(path: str) -> Dict:
    hidden = 0
    spaces = 0
    with zipfile.ZipFile(path, "r") as z:
        for name in z.namelist():
            if not name.lower().endswith(".xml"):
                continue
            raw = z.read(name)
            try:
                text = raw.decode("utf-8")
            except Exception:
                continue
            r = inspect_text(text)
            hidden += r["hidden_count"]
            spaces += r["space_homoglyph_count"]
    return {"hidden_count": hidden, "space_homoglyph_count": spaces}


_DOC_PROP_FILES = {
    "docProps/core.xml",
    "docProps/app.xml",
    "docProps/custom.xml",
    "meta.xml",
}


def _scrub_office_xml(name: str, data: bytes, *, aggressive: bool) -> bytes:
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        text = data.decode("utf-8", errors="replace")
        cleaned, _ = clean_text(text, aggressive=aggressive)
        return cleaned.encode("utf-8")

    # Empty obvious identity fields in Dublin Core / cp / dc.
    if name.replace("\\", "/") in _DOC_PROP_FILES or name.endswith("meta.xml"):
        for el in root.iter():
            loc = _xml_local(el.tag).lower()
            if loc in {
                "creator", "lastmodifiedby", "company", "manager",
                "description", "subject", "keywords", "producer",
                "initial-creator", "printed-by", "template",
                "editing-cycles", "editing-duration",
            }:
                el.text = ""
                for child in list(el):
                    el.remove(child)

    _clean_xml_text_nodes(root, aggressive=aggressive)
    buf = io.BytesIO()
    ET.ElementTree(root).write(buf, encoding="utf-8", xml_declaration=True)
    return buf.getvalue()


def _clean_office(path: str, output_dir: str, *, aggressive: bool) -> Tuple[str, str]:
    out = _out_path(path, output_dir)
    touched = 0
    with zipfile.ZipFile(path, "r") as zin, zipfile.ZipFile(
        out, "w", compression=zipfile.ZIP_DEFLATED
    ) as zout:
        for info in zin.infolist():
            data = zin.read(info.filename)
            name = info.filename.replace("\\", "/")
            if name.lower().endswith(".xml"):
                new = _scrub_office_xml(name, data, aggressive=aggressive)
                if new != data:
                    touched += 1
                data = new
            # Drop custom props that often hold generator / C2PA sidecars.
            if name == "docProps/custom.xml":
                touched += 1
                continue
            zout.writestr(info, data)
    return out, f"office container rewritten; {touched} xml parts scrubbed"


def _inspect_pdf(path: str) -> Dict:
    info: Dict = {"tool": None}
    try:
        import pypdf  # type: ignore

        r = pypdf.PdfReader(path)
        meta = dict(r.metadata or {})
        info["tool"] = "pypdf"
        info["metadata_keys"] = [str(k) for k in meta.keys()]
        return info
    except Exception as e:
        info["pypdf"] = str(e)
    if shutil.which("exiftool"):
        proc = subprocess.run(
            ["exiftool", "-json", path], capture_output=True, text=True
        )
        info["tool"] = "exiftool"
        info["exiftool_ok"] = proc.returncode == 0
        return info
    with open(path, "rb") as f:
        data = f.read(65536)
    info["tool"] = "bytes"
    info["has_xmp"] = b"<?xpacket" in data or b"http://ns.adobe.com/xap" in data
    info["has_c2pa_needle"] = b"c2pa" in data.lower() or b"jumb" in data.lower()
    return info


def _clean_pdf(path: str, output_dir: str) -> Tuple[str, str]:
    out = _out_path(path, output_dir)
    err = "pypdf missing"
    try:
        import pypdf  # type: ignore

        reader = pypdf.PdfReader(path)
        writer = pypdf.PdfWriter()
        writer.append(reader)
        try:
            writer.metadata = None  # type: ignore[assignment]
        except Exception:
            try:
                writer.add_metadata({})
            except Exception:
                pass
        with open(out, "wb") as f:
            writer.write(f)
        extra = ""
        if shutil.which("exiftool"):
            subprocess.run(
                ["exiftool", "-overwrite_original", "-all:all=", out],
                capture_output=True, text=True,
            )
            extra = "; exiftool -all:all="
        return out, f"pdf metadata cleared via pypdf{extra} (body pages preserved)"
    except ImportError:
        err = "pypdf missing"
    except Exception as e:
        err = str(e)

    if shutil.which("exiftool"):
        shutil.copy2(path, out)
        proc = subprocess.run(
            ["exiftool", "-overwrite_original", "-all:all=", out],
            capture_output=True, text=True,
        )
        if proc.returncode == 0:
            return out, "pdf metadata cleared via exiftool (best-effort; XMP/C2PA may remain if compressed)"
        err = proc.stderr.strip() or err

    # Last resort: copy + strip uncompressed XMP packets only.
    with open(path, "rb") as f:
        data = f.read()
    new, n = re.subn(br"<\?xpacket begin=.*?<\?xpacket end=[^?]*\?>", b"", data, flags=re.S)
    with open(out, "wb") as f:
        f.write(new)
    return out, (
        f"pdf byte-level XMP packet strip ({n} packets); "
        f"install pypdf or exiftool for a fuller Info-dict wipe ({err})"
    )
