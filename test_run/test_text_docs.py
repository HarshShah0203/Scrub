"""Tests for Unicode hygiene + document routing (stdlib-friendly)."""

from __future__ import annotations

import os
import sys
import tempfile
import zipfile
from xml.etree import ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from document_strip import clean_document, inspect_document
from text_hygiene import clean_text, inspect_text, looks_binary


def test_unicode_hidden_and_spaces():
    raw = "Hello\u200b world\u00a0there\u202e"
    stats = inspect_text(raw)
    assert stats["hidden_count"] >= 2
    assert stats["space_homoglyph_count"] == 1
    cleaned, result = clean_text(raw)
    assert "\u200b" not in cleaned
    assert "\u202e" not in cleaned
    assert "\u00a0" not in cleaned
    assert result["changed"] is True
    assert inspect_text(cleaned)["hidden_count"] == 0


def test_zwj_kept_unless_aggressive():
    emoji = "A\u200dB"
    kept, _ = clean_text(emoji)
    assert "\u200d" in kept
    gone, _ = clean_text(emoji, aggressive=True)
    assert "\u200d" not in gone


def test_looks_binary_zip_and_pdf():
    assert looks_binary(b"PK\x03\x04" + b"x" * 20)
    assert looks_binary(b"%PDF-1.7\n")
    assert not looks_binary("plain text\n".encode())


def test_markdown_roundtrip():
    with tempfile.TemporaryDirectory() as td:
        src = os.path.join(td, "note.md")
        with open(src, "w", encoding="utf-8") as f:
            f.write("---\ngenerator: SomeModel\n---\nHi\u200b there")
        out, detail = clean_document(src, td)
        text = open(out, encoding="utf-8").read()
        assert "\u200b" not in text
        assert "generator:" not in text.lower()
        assert "Hi" in text
        assert "unicode" in detail.lower() or "hygiene" in detail.lower()


def test_html_meta_and_unicode():
    with tempfile.TemporaryDirectory() as td:
        src = os.path.join(td, "p.html")
        with open(src, "w", encoding="utf-8") as f:
            f.write(
                "<html><head>"
                "<meta name=\"generator\" content=\"LLM\">"
                "</head><body>Hi\u200b</body></html>"
            )
        out, _ = clean_document(src, td)
        text = open(out, encoding="utf-8").read()
        assert "generator" not in text.lower()
        assert "\u200b" not in text
        assert "Hi" in text


def test_svg_metadata_node():
    with tempfile.TemporaryDirectory() as td:
        src = os.path.join(td, "i.svg")
        src_xml = (
            "<svg xmlns='http://www.w3.org/2000/svg'>"
            "<metadata><rdf:RDF xmlns:rdf='http://www.w3.org/1999/02/22-rdf-syntax-ns#'>"
            "<rdf:Description>secret</rdf:Description></rdf:RDF></metadata>"
            "<text>ok</text></svg>"
        )
        with open(src, "w", encoding="utf-8") as f:
            f.write(src_xml)
        out, _ = clean_document(src, td)
        tree = ET.parse(out)
        tags = {_xml_local(el.tag).lower() for el in tree.getroot().iter()}
        assert "metadata" not in tags
        assert "rdf" not in tags


def _xml_local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def test_docx_core_props():
    with tempfile.TemporaryDirectory() as td:
        src = os.path.join(td, "a.docx")
        core = (
            "<?xml version='1.0' encoding='UTF-8'?>"
            "<cp:coreProperties xmlns:cp='http://schemas.openxmlformats.org/package/2006/metadata/core-properties'"
            " xmlns:dc='http://purl.org/dc/elements/1.1/'>"
            "<dc:creator>SecretBot</dc:creator>"
            "<cp:lastModifiedBy>SecretBot</cp:lastModifiedBy>"
            "</cp:coreProperties>"
        )
        body = (
            "<?xml version='1.0' encoding='UTF-8'?>"
            "<w:document xmlns:w='http://schemas.openxmlformats.org/wordprocessingml/2006/main'>"
            "<w:t>Hello\u200b</w:t></w:document>"
        )
        with zipfile.ZipFile(src, "w") as z:
            z.writestr("docProps/core.xml", core)
            z.writestr("word/document.xml", body)
            z.writestr("[Content_Types].xml", "<Types></Types>")
        out, _ = clean_document(src, td)
        with zipfile.ZipFile(out) as z:
            core_out = z.read("docProps/core.xml").decode("utf-8")
            body_out = z.read("word/document.xml").decode("utf-8")
        assert "SecretBot" not in core_out
        assert "\u200b" not in body_out
        assert "Hello" in body_out


def test_inspect_markdown():
    with tempfile.TemporaryDirectory() as td:
        src = os.path.join(td, "n.md")
        open(src, "w", encoding="utf-8").write("x\u200by")
        r = inspect_document(src)
        assert r["unicode"]["hidden_count"] >= 1


if __name__ == "__main__":
    tests = [v for k, v in list(globals().items()) if k.startswith("test_")]
    failed = 0
    for fn in tests:
        try:
            fn()
            print(f"ok  {fn.__name__}")
        except Exception as e:
            failed += 1
            print(f"FAIL {fn.__name__}: {type(e).__name__}: {e}")
    if failed:
        raise SystemExit(1)
    print(f"{len(tests)} passed")
