"""
Before/after provenance audit for Scrub cleans.

Paid tools show a scan → clean → verify loop. This writes a small JSON
sidecar summarizing removable provenance signals on the input vs output
so users can confirm C2PA / generator tags are gone (offline, no network).
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

from origin_detect import OriginReport, detect_origin
from stripper import inspect_file, summarize_inspection


@dataclass
class AuditSnapshot:
    path: str
    origin_matches: List[str] = field(default_factory=list)
    likely_robust_watermark: bool = False
    reasons: List[str] = field(default_factory=list)
    removable_props: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None


def snapshot(path: str) -> AuditSnapshot:
    try:
        origin: OriginReport = detect_origin(path)
        props: Dict[str, Any] = {}
        try:
            props = summarize_inspection(inspect_file(path))
        except Exception as e:
            props = {"audit_error": str(e)}
        return AuditSnapshot(
            path=path,
            origin_matches=list(origin.matches or []),
            likely_robust_watermark=bool(origin.likely_robust_watermark),
            reasons=list(origin.reasons or []),
            removable_props=props if isinstance(props, dict) else {"raw": props},
        )
    except Exception as e:
        return AuditSnapshot(path=path, error=str(e))


def write_clean_audit(
    input_path: str,
    output_path: str,
    detail: str,
    extra: Optional[Dict[str, Any]] = None,
) -> str:
    """
    Write `<output>_audit.json` next to the cleaned file.
    Returns the audit JSON path.
    """
    before = snapshot(input_path)
    after = snapshot(output_path)
    payload = {
        "input": asdict(before),
        "output": asdict(after),
        "detail": detail,
        "c2pa_or_generator_tags_cleared": (
            bool(before.origin_matches) and not after.origin_matches
        ),
        "extra": extra or {},
    }
    base, _ = os.path.splitext(output_path)
    audit_path = base + "_audit.json"
    with open(audit_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, default=str)
    return audit_path
