"""Apple-style settings panel — grouped cards, subtle dividers, switches over checkboxes."""
from __future__ import annotations

import threading
from typing import Callable

import customtkinter as ctk

import recorder
import streaming
import theme as T
import transcribe

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")


LANGUAGES = [
    "auto", "en", "de", "fr", "es", "it", "nl", "pt", "pl", "ru",
    "ja", "zh", "ko", "tr", "ar", "uk", "sv", "no", "da", "fi",
]
SCHEMES = ["http", "https"]
WS_SCHEMES = ["ws", "wss"]
INJECT_METHODS = ["paste", "keystrokes"]
SAMPLE_RATES = ["16000", "22050", "44100", "48000"]
STREAMING_MODELS = ["tiny", "base", "small", "medium", "large-v3", "large-v3-turbo"]
STREAMING_TASKS = ["transcribe", "translate"]
TASK_DESCRIPTIONS = {
    "transcribe": "Keep the spoken language (e.g. German → German text)",
    "translate":  "Always translate to English regardless of spoken language",
}
PREVIEW_ANIMATIONS = ["instant", "typewriter_word", "typewriter_char", "fade", "cursor"]
UI_SCALES = ["0.85", "1.0", "1.15", "1.25", "1.5", "1.75", "2.0"]
ANIMATION_DESCRIPTIONS = {
    "instant":          "Text appears immediately as it arrives",
    "typewriter_word":  "Words appear one at a time (recommended)",
    "typewriter_char":  "Characters appear one at a time — classic typewriter",
    "fade":             "New text fades in from dim to bright",
    "cursor":           "Instant text with a blinking cursor at the end",
}
RECORD_MODES = ["hold_to_record", "press_to_toggle", "voice_activity_detection", "continuous"]
MODE_LABELS = {
    "hold_to_record": "Hold to record",
    "press_to_toggle": "Press to toggle",
    "voice_activity_detection": "Voice activity detection",
    "continuous": "Continuous dictation",
}
MODE_DESCRIPTIONS = {
    "hold_to_record": "Hold the hotkey while talking. Release to transcribe.",
    "press_to_toggle": "Tap the hotkey to start. Tap again to stop & transcribe.",
    "voice_activity_detection": "Tap to start. Auto-stops after a brief pause.",
    "continuous": "Tap to start a session. Pauses split utterances. Tap again to end.",
}


# ───────────────────────────── primitives ───────────────────────────────────


class Card(ctk.CTkFrame):
    """A grouped settings card — rounded surface with subtle inner padding."""

    def __init__(self, master, **kwargs) -> None:
        super().__init__(
            master,
            fg_color=T.BG_CARD,
            corner_radius=T.R_LG,
            border_width=0,
            **kwargs,
        )


class Section:
    """One section of the panel: title + optional description + a card with rows."""

    def __init__(self, parent, title: str, subtitle: str = "") -> None:
        wrap = ctk.CTkFrame(parent, fg_color="transparent")
        wrap.pack(fill="x", padx=T.S_XL, pady=(T.S_LG, 0))

        ctk.CTkLabel(
            wrap, text=title, anchor="w",
            text_color=T.TEXT_PRIMARY, font=T.title(15, "bold"),
        ).pack(anchor="w")

        if subtitle:
            ctk.CTkLabel(
                wrap, text=subtitle, anchor="w", wraplength=560,
                text_color=T.TEXT_SECONDARY, font=T.caption(11),
                justify="left",
            ).pack(anchor="w", pady=(2, 0))

        self.card = Card(parent)
        self.card.pack(fill="x", padx=T.S_XL, pady=(T.S_SM, 0))
        self._first_row = True

    def add_row(self, *widgets, weight=(0,), divider: bool = True) -> ctk.CTkFrame:
        """Add a labeled row inside the card."""
        if not self._first_row and divider:
            ctk.CTkFrame(self.card, fg_color=T.SEPARATOR, height=1).pack(
                fill="x", padx=T.S_LG, pady=0,
            )
        self._first_row = False

        row = ctk.CTkFrame(self.card, fg_color="transparent")
        row.pack(fill="x", padx=T.S_LG, pady=T.S_MD)
        return row


def labeled_row(section: Section, label: str, control_builder, hint: str = "") -> None:
    """Helper: row with a left-aligned label and a control(s) on the right."""
    row = section.add_row()
    left = ctk.CTkFrame(row, fg_color="transparent")
    left.pack(side="left", fill="x", expand=True)
    ctk.CTkLabel(left, text=label, anchor="w",
                 text_color=T.TEXT_PRIMARY, font=T.body(13)).pack(anchor="w")
    if hint:
        ctk.CTkLabel(left, text=hint, anchor="w",
                     text_color=T.TEXT_TERTIARY, font=T.caption(11)).pack(anchor="w")
    control_builder(row)


# ───────────────────────────── window ───────────────────────────────────────


class SettingsWindow(ctk.CTkToplevel):
    def __init__(self, master, cfg: dict, on_save: Callable[[dict], None]) -> None:
        super().__init__(master)
        self.title("Settings")
        self.geometry("680x880")
        self.minsize(640, 640)
        self.configure(fg_color=T.BG_BASE)
        self.attributes("-topmost", True)
        self.after(180, lambda: self.attributes("-topmost", False))

        self._cfg = dict(cfg)
        self._on_save = on_save

        # Header bar
        header = ctk.CTkFrame(self, fg_color="transparent", height=84)
        header.pack(fill="x", padx=T.S_XL, pady=(T.S_XL, 0))
        header.pack_propagate(False)
        ctk.CTkLabel(
            header, text="Murmur", anchor="w",
            text_color=T.TEXT_PRIMARY, font=T.display(26, "bold"),
        ).pack(anchor="w")
        ctk.CTkLabel(
            header, text="Push-to-talk dictation · local Whisper",
            anchor="w", text_color=T.TEXT_SECONDARY, font=T.body(13),
        ).pack(anchor="w", pady=(2, 0))

        # Scroll body
        body = ctk.CTkScrollableFrame(
            self, fg_color="transparent",
            scrollbar_button_color=T.BG_CARD, scrollbar_button_hover_color=T.BG_HOVER,
        )
        body.pack(fill="both", expand=True, padx=0, pady=(T.S_SM, 0))

        # Build all sections
        self._build_streaming(body)
        self._build_recording(body)
        self._build_audio(body)
        self._build_input(body)
        self._build_batch(body)
        self._build_feedback(body)

        # Footer with Save/Cancel
        self._build_footer()

        # Async server probes (both batch + streaming).
        threading.Thread(target=self._test_connection_silent, daemon=True).start()
        threading.Thread(target=self._test_streaming_silent, daemon=True).start()

    # ───── sections ─────

    def _build_streaming(self, parent) -> None:
        cfg = self._cfg
        section = Section(
            parent,
            "Live streaming",
            "Words appear as you speak via WhisperLive on your local GPU.",
        )

        # Enable toggle row
        def enabled_ctrl(row):
            self.streaming_enabled_var = ctk.BooleanVar(value=bool(cfg.get("streaming_enabled", False)))
            sw = ctk.CTkSwitch(
                row, text="", variable=self.streaming_enabled_var,
                progress_color=T.ACCENT, button_color="#ffffff", button_hover_color="#ffffff",
                fg_color=T.BG_INPUT, width=44, height=24, corner_radius=12,
            )
            sw.pack(side="right")
        labeled_row(section, "Enable streaming", enabled_ctrl,
                    hint="Recommended. Falls back to batch if unreachable.")

        # Server endpoint
        def server_ctrl(row):
            wrap = ctk.CTkFrame(row, fg_color="transparent")
            wrap.pack(side="right")
            self.streaming_scheme_var = ctk.StringVar(value=cfg.get("streaming_scheme", "ws"))
            ctk.CTkOptionMenu(
                wrap, variable=self.streaming_scheme_var, values=WS_SCHEMES, width=64,
                fg_color=T.BG_INPUT, button_color=T.BG_INPUT, button_hover_color=T.BG_HOVER,
                text_color=T.TEXT_PRIMARY, dropdown_fg_color=T.BG_CARD,
                font=T.body(12),
            ).pack(side="left")
            self.streaming_host_var = ctk.StringVar(value=cfg.get("streaming_host", "127.0.0.1"))
            _entry(wrap, self.streaming_host_var, width=150).pack(side="left", padx=6)
            ctk.CTkLabel(wrap, text=":", text_color=T.TEXT_TERTIARY).pack(side="left")
            self.streaming_port_var = ctk.StringVar(value=str(cfg.get("streaming_port", 9090)))
            _entry(wrap, self.streaming_port_var, width=70).pack(side="left", padx=(4, 6))
            ctk.CTkButton(
                wrap, text="Test", width=54, command=self._test_streaming,
                fg_color=T.ACCENT, hover_color=T.ACCENT_HOVER, text_color="#ffffff",
                corner_radius=T.R_MD, font=T.body(12, "bold"),
            ).pack(side="left")
        labeled_row(section, "Server", server_ctrl, hint="WhisperLive WebSocket")

        # Streaming status line.
        self.streaming_status_var = ctk.StringVar(value="")
        status_row = section.add_row()
        ctk.CTkLabel(
            status_row, textvariable=self.streaming_status_var,
            text_color=T.TEXT_TERTIARY, font=T.caption(11), anchor="w",
        ).pack(anchor="w")

        # Model
        def model_ctrl(row):
            self.streaming_model_var = ctk.StringVar(value=cfg.get("streaming_model", "small"))
            ctk.CTkOptionMenu(
                row, variable=self.streaming_model_var, values=STREAMING_MODELS, width=220,
                fg_color=T.BG_INPUT, button_color=T.BG_INPUT, button_hover_color=T.BG_HOVER,
                text_color=T.TEXT_PRIMARY, dropdown_fg_color=T.BG_CARD,
                font=T.body(12),
            ).pack(side="right")
        labeled_row(section, "Model", model_ctrl,
                    hint="Smaller = lower latency · larger = higher quality")

    def _build_recording(self, parent) -> None:
        cfg = self._cfg
        section = Section(parent, "Recording")

        def mode_ctrl(row):
            self.mode_var = ctk.StringVar(value=cfg.get("record_mode", "hold_to_record"))
            menu = ctk.CTkOptionMenu(
                row, variable=self.mode_var, values=RECORD_MODES, width=260,
                fg_color=T.BG_INPUT, button_color=T.BG_INPUT, button_hover_color=T.BG_HOVER,
                text_color=T.TEXT_PRIMARY, dropdown_fg_color=T.BG_CARD,
                font=T.body(12),
                command=lambda v: self._mode_desc.configure(text=MODE_DESCRIPTIONS.get(v, "")),
            )
            menu.pack(side="right")
        labeled_row(section, "Mode", mode_ctrl)

        # Description sub-row (no border, no divider)
        ctk.CTkFrame(section.card, fg_color=T.SEPARATOR, height=1).pack(fill="x", padx=T.S_LG)
        desc_row = ctk.CTkFrame(section.card, fg_color="transparent")
        desc_row.pack(fill="x", padx=T.S_LG, pady=(T.S_MD, T.S_MD))
        self._mode_desc = ctk.CTkLabel(
            desc_row, text=MODE_DESCRIPTIONS[self.mode_var.get()],
            text_color=T.TEXT_SECONDARY, font=T.caption(11), anchor="w",
            justify="left", wraplength=580,
        )
        self._mode_desc.pack(anchor="w")

        # VAD silence slider
        def vad_ctrl(row):
            wrap = ctk.CTkFrame(row, fg_color="transparent")
            wrap.pack(side="right")
            self.vad_silence_var = ctk.IntVar(value=int(cfg.get("vad_silence_ms", 700)))
            self._vad_label = ctk.CTkLabel(
                wrap, text=f"{self.vad_silence_var.get()} ms", width=70,
                text_color=T.TEXT_SECONDARY, font=T.body(12), anchor="e",
            )
            ctk.CTkSlider(
                wrap, from_=200, to=2000, number_of_steps=36,
                variable=self.vad_silence_var, width=240,
                progress_color=T.ACCENT, button_color=T.ACCENT,
                button_hover_color=T.ACCENT_HOVER, fg_color=T.BG_INPUT,
                command=lambda v: self._vad_label.configure(text=f"{int(float(v))} ms"),
            ).pack(side="left")
            self._vad_label.pack(side="left", padx=(10, 0))
        labeled_row(section, "Silence timeout", vad_ctrl,
                    hint="For VAD & continuous modes")

    def _build_audio(self, parent) -> None:
        cfg = self._cfg
        section = Section(parent, "Audio")

        devices = [("Default", None)] + [(name, idx) for idx, name in recorder.list_input_devices()]
        self._device_map = {label: idx for label, idx in devices}
        current_idx = cfg.get("audio_device")
        current_name = cfg.get("audio_device_name")
        current_label = "Default"
        if current_name and current_name in self._device_map:
            current_label = current_name
        else:
            for label, idx in devices:
                if idx == current_idx:
                    current_label = label
                    break

        def mic_ctrl(row):
            wrap = ctk.CTkFrame(row, fg_color="transparent")
            wrap.pack(side="right")
            self.device_var = ctk.StringVar(value=current_label)
            ctk.CTkOptionMenu(
                wrap, variable=self.device_var,
                values=[label for label, _ in devices], width=300,
                fg_color=T.BG_INPUT, button_color=T.BG_INPUT, button_hover_color=T.BG_HOVER,
                text_color=T.TEXT_PRIMARY, dropdown_fg_color=T.BG_CARD,
                font=T.body(12),
            ).pack(side="left")
            ctk.CTkButton(
                wrap, text="↻", width=32, height=28, command=self._refresh_devices,
                fg_color=T.BG_INPUT, hover_color=T.BG_HOVER, text_color=T.TEXT_PRIMARY,
                corner_radius=T.R_MD, font=T.body(14, "bold"),
            ).pack(side="left", padx=(6, 0))
        labeled_row(section, "Microphone", mic_ctrl)

        def rate_ctrl(row):
            self.rate_var = ctk.StringVar(value=str(cfg.get("sample_rate", 16000)))
            ctk.CTkOptionMenu(
                row, variable=self.rate_var, values=SAMPLE_RATES, width=120,
                fg_color=T.BG_INPUT, button_color=T.BG_INPUT, button_hover_color=T.BG_HOVER,
                text_color=T.TEXT_PRIMARY, dropdown_fg_color=T.BG_CARD,
                font=T.body(12),
            ).pack(side="right")
        labeled_row(section, "Sample rate", rate_ctrl, hint="Hz")

    def _build_input(self, parent) -> None:
        cfg = self._cfg
        section = Section(parent, "Hotkey")

        def hotkey_ctrl(row):
            self.hotkey_var = ctk.StringVar(value=cfg.get("hotkey", "f12"))
            _entry(row, self.hotkey_var, width=220, mono=True).pack(side="right")
        labeled_row(section, "Combo", hotkey_ctrl,
                    hint="e.g. f12, ctrl+alt+space, right_ctrl")

        def inject_ctrl(row):
            self.inject_var = ctk.StringVar(value=cfg.get("inject_method", "paste"))
            ctk.CTkOptionMenu(
                row, variable=self.inject_var, values=INJECT_METHODS, width=160,
                fg_color=T.BG_INPUT, button_color=T.BG_INPUT, button_hover_color=T.BG_HOVER,
                text_color=T.TEXT_PRIMARY, dropdown_fg_color=T.BG_CARD,
                font=T.body(12),
            ).pack(side="right")
        labeled_row(section, "Inject method", inject_ctrl,
                    hint="Paste works in terminals · keystrokes for picky apps")

    def _build_batch(self, parent) -> None:
        cfg = self._cfg
        section = Section(
            parent, "Batch fallback",
            "OpenAI-compatible server used when streaming is off or unreachable.",
        )

        def server_ctrl(row):
            wrap = ctk.CTkFrame(row, fg_color="transparent")
            wrap.pack(side="right")
            self.scheme_var = ctk.StringVar(value=cfg.get("server_scheme", "http"))
            ctk.CTkOptionMenu(
                wrap, variable=self.scheme_var, values=SCHEMES, width=70,
                fg_color=T.BG_INPUT, button_color=T.BG_INPUT, button_hover_color=T.BG_HOVER,
                text_color=T.TEXT_PRIMARY, dropdown_fg_color=T.BG_CARD,
                font=T.body(12),
            ).pack(side="left")
            self.host_var = ctk.StringVar(value=cfg.get("server_host", "127.0.0.1"))
            _entry(wrap, self.host_var, width=170).pack(side="left", padx=6)
            ctk.CTkLabel(wrap, text=":", text_color=T.TEXT_TERTIARY).pack(side="left")
            self.port_var = ctk.StringVar(value=str(cfg.get("server_port", 8000)))
            _entry(wrap, self.port_var, width=70).pack(side="left", padx=(4, 6))
            ctk.CTkButton(
                wrap, text="Test", width=54, command=self._test_connection,
                fg_color=T.ACCENT, hover_color=T.ACCENT_HOVER, text_color="#ffffff",
                corner_radius=T.R_MD, font=T.body(12, "bold"),
            ).pack(side="left")
        labeled_row(section, "Server", server_ctrl, hint="OpenAI-compatible HTTP")

        self.server_status_var = ctk.StringVar(value="")
        status_row = section.add_row()
        ctk.CTkLabel(
            status_row, textvariable=self.server_status_var,
            text_color=T.TEXT_TERTIARY, font=T.caption(11), anchor="w",
        ).pack(anchor="w")

        def model_ctrl(row):
            wrap = ctk.CTkFrame(row, fg_color="transparent")
            wrap.pack(side="right")
            self.model_var = ctk.StringVar(value=cfg.get("model", ""))
            self._all_models: list[str] = [cfg.get("model", "")] if cfg.get("model") else []
            self.model_combo = ctk.CTkComboBox(
                wrap, variable=self.model_var, values=self._all_models, width=360,
                fg_color=T.BG_INPUT, border_color=T.BG_INPUT, button_color=T.BG_INPUT,
                button_hover_color=T.BG_HOVER, text_color=T.TEXT_PRIMARY,
                dropdown_fg_color=T.BG_CARD, font=T.body(12),
            )
            self.model_combo.pack(side="right")
            self.model_combo.bind("<KeyRelease>", self._filter_models)
        labeled_row(section, "Model", model_ctrl, hint="Type to filter · large list")

        def lang_ctrl(row):
            self.lang_var = ctk.StringVar(value=cfg.get("language", "auto"))
            ctk.CTkOptionMenu(
                row, variable=self.lang_var, values=LANGUAGES, width=140,
                fg_color=T.BG_INPUT, button_color=T.BG_INPUT, button_hover_color=T.BG_HOVER,
                text_color=T.TEXT_PRIMARY, dropdown_fg_color=T.BG_CARD,
                font=T.body(12),
            ).pack(side="right")
        labeled_row(section, "Language", lang_ctrl, hint="auto = whisper detects per utterance")

    def _build_feedback(self, parent) -> None:
        cfg = self._cfg
        section = Section(parent, "Feedback")

        def status_ctrl(row):
            self.status_window_var = ctk.BooleanVar(value=bool(cfg.get("show_status_window", True)))
            ctk.CTkSwitch(
                row, text="", variable=self.status_window_var,
                progress_color=T.ACCENT, button_color="#ffffff",
                fg_color=T.BG_INPUT, width=44, height=24, corner_radius=12,
            ).pack(side="right")
        labeled_row(section, "Status overlay", status_ctrl,
                    hint="Floating window showing live recording / transcription")

        def scale_ctrl(row):
            self.ui_scale_var = ctk.StringVar(value=str(cfg.get("ui_scale", 1.0)))
            ctk.CTkOptionMenu(
                row, variable=self.ui_scale_var, values=UI_SCALES, width=120,
                fg_color=T.BG_INPUT, button_color=T.BG_INPUT,
                button_hover_color=T.BG_HOVER, text_color=T.TEXT_PRIMARY,
                dropdown_fg_color=T.BG_CARD, font=T.body(12),
            ).pack(side="right")
        labeled_row(section, "UI scale", scale_ctrl,
                    hint="Multiplier on top of system DPI · requires app restart")

        def anim_ctrl(row):
            self.preview_animation_var = ctk.StringVar(
                value=cfg.get("preview_animation", "typewriter_word")
            )
            ctk.CTkOptionMenu(
                row, variable=self.preview_animation_var,
                values=PREVIEW_ANIMATIONS, width=200,
                fg_color=T.BG_INPUT, button_color=T.BG_INPUT,
                button_hover_color=T.BG_HOVER, text_color=T.TEXT_PRIMARY,
                dropdown_fg_color=T.BG_CARD, font=T.body(12),
                command=lambda v: self._anim_desc.configure(
                    text=ANIMATION_DESCRIPTIONS.get(v, "")
                ),
            ).pack(side="right")
        labeled_row(section, "Live text animation", anim_ctrl,
                    hint="How streaming text appears in the overlay")

        # Animation description row.
        desc_row = section.add_row()
        self._anim_desc = ctk.CTkLabel(
            desc_row,
            text=ANIMATION_DESCRIPTIONS.get(
                cfg.get("preview_animation", "typewriter_word"), ""
            ),
            text_color=T.TEXT_TERTIARY, font=T.caption(11), anchor="w",
            justify="left",
        )
        self._anim_desc.pack(anchor="w")

        def sounds_ctrl(row):
            self.play_sounds_var = ctk.BooleanVar(value=bool(cfg.get("play_sounds", True)))
            ctk.CTkSwitch(
                row, text="", variable=self.play_sounds_var,
                progress_color=T.ACCENT, button_color="#ffffff",
                fg_color=T.BG_INPUT, width=44, height=24, corner_radius=12,
            ).pack(side="right")
        labeled_row(section, "Audio cues", sounds_ctrl,
                    hint="Short beep at start and end of each recording")

    def _build_footer(self) -> None:
        # Hairline separator above the footer.
        ctk.CTkFrame(self, fg_color=T.SEPARATOR, height=1).pack(fill="x", pady=(T.S_LG, 0))
        bar = ctk.CTkFrame(self, fg_color=T.BG_BASE, height=68)
        bar.pack(fill="x", side="bottom")
        bar.pack_propagate(False)

        ctk.CTkButton(
            bar, text="Save", width=120, height=36, command=self._save,
            fg_color=T.ACCENT, hover_color=T.ACCENT_HOVER, text_color="#ffffff",
            corner_radius=T.R_MD, font=T.body(13, "bold"),
        ).pack(side="right", padx=(0, T.S_XL), pady=T.S_LG)

        ctk.CTkButton(
            bar, text="Cancel", width=92, height=36, command=self.destroy,
            fg_color="transparent", hover_color=T.BG_CARD,
            text_color=T.TEXT_PRIMARY, border_width=0,
            corner_radius=T.R_MD, font=T.body(13),
        ).pack(side="right", padx=(0, T.S_SM), pady=T.S_LG)

    # ───── helpers ─────

    def _current_url(self) -> str:
        scheme = self.scheme_var.get()
        host = self.host_var.get().strip() or "127.0.0.1"
        port = self.port_var.get().strip() or "8000"
        return f"{scheme}://{host}:{port}"

    def _test_connection(self) -> None:
        self.server_status_var.set("Testing…")
        threading.Thread(target=self._test_connection_silent, daemon=True).start()

    def _test_streaming(self) -> None:
        self.streaming_status_var.set("Testing…")
        threading.Thread(target=self._test_streaming_silent, daemon=True).start()

    def _test_streaming_silent(self) -> None:
        scheme = self.streaming_scheme_var.get()
        host = self.streaming_host_var.get().strip() or "127.0.0.1"
        port = (self.streaming_port_var.get() or "9090").strip()
        url = f"{scheme}://{host}:{port}"
        model = self.streaming_model_var.get()

        import time
        t0 = time.time()
        cli = streaming.WhisperLiveClient(url, language="en", model=model, timeout=8.0)
        try:
            cli.connect()
        except streaming.StreamingError as exc:
            self.streaming_status_var.set(f"●  Unreachable — {exc}")
            return
        finally:
            try:
                cli.close()
            except Exception:
                pass
        dt = time.time() - t0
        self.streaming_status_var.set(
            f"●  Connected · ready in {dt:.1f}s · model={model}"
        )

    def _test_connection_silent(self) -> None:
        url = self._current_url()
        models = transcribe.list_models(url, timeout=4.0)
        if not models:
            self.server_status_var.set(f"●  Unreachable — {url}")
            return
        recommended = [
            "deepdml/faster-whisper-large-v3-turbo-ct2",
            "Systran/faster-whisper-large-v3",
            "Systran/faster-whisper-medium",
            "Systran/faster-whisper-small",
        ]
        top = [m for m in recommended if m in models]
        rest = sorted(m for m in models if m not in top)
        self._all_models = top + rest
        current = self.model_var.get()
        if current and current not in self._all_models:
            self._all_models = [current] + self._all_models
        self._filter_models()
        self.server_status_var.set(f"●  Connected · {len(models)} models")

    def _filter_models(self, _event=None) -> None:
        typed = self.model_var.get().lower().strip()
        if not typed:
            shown = self._all_models[:60]
        else:
            matches = [m for m in self._all_models if typed in m.lower()]
            shown = matches[:60] if matches else self._all_models[:60]
        if not shown:
            shown = [self.model_var.get() or ""]
        self.model_combo.configure(values=shown)

    def _refresh_devices(self) -> None:
        devices = [("Default", None)] + [(name, idx) for idx, name in recorder.list_input_devices()]
        self._device_map = {label: idx for label, idx in devices}

    def _save(self) -> None:
        try:
            label = self.device_var.get()
            new_cfg = dict(self._cfg)
            new_cfg["server_scheme"] = self.scheme_var.get()
            new_cfg["server_host"] = self.host_var.get().strip() or "127.0.0.1"
            new_cfg["server_port"] = int(self.port_var.get().strip() or 8000)
            new_cfg.pop("server_url", None)
            new_cfg["model"] = self.model_var.get().strip()
            new_cfg["language"] = self.lang_var.get().strip() or "auto"
            new_cfg["hotkey"] = self.hotkey_var.get().strip().lower()
            new_cfg["inject_method"] = self.inject_var.get()
            new_cfg["sample_rate"] = int(self.rate_var.get())
            new_cfg["audio_device"] = self._device_map.get(label)
            new_cfg["audio_device_name"] = None if label == "Default" else label
            new_cfg["record_mode"] = self.mode_var.get()
            new_cfg["vad_silence_ms"] = int(self.vad_silence_var.get())
            new_cfg["show_status_window"] = bool(self.status_window_var.get())
            new_cfg["preview_animation"] = self.preview_animation_var.get()
            new_cfg["ui_scale"] = float(self.ui_scale_var.get() or 1.0)
            new_cfg["play_sounds"] = bool(self.play_sounds_var.get())
            new_cfg["streaming_enabled"] = bool(self.streaming_enabled_var.get())
            new_cfg["streaming_scheme"] = self.streaming_scheme_var.get()
            new_cfg["streaming_host"] = self.streaming_host_var.get().strip() or "127.0.0.1"
            new_cfg["streaming_port"] = int(self.streaming_port_var.get().strip() or 9090)
            new_cfg["streaming_model"] = self.streaming_model_var.get()
            new_cfg["streaming_task"] = self.streaming_task_var.get()
        except Exception as exc:
            self.server_status_var.set(f"●  Invalid: {exc}")
            return
        self._on_save(new_cfg)
        self.destroy()


def _entry(parent, var, width: int = 200, mono: bool = False) -> ctk.CTkEntry:
    return ctk.CTkEntry(
        parent, textvariable=var, width=width, height=30,
        fg_color=T.BG_INPUT, border_color=T.BG_INPUT, border_width=0,
        text_color=T.TEXT_PRIMARY, corner_radius=T.R_MD,
        font=T.mono(12) if mono else T.body(12),
    )
