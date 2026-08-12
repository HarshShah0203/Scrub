"""
Scrub — strip metadata & invisible watermarks from images/video/audio.

Modern Tk UI built on customtkinter. Auto-adapts to macOS light/dark mode,
uses rounded cards, a soft green primary accent, and keeps the window
layout simple: drop files on the left, configure on the right, hit Start.
"""

from __future__ import annotations

import os
import json
import queue
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from typing import Dict, List, Optional

import customtkinter as ctk

from stripper import strip_file_metadata
from watermark_remover import (
    IMAGE_EXTS,
    VIDEO_EXTS,
    AUDIO_EXTS,
    clean_file_v2,
)
from origin_detect import detect_origin


APP_NAME = "Scrub"
SUPPORTED_EXTS = sorted(IMAGE_EXTS | VIDEO_EXTS | AUDIO_EXTS)
DEFAULT_OUT = os.path.expanduser("~/Desktop/Scrub")
CONFIG_PATH = os.path.join(
    os.path.expanduser("~"),
    ".scrub_config.json",
)


# ---------------------------------------------------------------------------
# Theme — tuned to look at home in macOS light + dark mode.
# ---------------------------------------------------------------------------

ACCENT       = "#2e7d32"
ACCENT_HOVER = "#276326"
ACCENT_SUB   = ("#e8f1e9", "#1f3b22")   # (light, dark)

CARD_BG      = ("#ffffff", "#1f2024")
APP_BG       = ("#f4f5f7", "#151619")
BORDER       = ("#e3e5e8", "#2a2c31")
TEXT         = ("#1f2328", "#e8eaee")
MUTED        = ("#6b7280", "#9aa1ac")

STATUS_QUEUED  = "\u00b7  queued"
STATUS_RUNNING = "\u21bb  running"
STATUS_OK      = "\u2713  cleaned"
STATUS_FAIL    = "\u2717  failed"


class App(ctk.CTk):
    def __init__(self):
        ctk.set_appearance_mode("system")      # follows macOS
        ctk.set_default_color_theme("green")
        super().__init__()

        self.title(APP_NAME)
        self.geometry("1220x740")
        self.minsize(1120, 680)
        self.configure(fg_color=APP_BG)

        self._files: List[str] = []
        self._file_rows: Dict[str, str] = {}
        self._origin_cache: Dict[str, str] = {}
        self._msg_q: "queue.Queue[tuple]" = queue.Queue()
        self._processing = False
        self._last_out_dir: Optional[str] = None

        self.output_dir_var = tk.StringVar(value=self._load_default_output_dir())
        self.strength_var = tk.StringVar(value="near_lossless")
        self.auto_var = tk.BooleanVar(value=True)
        self.mode_var = tk.StringVar(value="full")
        self.diffusion_var = tk.BooleanVar(value=False)

        self._build()
        self._refresh_strength_state()
        self._style_tree()
        self._poll_messages()
        self.bind("<<AppearanceChanged>>", lambda _e: self._style_tree())

    # ------------------------------------------------------------------ UI
    def _build(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)

        outer = ctk.CTkFrame(self, fg_color="transparent")
        outer.grid(row=0, column=0, sticky="nsew", padx=22, pady=20)
        # Left = files (flexible). Right = settings (fixed-width so nothing
        # clips at any reasonable window size).
        outer.grid_columnconfigure(0, weight=1, minsize=520)
        outer.grid_columnconfigure(1, weight=0, minsize=460)
        outer.grid_rowconfigure(1, weight=1)

        # --- Header -------------------------------------------------------
        header = ctk.CTkFrame(outer, fg_color="transparent")
        header.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 14))
        ctk.CTkLabel(
            header, text=f"{APP_NAME}",
            font=ctk.CTkFont(size=28, weight="bold"),
            text_color=TEXT, anchor="w",
        ).pack(anchor="w")
        ctk.CTkLabel(
            header,
            text=("Strip EXIF, IPTC, XMP, C2PA manifests, and invisible AI "
                  "watermarks (SynthID, Kling, Veo, Sora, AudioSeal, …). "
                  "Fully local. Originals are never modified."),
            font=ctk.CTkFont(size=13), text_color=MUTED,
            anchor="w", justify="left", wraplength=980,
        ).pack(anchor="w", pady=(4, 0))

        # --- Body: two cards ---------------------------------------------
        self._build_files_card(outer).grid(row=1, column=0, sticky="nsew",
                                           padx=(0, 10))
        self._build_settings_card(outer).grid(row=1, column=1, sticky="nsew")

        # --- Action bar ---------------------------------------------------
        action = ctk.CTkFrame(outer, fg_color="transparent")
        action.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(16, 0))

        self.run_btn = ctk.CTkButton(
            action,
            text="▶  Start — strip metadata & watermarks",
            command=self._run,
            fg_color=ACCENT, hover_color=ACCENT_HOVER,
            text_color="#ffffff",
            font=ctk.CTkFont(size=14, weight="bold"),
            height=46, corner_radius=12,
        )
        self.run_btn.pack(side="left", padx=(0, 10))

        self.reveal_btn = ctk.CTkButton(
            action, text="Show output folder",
            command=self._reveal_output,
            fg_color="transparent", hover_color=ACCENT_SUB,
            text_color=TEXT, border_width=1, border_color=BORDER,
            height=46, corner_radius=12,
            font=ctk.CTkFont(size=13),
        )
        self.reveal_btn.pack(side="left")
        self.reveal_btn.configure(state="disabled")

        ctk.CTkLabel(
            action,
            text="  Cleaned copies get a \"_clean\" suffix.",
            font=ctk.CTkFont(size=12), text_color=MUTED,
        ).pack(side="left", padx=(12, 0))

        # --- Progress bar -------------------------------------------------
        self.progress = ctk.CTkProgressBar(
            outer, height=6, corner_radius=3,
            progress_color=ACCENT,
        )
        self.progress.set(0.0)
        self.progress.grid(row=3, column=0, columnspan=2,
                           sticky="ew", pady=(14, 6))

        # --- Full-width activity log --------------------------------------
        log_card = ctk.CTkFrame(
            outer, fg_color=CARD_BG, corner_radius=12,
            border_width=1, border_color=BORDER,
        )
        log_card.grid(row=4, column=0, columnspan=2, sticky="ew",
                      pady=(8, 0))
        log_card.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(
            log_card, text="ACTIVITY",
            font=ctk.CTkFont(size=10, weight="bold"),
            text_color=MUTED,
        ).grid(row=0, column=0, sticky="w", padx=16, pady=(10, 2))
        self.log = ctk.CTkTextbox(
            log_card, height=110, corner_radius=8,
            border_width=0, fg_color="transparent",
            font=ctk.CTkFont(family="SF Mono", size=11),
            wrap="word",
        )
        self.log.grid(row=1, column=0, sticky="ew", padx=12, pady=(0, 10))
        self.log.configure(state="disabled")

    def _build_files_card(self, parent) -> ctk.CTkFrame:
        card = ctk.CTkFrame(parent, fg_color=CARD_BG, corner_radius=14,
                            border_width=1, border_color=BORDER)
        card.grid_columnconfigure(0, weight=1)
        card.grid_rowconfigure(2, weight=1)

        header = ctk.CTkFrame(card, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=18, pady=(16, 4))
        header.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(header, text="Files",
                     font=ctk.CTkFont(size=15, weight="bold"),
                     text_color=TEXT).grid(row=0, column=0, sticky="w")
        self.count_label = ctk.CTkLabel(
            header, text="0 files",
            font=ctk.CTkFont(size=12), text_color=MUTED,
        )
        self.count_label.grid(row=0, column=1, sticky="e")

        # Drop zone (clickable) ------------------------------------------
        drop = ctk.CTkFrame(
            card, fg_color=ACCENT_SUB, corner_radius=12,
            border_width=1, border_color=BORDER, height=96,
        )
        drop.grid(row=1, column=0, sticky="ew", padx=18, pady=(8, 10))
        drop.grid_propagate(False)
        drop.grid_columnconfigure(0, weight=1)
        drop_title = ctk.CTkLabel(
            drop, text="＋  Click to add images, videos, or audio",
            font=ctk.CTkFont(size=14, weight="bold"),
            text_color=ACCENT,
        )
        drop_title.grid(row=0, column=0, pady=(18, 2))
        drop_hint = ctk.CTkLabel(
            drop,
            text="Accepts " + ", ".join(
                sorted({e.lstrip(".") for e in SUPPORTED_EXTS})),
            font=ctk.CTkFont(size=10), text_color=MUTED,
            wraplength=560, justify="center",
        )
        drop_hint.grid(row=1, column=0, pady=(0, 16))
        for w in (drop, drop_title, drop_hint):
            w.bind("<Button-1>", lambda _e: self._select_files())
            try:
                w.configure(cursor="hand2")
            except Exception:
                pass

        # Results table --------------------------------------------------
        tbl_wrap = ctk.CTkFrame(card, fg_color="transparent")
        tbl_wrap.grid(row=2, column=0, sticky="nsew", padx=18, pady=(0, 10))
        tbl_wrap.grid_columnconfigure(0, weight=1)
        tbl_wrap.grid_rowconfigure(0, weight=1)

        cols = ("status", "name", "origin")
        self.tree = ttk.Treeview(
            tbl_wrap, columns=cols, show="headings",
            selectmode="extended", style="Scrub.Treeview",
        )
        self.tree.heading("status", text="Status")
        self.tree.heading("name", text="File")
        self.tree.heading("origin", text="Detected origin")
        self.tree.column("status", width=110, anchor="w", stretch=False)
        self.tree.column("name", width=320, anchor="w", stretch=True)
        self.tree.column("origin", width=220, anchor="w", stretch=False)
        self.tree.grid(row=0, column=0, sticky="nsew")
        vsb = ctk.CTkScrollbar(tbl_wrap, command=self.tree.yview)
        vsb.grid(row=0, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=vsb.set)

        # Toolbar --------------------------------------------------------
        toolbar = ctk.CTkFrame(card, fg_color="transparent")
        toolbar.grid(row=3, column=0, sticky="ew", padx=18, pady=(0, 16))
        _mk = lambda txt, cmd: ctk.CTkButton(
            toolbar, text=txt, command=cmd, height=32, corner_radius=9,
            fg_color="transparent", border_width=1, border_color=BORDER,
            text_color=TEXT, hover_color=ACCENT_SUB,
            font=ctk.CTkFont(size=12),
        )
        _mk("Add files…", self._select_files).pack(side="left")
        _mk("Remove selected", self._remove_selected).pack(side="left", padx=(6, 0))
        _mk("Clear all", self._clear_files).pack(side="left", padx=(6, 0))
        return card

    def _build_settings_card(self, parent) -> ctk.CTkScrollableFrame:
        # Scrollable so the full set of settings is always reachable even
        # when the window is short. The outer card is a CTkScrollableFrame
        # whose header-row shows a big "Settings" label; content below it
        # scrolls when needed.
        card = ctk.CTkScrollableFrame(
            parent, fg_color=CARD_BG, corner_radius=14,
            border_width=1, border_color=BORDER,
            label_text="  Settings",
            label_font=ctk.CTkFont(size=15, weight="bold"),
            label_fg_color=CARD_BG,
            label_text_color=TEXT,
            label_anchor="w",
            scrollbar_button_color=BORDER,
            scrollbar_button_hover_color=MUTED,
        )
        card.grid_columnconfigure(0, weight=1)

        PAD_X = 14   # inner padding is a bit tighter inside a scroll frame
        WRAP = 400
        row = 0

        # Output folder --------------------------------------------------
        ctk.CTkLabel(card, text="OUTPUT FOLDER",
                     font=ctk.CTkFont(size=10, weight="bold"),
                     text_color=MUTED).grid(
            row=row, column=0, sticky="w", padx=PAD_X, pady=(10, 4))
        row += 1
        out_row = ctk.CTkFrame(card, fg_color="transparent")
        out_row.grid(row=row, column=0, sticky="ew", padx=PAD_X)
        out_row.grid_columnconfigure(0, weight=1)
        self.out_entry = ctk.CTkEntry(
            out_row, textvariable=self.output_dir_var, height=34,
            corner_radius=9, border_color=BORDER,
        )
        self.out_entry.grid(row=0, column=0, sticky="ew")
        ctk.CTkButton(
            out_row, text="Choose…", command=self._choose_output_dir,
            height=34, width=82, corner_radius=9,
            fg_color="transparent", border_width=1, border_color=BORDER,
            text_color=TEXT, hover_color=ACCENT_SUB,
        ).grid(row=0, column=1, padx=(6, 0))
        row += 1
        ctk.CTkButton(
            card,
            text="Set current folder as default",
            command=self._save_default_output_dir,
            height=32,
            corner_radius=9,
            fg_color="transparent",
            border_width=1,
            border_color=BORDER,
            text_color=TEXT,
            hover_color=ACCENT_SUB,
            font=ctk.CTkFont(size=12),
        ).grid(row=row, column=0, sticky="w", padx=PAD_X, pady=(8, 0))
        row += 1

        # Mode -----------------------------------------------------------
        ctk.CTkLabel(card, text="MODE",
                     font=ctk.CTkFont(size=10, weight="bold"),
                     text_color=MUTED).grid(
            row=row, column=0, sticky="w", padx=PAD_X, pady=(16, 4))
        row += 1
        ctk.CTkRadioButton(
            card, text="Metadata + invisible watermarks (recommended)",
            variable=self.mode_var, value="full",
            command=self._refresh_strength_state,
            fg_color=ACCENT, hover_color=ACCENT_HOVER,
            text_color=TEXT, font=ctk.CTkFont(size=12),
        ).grid(row=row, column=0, sticky="w", padx=PAD_X, pady=(0, 4))
        row += 1
        ctk.CTkRadioButton(
            card, text="Metadata only (fast; pixels untouched)",
            variable=self.mode_var, value="metadata_only",
            command=self._refresh_strength_state,
            fg_color=ACCENT, hover_color=ACCENT_HOVER,
            text_color=TEXT, font=ctk.CTkFont(size=12),
        ).grid(row=row, column=0, sticky="w", padx=PAD_X)
        row += 1

        # Strength -------------------------------------------------------
        ctk.CTkLabel(card, text="STRENGTH",
                     font=ctk.CTkFont(size=10, weight="bold"),
                     text_color=MUTED).grid(
            row=row, column=0, sticky="w", padx=PAD_X, pady=(16, 4))
        row += 1
        self.auto_switch = ctk.CTkSwitch(
            card,
            text="Auto — pick the safest setting for each file",
            variable=self.auto_var, onvalue=True, offvalue=False,
            command=self._refresh_strength_state,
            progress_color=ACCENT, button_color="#ffffff",
            font=ctk.CTkFont(size=12), text_color=TEXT,
        )
        self.auto_switch.grid(row=row, column=0, sticky="w", padx=PAD_X)
        row += 1
        ctk.CTkLabel(
            card,
            text=("Near-lossless by default; bumps to Medium when the file's "
                  "metadata indicates a robustly-watermarked origin (Google "
                  "Imagen/Gemini, Veo, Kling, Sora, AudioSeal…)."),
            font=ctk.CTkFont(size=11), text_color=MUTED,
            wraplength=WRAP - 42, justify="left", anchor="w",
        ).grid(row=row, column=0, sticky="ew",
               padx=(PAD_X + 42, PAD_X), pady=(3, 0))
        row += 1

        manual = ctk.CTkFrame(card, fg_color="transparent")
        manual.grid(row=row, column=0, sticky="ew", padx=PAD_X, pady=(10, 0))
        manual.grid_columnconfigure(0, weight=1, uniform="strength")
        manual.grid_columnconfigure(1, weight=1, uniform="strength")
        self.manual_radios = []
        # 2x2 grid so every label is visible at any reasonable card width.
        layout = [
            ("Near-lossless", "near_lossless", 0, 0),
            ("Light",         "light",         0, 1),
            ("Medium",        "medium",        1, 0),
            ("Strong",        "strong",        1, 1),
        ]
        for label, value, r, c in layout:
            rb = ctk.CTkRadioButton(
                manual, text=label, variable=self.strength_var, value=value,
                fg_color=ACCENT, hover_color=ACCENT_HOVER,
                text_color=TEXT, font=ctk.CTkFont(size=12),
            )
            rb.grid(row=r, column=c, sticky="w", padx=(0, 10), pady=(0, 6))
            self.manual_radios.append(rb)
        row += 1

        # Advanced -------------------------------------------------------
        ctk.CTkLabel(card, text="ADVANCED",
                     font=ctk.CTkFont(size=10, weight="bold"),
                     text_color=MUTED).grid(
            row=row, column=0, sticky="w", padx=PAD_X, pady=(16, 4))
        row += 1
        self.diff_chk = ctk.CTkCheckBox(
            card,
            text="Use diffusion regeneration for images",
            variable=self.diffusion_var,
            fg_color=ACCENT, hover_color=ACCENT_HOVER,
            text_color=TEXT, font=ctk.CTkFont(size=12),
        )
        self.diff_chk.grid(row=row, column=0, sticky="w", padx=PAD_X)
        row += 1
        ctk.CTkLabel(
            card,
            text=("Slow; requires torch + diffusers. Produces the cleanest "
                  "image outputs at the cost of a low-denoise img2img pass."),
            font=ctk.CTkFont(size=11), text_color=MUTED,
            wraplength=WRAP - 26, justify="left", anchor="w",
        ).grid(row=row, column=0, sticky="ew",
               padx=(PAD_X + 26, PAD_X), pady=(3, 18))
        row += 1

        return card

    # ------------------------------------------------------------ ttk skin
    def _style_tree(self):
        """Match ttk.Treeview colours to the current customtkinter theme."""
        mode = ctk.get_appearance_mode().lower()
        bg = "#1f2024" if mode == "dark" else "#ffffff"
        fg = "#e8eaee" if mode == "dark" else "#1f2328"
        muted = "#9aa1ac" if mode == "dark" else "#6b7280"
        sel = "#1f3b22" if mode == "dark" else "#e8f1e9"

        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure(
            "Scrub.Treeview",
            background=bg, fieldbackground=bg, foreground=fg,
            rowheight=30, borderwidth=0, font=("SF Pro Text", 12),
        )
        style.configure(
            "Scrub.Treeview.Heading",
            background=bg, foreground=muted,
            relief="flat", borderwidth=0,
            font=("SF Pro Text", 11, "bold"),
        )
        style.map(
            "Scrub.Treeview",
            background=[("selected", sel)],
            foreground=[("selected", fg)],
        )
        style.layout("Scrub.Treeview", [
            ("Scrub.Treeview.treearea", {"sticky": "nswe"}),
        ])

    # ------------------------------------------------------------ state
    def _refresh_strength_state(self):
        disable_manual = self.auto_var.get() or self.mode_var.get() == "metadata_only"
        for rb in self.manual_radios:
            rb.configure(state="disabled" if disable_manual else "normal")
        if self.mode_var.get() == "metadata_only":
            self.auto_switch.configure(state="disabled")
            self.diff_chk.configure(state="disabled")
        else:
            self.auto_switch.configure(state="normal")
            self.diff_chk.configure(state="normal")

    # ------------------------------------------------------------ log/poll
    def _append_log(self, msg: str):
        self.log.configure(state="normal")
        self.log.insert("end", msg + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _poll_messages(self):
        try:
            while True:
                kind, payload = self._msg_q.get_nowait()
                if kind == "log":
                    self._append_log(payload)
                elif kind == "progress":
                    self.progress.set(payload)
                elif kind == "status":
                    path, status, detail = payload
                    self._set_row_status(path, status, detail)
                elif kind == "done":
                    self._processing = False
                    self.run_btn.configure(
                        state="normal",
                        fg_color=ACCENT, hover_color=ACCENT_HOVER,
                        text="▶  Start — strip metadata & watermarks",
                    )
                    if self._last_out_dir and os.path.isdir(self._last_out_dir):
                        self.reveal_btn.configure(state="normal")
                    self._append_log(payload)
        except queue.Empty:
            pass
        self.after(120, self._poll_messages)

    def _set_row_status(self, path: str, status: str, detail: str = ""):
        row = self._file_rows.get(path)
        if not row:
            return
        origin = self._origin_cache.get(path, "")
        if detail:
            origin = detail
            self._origin_cache[path] = detail
        self.tree.item(row, values=(status, os.path.basename(path), origin))

    def _describe_origin(self, path: str) -> str:
        try:
            rep = detect_origin(path)
        except Exception:
            return ""
        if rep.matches:
            top = rep.matches[0].replace("_", " ")
            marker = "  ●" if rep.likely_robust_watermark else "  ○"
            return f"{top}{marker}"
        return "—"

    # ------------------------------------------------------------ actions
    def _select_files(self):
        patterns = [f"*{e}" for e in SUPPORTED_EXTS]
        filetypes = [
            ("Supported media", patterns),
            ("Images", [f"*{e}" for e in IMAGE_EXTS]),
            ("Videos", [f"*{e}" for e in VIDEO_EXTS]),
            ("Audio", [f"*{e}" for e in AUDIO_EXTS]),
            ("All files", ["*"]),
        ]
        paths = filedialog.askopenfilenames(title="Select files", filetypes=filetypes)
        if not paths:
            return
        for p in paths:
            if p in self._file_rows:
                continue
            origin = self._describe_origin(p)
            self._origin_cache[p] = origin
            row = self.tree.insert(
                "", "end",
                values=(STATUS_QUEUED, os.path.basename(p), origin),
            )
            self._file_rows[p] = row
            self._files.append(p)
        self._refresh_count()

    def _choose_output_dir(self):
        path = filedialog.askdirectory(title="Choose output folder")
        if path:
            self.output_dir_var.set(path)

    def _load_default_output_dir(self) -> str:
        try:
            if os.path.exists(CONFIG_PATH):
                with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                    data = json.load(f)
                saved = (data.get("default_output_dir") or "").strip()
                if saved:
                    return saved
        except Exception:
            pass
        return DEFAULT_OUT

    def _save_default_output_dir(self):
        path = (self.output_dir_var.get() or "").strip()
        if not path:
            self._append_log("Cannot save default: output folder is empty.")
            return
        try:
            os.makedirs(path, exist_ok=True)
        except Exception as e:
            self._append_log(f"Cannot save default output folder: {e}")
            return
        try:
            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump({"default_output_dir": path}, f, indent=2)
        except Exception as e:
            self._append_log(f"Cannot write config file: {e}")
            return
        self._append_log(f"Default output folder saved: {path}")

    def _clear_files(self):
        for _p, row in list(self._file_rows.items()):
            try:
                self.tree.delete(row)
            except tk.TclError:
                pass
        self._files = []
        self._file_rows.clear()
        self._origin_cache.clear()
        self._refresh_count()

    def _remove_selected(self):
        sel = self.tree.selection()
        if not sel:
            return
        reverse = {v: k for k, v in self._file_rows.items()}
        for row in sel:
            path = reverse.get(row)
            if path:
                self._files.remove(path)
                self._file_rows.pop(path, None)
                self._origin_cache.pop(path, None)
            self.tree.delete(row)
        self._refresh_count()

    def _refresh_count(self):
        n = len(self._files)
        self.count_label.configure(text=f"{n} file{'s' if n != 1 else ''}")

    def _reveal_output(self):
        out_dir = self._last_out_dir or self.output_dir_var.get()
        if not out_dir or not os.path.isdir(out_dir):
            messagebox.showinfo("No output yet",
                                "Run a cleaning pass first.")
            return
        try:
            if sys.platform == "darwin":
                subprocess.run(["open", out_dir], check=False)
            elif os.name == "nt":
                os.startfile(out_dir)  # type: ignore[attr-defined]
            else:
                subprocess.run(["xdg-open", out_dir], check=False)
        except Exception as e:
            messagebox.showerror("Cannot open folder", str(e))

    # ------------------------------------------------------------ worker
    def _run(self):
        if self._processing:
            return
        if not self._files:
            self._append_log("No files selected. Click the add panel first.")
            return
        out_dir = (self.output_dir_var.get() or "").strip()
        if not out_dir:
            self._append_log("Choose an output folder first.")
            return
        try:
            os.makedirs(out_dir, exist_ok=True)
        except Exception as e:
            self._append_log(f"Cannot create output folder: {e}")
            return

        mode = self.mode_var.get()
        strength = self.strength_var.get()
        auto = self.auto_var.get()
        use_diffusion = bool(self.diffusion_var.get())
        self._last_out_dir = out_dir

        self._append_log(
            f"Starting {len(self._files)} file(s) -> {out_dir} "
            f"[mode={mode}, auto={auto}, strength={strength}, "
            f"diffusion={use_diffusion}]"
        )

        for p in self._files:
            self._set_row_status(p, STATUS_QUEUED, self._origin_cache.get(p, ""))

        self._processing = True
        self.run_btn.configure(
            state="disabled",
            fg_color="#6b7280", hover_color="#6b7280",
            text="Working — please wait…",
        )
        self.reveal_btn.configure(state="disabled")
        self.progress.set(0.0)

        threading.Thread(
            target=self._worker,
            args=(list(self._files), out_dir, mode, strength,
                  auto, use_diffusion),
            daemon=True,
        ).start()

    def _worker(self, files, out_dir, mode, strength, auto, use_diffusion):
        ok = 0
        total = max(1, len(files))
        for i, p in enumerate(files, start=1):
            name = os.path.basename(p)
            self._msg_q.put(("status", (p, STATUS_RUNNING, "")))
            try:
                if mode == "metadata_only":
                    result_path, detail = strip_file_metadata(
                        input_path=p, output_dir=out_dir,
                        prefer_stream_copy=True,
                    )
                    origin_label = "metadata removed"
                else:
                    report = clean_file_v2(
                        input_path=p,
                        output_dir=out_dir,
                        strength=strength,
                        use_diffusion=use_diffusion,
                        auto_strength=auto,
                    )
                    result_path = report.output_path
                    detail = report.detail
                    if report.auto_escalated and report.origin and report.origin.matches:
                        origin_label = (f"{report.origin.matches[0]} "
                                        f"→ {report.strength_used}")
                    elif report.origin and report.origin.matches:
                        origin_label = (f"{report.origin.matches[0]} "
                                        f"(kept {report.strength_used})")
                    else:
                        origin_label = f"no signature · {report.strength_used}"
                ok += 1
                self._msg_q.put(("status", (p, STATUS_OK, origin_label)))
                self._msg_q.put(("log",
                                 f"OK   [{i}/{len(files)}]  {name}  →  "
                                 f"{os.path.basename(result_path)}"))
                self._msg_q.put(("log", f"        {detail}"))
            except Exception as e:
                # Surface the first line of the error in the table so the
                # user can see *what* went wrong without opening the log.
                first_line = str(e).splitlines()[0] if str(e) else ""
                short = (first_line[:44] + "…") if len(first_line) > 45 else first_line
                label = f"{type(e).__name__}: {short}" if short else type(e).__name__
                self._msg_q.put(("status", (p, STATUS_FAIL, label)))
                self._msg_q.put(("log",
                                 f"FAIL [{i}/{len(files)}]  {name}: "
                                 f"{type(e).__name__}: {e}"))
            self._msg_q.put(("progress", i / total))

        self._msg_q.put(
            ("done",
             f"Done. {ok}/{len(files)} succeeded. Output: {out_dir}")
        )


def main():
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
