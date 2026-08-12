"""Gradio web UI (alternative to the native Tk app).

Visually restrained, single-column layout with a soft-card aesthetic. Surfaces
the same Auto-strength behaviour as the Tk app so users get a consistent
experience regardless of frontend.
"""

from __future__ import annotations

import os
import json
from typing import List, Optional, Tuple

import gradio as gr

from stripper import strip_file_metadata
from watermark_remover import clean_file_v2


CUSTOM_CSS = """
:root {
  --brand: #2e7d32;
  --brand-dark: #276326;
  --brand-sub: #e8f1e9;
  --border: #e3e5e8;
  --muted: #6b7280;
}
.gradio-container {
  max-width: 960px !important;
  margin: 0 auto !important;
  font-family: -apple-system, BlinkMacSystemFont, "SF Pro Text", sans-serif;
}
#app-header { padding: 4px 0 16px; }
#app-header h1 {
  font-size: 26px !important; font-weight: 700 !important;
  margin: 0 0 6px !important;
}
#app-header p { color: var(--muted); margin: 0 !important; }
.card {
  background: #fff; border: 1px solid var(--border); border-radius: 14px;
  padding: 18px 18px 12px !important;
}
.card > .gap { gap: 10px !important; }
button.primary {
  background: var(--brand) !important; color: white !important;
  border: none !important;
  font-weight: 600 !important; padding: 12px 22px !important;
  border-radius: 10px !important;
}
button.primary:hover { background: var(--brand-dark) !important; }
.log-area textarea { font-family: "SF Mono", ui-monospace, monospace !important; }
"""

CONFIG_PATH = os.path.join(
    os.path.expanduser("~"),
    ".metadata_watermark_cleaner_config.json",
)
FALLBACK_OUTPUT_DIR = os.path.expanduser(
    "~/Desktop/Experiments/CREATED-DATA/"
    "METADATA-WATERMARK-REMOVED-VIDEO/PHOTO"
)


def _load_default_output_dir() -> str:
    try:
        if os.path.exists(CONFIG_PATH):
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            saved = (data.get("default_output_dir") or "").strip()
            if saved:
                return saved
    except Exception:
        pass
    return FALLBACK_OUTPUT_DIR


def _save_default_output_dir(path: str) -> Tuple[bool, str]:
    ok, path_or_err = _ensure_dir(path)
    if not ok:
        return False, path_or_err
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump({"default_output_dir": path_or_err}, f, indent=2)
    except Exception as e:
        return False, f"Could not save default directory: {e}"
    return True, path_or_err


def _ensure_dir(path: str) -> Tuple[bool, str]:
    path = (path or "").strip()
    if not path:
        return False, "Please provide an output folder path."
    try:
        os.makedirs(path, exist_ok=True)
    except Exception as e:
        return False, f"Cannot create output folder: {e}"
    return True, path


def process_uploads(
    files: Optional[List[str]],
    output_dir: str,
    mode: str,
    auto: bool,
    strength: str,
    use_diffusion: bool,
    use_spectral: bool,
    remove_visible: bool,
    write_audit: bool,
):
    if not files:
        return [], "No files uploaded.", ""

    ok, out_dir_or_err = _ensure_dir(output_dir)
    if not ok:
        return [], out_dir_or_err, ""
    out_dir = out_dir_or_err

    strength_key = {
        "near-lossless": "near_lossless",
        "light": "light",
        "medium": "medium",
        "strong": "strong",
    }.get(strength.strip().lower(), "near_lossless")

    cleaned: List[str] = []
    log_lines: List[str] = []
    rows: List[List[str]] = []

    for p in files:
        name = os.path.basename(p)
        try:
            if mode == "Metadata only (fast)":
                result_path, detail = strip_file_metadata(
                    input_path=p, output_dir=out_dir, prefer_stream_copy=True,
                )
                cleaned.append(result_path)
                log_lines.append(f"OK  {name} -> {os.path.basename(result_path)}  ({detail})")
                rows.append([name, "cleaned", "metadata-only", "-"])
                continue

            report = clean_file_v2(
                input_path=p,
                output_dir=out_dir,
                strength=strength_key,
                use_diffusion=use_diffusion,
                auto_strength=bool(auto),
                use_spectral=bool(use_spectral),
                remove_visible=bool(remove_visible),
                write_audit=bool(write_audit),
            )
            cleaned.append(report.output_path)
            if report.audit_path:
                cleaned.append(report.audit_path)
            log_lines.append(
                f"OK  {name} -> {os.path.basename(report.output_path)}  ({report.detail})"
            )
            origin = "—"
            if report.origin and report.origin.matches:
                origin = report.origin.matches[0]
                if report.auto_escalated:
                    origin += "  ●"
            rows.append([name, "cleaned", origin, report.strength_used])
        except Exception as e:
            log_lines.append(f"FAIL  {name}: {type(e).__name__}: {e}")
            rows.append([name, "failed", f"{type(e).__name__}", "-"])

    summary = f"**{len(cleaned)}/{len(files)} cleaned** -> `{out_dir}`"
    return cleaned, "\n".join(log_lines), summary, rows


def main():
    initial_output_dir = _load_default_output_dir()

    with gr.Blocks(title="Metadata & Watermark Cleaner", css=CUSTOM_CSS,
                   theme=gr.themes.Soft(primary_hue="green",
                                        neutral_hue="slate")) as demo:
        gr.HTML(
            """
<div id="app-header">
  <h1>Scrub</h1>
  <p>Strip EXIF, IPTC, XMP, C2PA, visible Gemini sparkles, and invisible AI
     watermarks (SynthID, Kling, Veo, Sora, AudioSeal, …). Fully local —
     your originals are never modified.</p>
</div>
"""
        )

        with gr.Group(elem_classes="card"):
            files_in = gr.File(
                label="Drop images, videos, or audio here",
                file_count="multiple",
                file_types=["image", "video", "audio"],
            )

        with gr.Group(elem_classes="card"):
            output_dir = gr.Textbox(
                label="Output folder",
                placeholder="~/Desktop/Scrub",
                value=initial_output_dir,
            )
            set_default = gr.Button("Set as default directory")
            default_status = gr.Markdown("")

            with gr.Row():
                mode = gr.Radio(
                    ["Metadata + invisible watermarks (recommended)",
                     "Metadata only (fast)"],
                    value="Metadata + invisible watermarks (recommended)",
                    label="Mode",
                )
            auto = gr.Checkbox(
                value=True,
                label="Auto — bump to Medium for Google / SynthID-class origins (recommended)",
            )
            strength = gr.Radio(
                ["Near-lossless", "Light", "Medium", "Strong"],
                value="Near-lossless",
                label="Manual strength (used when Auto is off)",
            )
            use_spectral = gr.Checkbox(
                value=True,
                label="Spectral / frequency-domain SynthID attack (images + short video ≤90s)",
            )
            remove_visible = gr.Checkbox(
                value=True,
                label="Remove visible Gemini / Nano Banana sparkle",
            )
            write_audit = gr.Checkbox(
                value=True,
                label="Write before/after audit JSON",
            )
            use_diffusion = gr.Checkbox(
                value=False,
                label="Advanced — diffusion regeneration for images (slow, requires torch + diffusers)",
            )

        go = gr.Button("Start — strip metadata & watermarks",
                       variant="primary", elem_classes="primary")

        summary = gr.Markdown("")
        with gr.Row():
            cleaned = gr.Files(label="Cleaned outputs")
        results = gr.Dataframe(
            headers=["File", "Status", "Detected origin", "Strength used"],
            datatype=["str", "str", "str", "str"],
            col_count=(4, "fixed"),
            label="Per-file report",
            wrap=True,
            value=[],
        )
        log = gr.Textbox(label="Activity log", lines=8, interactive=False,
                         elem_classes="log-area")

        def _on_auto_change(is_auto):
            return gr.update(interactive=not bool(is_auto))

        def _on_set_default(path: str):
            ok, result = _save_default_output_dir(path)
            if ok:
                return f"Default output folder saved: `{result}`"
            return f"Could not save default output folder: {result}"

        auto.change(_on_auto_change, inputs=[auto], outputs=[strength])
        set_default.click(
            fn=_on_set_default,
            inputs=[output_dir],
            outputs=[default_status],
        )

        go.click(
            fn=process_uploads,
            inputs=[files_in, output_dir, mode, auto, strength,
                    use_diffusion, use_spectral, remove_visible, write_audit],
            outputs=[cleaned, log, summary, results],
        )

    demo.launch(server_name="127.0.0.1", server_port=7860,
                share=False, inbrowser=True)


if __name__ == "__main__":
    main()
