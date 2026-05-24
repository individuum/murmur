from __future__ import annotations

import logging
import sys
import threading
import time
import traceback
from typing import Optional


# ── Enable Windows per-monitor DPI awareness BEFORE Tk imports anything. ────
# Without this, Windows pretends the screen is 5120×1440 instead of 7680×2160
# on a 150%-scaled 4K display and bitmap-stretches the app, making everything
# blurry and the status overlay look tiny.
def _enable_dpi_awareness() -> float:
    """Make the process per-monitor DPI aware AND return the scaling factor
    of the *primary* monitor (e.g. 1.0 / 1.25 / 1.5 / 2.0).

    NOTE: `GetDpiForSystem` returns 96 even on a 150% display — that API
    reports the "logical" system DPI of the original session, not the
    current monitor. Use `GetDpiForMonitor` instead.
    """
    if sys.platform != "win32":
        return 1.0
    import ctypes
    from ctypes import wintypes

    # PROCESS_PER_MONITOR_DPI_AWARE_V2 → best mode (Win 10 1703+).
    # The -4 constant must be wrapped in c_void_p so it's marshalled as a
    # pointer-sized value on x64; otherwise the call silently no-ops.
    try:
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    except Exception:
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            try:
                ctypes.windll.user32.SetProcessDPIAware()
            except Exception:
                pass

    # Find the primary monitor by enumerating + checking the PRIMARY flag,
    # then ask for its actual DPI. (POINT(0,0) isn't always on the primary.)
    try:
        class MONITORINFOEXW(ctypes.Structure):
            _fields_ = [
                ("cbSize", wintypes.DWORD),
                ("rcMonitor", wintypes.RECT),
                ("rcWork", wintypes.RECT),
                ("dwFlags", wintypes.DWORD),
                ("szDevice", ctypes.c_wchar * 32),
            ]
        MONITORINFOF_PRIMARY = 1
        primary_hmon = [None]

        @ctypes.WINFUNCTYPE(
            ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p,
            ctypes.POINTER(wintypes.RECT), ctypes.c_void_p,
        )
        def _cb(hmon, hdc, lprect, data):
            mi = MONITORINFOEXW(); mi.cbSize = ctypes.sizeof(MONITORINFOEXW)
            if ctypes.windll.user32.GetMonitorInfoW(hmon, ctypes.byref(mi)):
                if mi.dwFlags & MONITORINFOF_PRIMARY:
                    primary_hmon[0] = hmon
            return 1

        ctypes.windll.user32.EnumDisplayMonitors(None, None, _cb, 0)
        if primary_hmon[0]:
            dpi_x = ctypes.c_uint(); dpi_y = ctypes.c_uint()
            MDT_EFFECTIVE_DPI = 0
            if ctypes.windll.shcore.GetDpiForMonitor(
                primary_hmon[0], MDT_EFFECTIVE_DPI,
                ctypes.byref(dpi_x), ctypes.byref(dpi_y),
            ) == 0:
                return max(1.0, dpi_x.value / 96.0)
    except Exception:
        pass

    # Fallback: system DPI (often 96 even on scaled displays — not great).
    try:
        return max(1.0, ctypes.windll.user32.GetDpiForSystem() / 96.0)
    except Exception:
        return 1.0


_DPI_SCALE = _enable_dpi_awareness()


# Hold a global mutex handle so it lives as long as the process. Exits with
# code 0 if another instance already exists — prevents the CPU pile-up from
# zombie copies of the .exe stacking up.
_SINGLE_INSTANCE_HANDLE = None


def _enforce_single_instance() -> None:
    global _SINGLE_INSTANCE_HANDLE
    if sys.platform != "win32":
        return
    import ctypes
    ERROR_ALREADY_EXISTS = 183
    kernel32 = ctypes.windll.kernel32
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    _SINGLE_INSTANCE_HANDLE = kernel32.CreateMutexW(None, False, "Murmur_SingleInstance_v1")
    if kernel32.GetLastError() == ERROR_ALREADY_EXISTS:
        # Another instance won the race — bail out quietly.
        sys.exit(0)


_enforce_single_instance()


import customtkinter as ctk  # noqa: E402  (after DPI awareness)
import pystray  # noqa: E402

# Make CustomTkinter scale widgets + windows to physical DPI.
ctk.set_widget_scaling(_DPI_SCALE)
ctk.set_window_scaling(_DPI_SCALE)
# Optional user override (e.g. "1.25" to bump everything 25% bigger).
# Read after config has loaded; see App._apply_ui_scale.

import config as cfg_mod
import icons
import inject as injector
import recorder as rec_mod
import sounds
import transcribe as tx
from hotkey import HotkeyListener
from paths import app_dir
from status_window import StatusWindow
from streaming import AudioPipe, StreamingError, WhisperLiveClient
from vad import EnergyVAD


LOG_PATH = app_dir() / "murmur.log"
LAST_RECORDING = app_dir() / "last-recording.wav"

APP_NAME = "Murmur"

VALID_MODES = ("hold_to_record", "press_to_toggle", "voice_activity_detection", "continuous")


def _setup_logging() -> logging.Logger:
    """Configure logging with a stream handler only if stdout exists.

    When PyInstaller wraps us as a GUI exe (--noconsole), sys.stdout is None.
    Adding StreamHandler(None) silently breaks subsequent log calls in some
    Python builds. So we add it conditionally.
    """
    handlers: list[logging.Handler] = [logging.FileHandler(LOG_PATH, encoding="utf-8")]
    if sys.stdout is not None and sys.stdout.fileno() >= 0:
        try:
            handlers.append(logging.StreamHandler(sys.stdout))
        except Exception:
            pass
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(threadName)s %(message)s",
        handlers=handlers,
        force=True,
    )
    # Silence noisy third-party loggers; ours stays at DEBUG.
    for noisy in ("PIL", "urllib3", "requests", "websocket"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    logger = logging.getLogger("murmur")
    logger.setLevel(logging.DEBUG)
    return logger


log = _setup_logging()


def _trap(name: str, fn):
    """Wrap a callable so any exception is logged to file. Avoids silent loss
    inside hotkey/tray callbacks."""
    def wrapper(*a, **kw):
        try:
            return fn(*a, **kw)
        except Exception:
            log.exception("Unhandled exception in %s", name)
    wrapper.__name__ = name
    return wrapper


class App:
    def __init__(self) -> None:
        self.cfg = cfg_mod.load()
        # Apply user override on top of detected DPI scaling.
        user_scale = float(self.cfg.get("ui_scale", 1.0) or 1.0)
        if user_scale != 1.0:
            ctk.set_widget_scaling(_DPI_SCALE * user_scale)
            ctk.set_window_scaling(_DPI_SCALE * user_scale)
        log.info(
            "DPI: detected scale=%.2f, user override=%.2f, effective=%.2f",
            _DPI_SCALE, user_scale, _DPI_SCALE * user_scale,
        )
        self.recorder = rec_mod.Recorder(
            sample_rate=int(self.cfg["sample_rate"]),
            device=self._resolve_device(),
        )
        self.hotkey: Optional[HotkeyListener] = None
        self.tray: Optional[pystray.Icon] = None
        self.root: Optional[ctk.CTk] = None
        self._status_window: Optional[StatusWindow] = None
        self._settings_window = None
        self._level_poll_after = None
        self._last_stage = "idle"

        # Streaming state (per recording cycle).
        self._streaming_client: Optional[WhisperLiveClient] = None
        self._streaming_pipe: Optional[AudioPipe] = None
        self._streaming_listener = None
        self._streaming_connecting = False

        # Session state for non-hold modes (toggle / vad / continuous).
        # `_session_stop` being non-None means a session is running.
        self._session_stop: Optional[threading.Event] = None
        self._session_thread: Optional[threading.Thread] = None
        self._session_lock = threading.Lock()

        # Hold-mode state.
        self._hold_active = False
        self._hold_start_ts = 0.0

    # ───── helpers ─────

    def _resolve_device(self):
        name = self.cfg.get("audio_device_name")
        if name:
            for idx, dev_name in rec_mod.list_input_devices():
                if dev_name == name:
                    return idx
        return self.cfg.get("audio_device")

    def _mode(self) -> str:
        mode = self.cfg.get("record_mode", "hold_to_record")
        return mode if mode in VALID_MODES else "hold_to_record"

    def _use_streaming(self) -> bool:
        return bool(self.cfg.get("streaming_enabled")) and bool(self.cfg.get("streaming_host"))

    def _streaming_url(self) -> str:
        scheme = self.cfg.get("streaming_scheme", "ws")
        host = self.cfg.get("streaming_host", "127.0.0.1")
        port = int(self.cfg.get("streaming_port", 9090))
        return f"{scheme}://{host}:{port}"

    # ───── lifecycle ─────

    def run(self) -> None:
        url = cfg_mod.server_url(self.cfg)
        log.info(
            "Starting Murmur — mode=%s, hotkey=%s, model=%s, server=%s",
            self._mode(), self.cfg["hotkey"], self.cfg["model"], url,
        )
        # Tk root owns the main thread. Hidden — only the tray icon is visible
        # at rest; Toplevels (status / settings) appear over it as needed.
        self.root = ctk.CTk()
        self.root.withdraw()
        self.root.title(APP_NAME)
        self.root.protocol("WM_DELETE_WINDOW", lambda: None)

        threading.Thread(target=self._probe_server, args=(url,), daemon=True).start()
        self._start_hotkey()
        self._start_tray()

        try:
            self.root.mainloop()
        finally:
            self._teardown()

    def _probe_server(self, url: str) -> None:
        models = tx.list_models(url, timeout=3.0)
        if models:
            log.info("Server reachable — %d models available", len(models))
        else:
            log.warning("⚠  Server at %s is unreachable. Start the podman container.", url)

    def _teardown(self) -> None:
        log.info("Shutting down")
        self._end_session()  # cancel any running session
        try:
            if self.hotkey:
                self.hotkey.stop()
        except Exception:
            log.exception("hotkey stop failed")
        try:
            if self.tray:
                self.tray.stop()
        except Exception:
            log.exception("tray stop failed")

    # ───── tray ─────

    def _start_tray(self) -> None:
        menu = pystray.Menu(
            pystray.MenuItem("Settings", lambda *_: self.open_settings()),
            pystray.MenuItem("Reload config", lambda *_: self.reload_config()),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(lambda _: f"Mode: {self._mode()}", None, enabled=False),
            pystray.MenuItem(lambda _: f"Hotkey: {self.cfg['hotkey']}", None, enabled=False),
            pystray.MenuItem(lambda _: f"Model: {self.cfg['model'].split('/')[-1]}", None, enabled=False),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit", lambda *_: self.quit()),
        )
        self.tray = pystray.Icon("murmur", icons.idle(), APP_NAME, menu)
        self.tray.run_detached()

    def _set_icon(self, image) -> None:  # noqa: ANN001
        if self.tray:
            self.tray.icon = image

    # ───── status window (thread-safe entry points) ─────

    def _ensure_status(self) -> Optional[StatusWindow]:
        if not self.cfg.get("show_status_window", True) or not self.root:
            return None
        try:
            if self._status_window is None or not self._status_window.winfo_exists():
                self._status_window = StatusWindow(
                    self.root,
                    animation=self.cfg.get("preview_animation", "typewriter_word"),
                )
                log.debug("StatusWindow created")
            else:
                # Apply animation setting in case it was changed via Settings.
                self._status_window.set_animation(
                    self.cfg.get("preview_animation", "typewriter_word")
                )
            return self._status_window
        except Exception:
            log.exception("StatusWindow creation failed")
            return None

    def show_status(self, stage: str, sub: str = "") -> None:
        if not self.root:
            return
        self._last_stage = stage
        def update():
            sw = self._ensure_status()
            if sw is not None:
                sw.set_stage(stage, sub)
            if stage in ("recording", "listening"):
                self._start_level_polling()
            else:
                self._stop_level_polling()
        try:
            self.root.after_idle(update)
        except Exception:
            pass

    def show_preview(self, text: str) -> None:
        if not self.root:
            return
        self._last_stage = "done"
        def update():
            sw = self._ensure_status()
            if sw is not None:
                sw.set_stage("done", text or "(no speech)")
            self._stop_level_polling()
        try:
            self.root.after_idle(update)
        except Exception:
            pass

    def hide_status(self) -> None:
        if not self.root:
            return
        def hide():
            self._stop_level_polling()
            try:
                if self._status_window is not None and self._status_window.winfo_exists():
                    self._status_window.hide()
            except Exception:
                pass
        try:
            self.root.after_idle(hide)
        except Exception:
            pass

    # ───── level polling (main-thread timer) ─────

    def _start_level_polling(self) -> None:
        if not self.root or self._level_poll_after is not None:
            return
        self._level_poll_tick()

    def _stop_level_polling(self) -> None:
        if self._level_poll_after is not None and self.root:
            try:
                self.root.after_cancel(self._level_poll_after)
            except Exception:
                pass
            self._level_poll_after = None

    def _level_poll_tick(self) -> None:
        if not self.root:
            return
        try:
            if self._status_window is not None and self._status_window.winfo_exists():
                dbfs = self.recorder.current_level_dbfs(window_ms=80)
                self._status_window.set_level(dbfs)
        except Exception:
            pass
        self._level_poll_after = self.root.after(60, self._level_poll_tick)

    # ───── settings (main-thread) ─────

    def open_settings(self) -> None:
        if not self.root:
            return
        def show():
            import settings_window  # noqa: PLC0415
            if self._settings_window is not None:
                try:
                    if self._settings_window.winfo_exists():
                        self._settings_window.lift()
                        self._settings_window.focus_force()
                        return
                except Exception:
                    pass
            self._settings_window = settings_window.SettingsWindow(
                self.root, self.cfg, on_save=self._on_settings_save,
            )
        self.root.after_idle(show)

    def _on_settings_save(self, new_cfg: dict) -> None:
        cfg_mod.save(new_cfg)
        self.reload_config()
        self._settings_window = None

    def reload_config(self) -> None:
        was_in_session = self._session_stop is not None
        self._end_session()
        self.cfg = cfg_mod.load()
        log.info(
            "Config reloaded — mode=%s hotkey=%s model=%s server=%s",
            self._mode(), self.cfg["hotkey"], self.cfg["model"], cfg_mod.server_url(self.cfg),
        )
        self.recorder = rec_mod.Recorder(
            sample_rate=int(self.cfg["sample_rate"]),
            device=self._resolve_device(),
        )
        self._start_hotkey()
        if self.tray:
            self.tray.update_menu()
        if was_in_session:
            log.info("(active session was cancelled by config reload)")

    def quit(self) -> None:
        if self.root:
            self.root.after_idle(self.root.quit)

    # ───── hotkey wiring ─────

    def _start_hotkey(self) -> None:
        if self.hotkey:
            self.hotkey.stop()
        try:
            self.hotkey = HotkeyListener(
                self.cfg["hotkey"],
                on_activate=_trap("on_ptt_down", self._on_ptt_down),
                on_deactivate=_trap("on_ptt_up", self._on_ptt_up),
            )
            self.hotkey.start()
            log.info("Hotkey listener armed on %r", self.cfg["hotkey"])
        except Exception:
            log.exception("Failed to start hotkey listener for combo %r", self.cfg["hotkey"])

    def _on_ptt_down(self) -> None:
        mode = self._mode()
        log.info("PTT down (mode=%s)", mode)
        if mode == "hold_to_record":
            self._begin_hold()
        else:
            with self._session_lock:
                if self._session_stop is not None:
                    self._end_session_locked()
                else:
                    self._start_session_locked(mode)

    def _on_ptt_up(self) -> None:
        log.info("PTT up (mode=%s)", self._mode())
        if self._mode() == "hold_to_record":
            self._end_hold()

    # ───── streaming helpers ─────

    def _begin_streaming_async(self) -> None:
        """Wire the recorder's chunks into a buffer pipe right now, then
        connect to WhisperLive in the background. Audio is queued until the
        connection is ready — zero PTT latency. Returns immediately."""
        if not self._use_streaming():
            return
        rate = self.recorder.effective_rate or int(self.cfg.get("sample_rate", 16000))
        self._streaming_pipe = AudioPipe(rate)
        self._streaming_pipe.start()
        self._streaming_listener = self._streaming_pipe.feed
        self.recorder.add_listener(self._streaming_listener)
        self._streaming_connecting = True
        threading.Thread(
            target=self._connect_streaming_worker, name="ws-connect", daemon=True,
        ).start()

    def _connect_streaming_worker(self) -> None:
        t0 = time.time()
        try:
            cli = WhisperLiveClient(
                self._streaming_url(),
                language=self.cfg.get("language", "auto"),
                model=self.cfg.get("streaming_model", "small"),
                timeout=float(self.cfg.get("streaming_timeout_s", 30.0)),
                task=self.cfg.get("streaming_task", "transcribe"),
            )
            cli.set_on_text(self._on_streaming_text)
            cli.connect()
        except StreamingError as exc:
            dt = time.time() - t0
            log.warning("Streaming connect failed after %.2fs (%s) — batch fallback", dt, exc)
            if self._streaming_pipe is not None:
                self._streaming_pipe.cancel()
            self._streaming_connecting = False
            return
        dt = time.time() - t0
        # If recording already finished while we were connecting, drop the
        # connection cleanly — batch fallback already handled the audio.
        recording_active = self._hold_active or self._session_stop is not None
        if not recording_active:
            log.info("Streaming connected in %.2fs but recording ended — discarding", dt)
            try:
                cli.close()
            except Exception:
                pass
            if self._streaming_pipe is not None:
                self._streaming_pipe.cancel()
            self._streaming_connecting = False
            return
        # Attach client to the pipe → buffered chunks flush in order, future
        # chunks stream live.
        self._streaming_client = cli
        if self._streaming_pipe is not None:
            self._streaming_pipe.set_client(cli)
        self._streaming_connecting = False
        log.info("Streaming connected in %.2fs — pipe drained, live", dt)
        # Update status sub-text on Tk thread.
        if self.root:
            try:
                self.root.after_idle(
                    lambda: self._status_window
                    and self._status_window.set_preview("streaming live…")
                )
            except Exception:
                pass

    def _detach_stream_listener(self) -> None:
        if self._streaming_listener is not None:
            try:
                self.recorder.remove_listener(self._streaming_listener)
            except Exception:
                pass
            self._streaming_listener = None

    def _teardown_streaming(self) -> None:
        """Tear down both the pipe and the client (call after final text is
        fetched). Safe to call multiple times."""
        self._detach_stream_listener()
        if self._streaming_pipe is not None:
            try:
                self._streaming_pipe.stop()
            except Exception:
                pass
            self._streaming_pipe = None
        if self._streaming_client is not None:
            try:
                self._streaming_client.close()
            except Exception:
                pass
            self._streaming_client = None
        self._streaming_connecting = False

    def _on_streaming_text(self, text: str, is_final: bool) -> None:
        """Called from the WhisperLive recv thread — marshal into Tk thread."""
        if not text or not self.root:
            return
        def update():
            sw = self._ensure_status()
            if sw is not None:
                sw.set_preview(text)
        try:
            self.root.after_idle(update)
        except Exception:
            pass

    # ───── hold-to-record ─────

    def _begin_hold(self) -> None:
        if self._hold_active:
            return
        try:
            self.recorder.start()  # fast — does not wait for WS
            self._hold_active = True
            self._hold_start_ts = time.time()
            self._set_icon(icons.recording())
            using_stream = self._use_streaming()
            self.show_status("recording", "connecting…" if using_stream else "")
            if self.cfg.get("play_sounds", True):
                sounds.cue_start()
            log.info(
                "Recording started (hold, %s) — device=%s rate=%dHz",
                "streaming" if using_stream else "batch",
                self.recorder.effective_device, self.recorder.effective_rate,
            )
            # Connect to WhisperLive in parallel — audio is buffered meanwhile.
            self._begin_streaming_async()
        except Exception:
            log.exception("Failed to start recording")
            self._set_icon(icons.idle())
            self.hide_status()
            self._hold_active = False
            self._teardown_streaming()

    def _end_hold(self) -> None:
        if not self._hold_active:
            return
        self._hold_active = False
        elapsed_ms = (time.time() - self._hold_start_ts) * 1000
        threading.Thread(
            target=self._transcribe_and_inject,
            args=(elapsed_ms,),
            daemon=True,
        ).start()

    # ───── session-based modes (toggle / vad / continuous) ─────

    def _start_session_locked(self, mode: str) -> None:
        """Caller holds _session_lock."""
        self._session_stop = threading.Event()
        self._session_thread = threading.Thread(
            target=self._run_session, args=(mode, self._session_stop), daemon=True,
        )
        self._session_thread.start()

    def _end_session_locked(self) -> None:
        if self._session_stop is not None:
            self._session_stop.set()

    def _end_session(self) -> None:
        with self._session_lock:
            self._end_session_locked()

    def _run_session(self, mode: str, stop_event: threading.Event) -> None:
        log.info("Session started — mode=%s", mode)
        try:
            while not stop_event.is_set():
                try:
                    self.recorder.start()
                except Exception:
                    log.exception("recorder.start failed")
                    break

                self._set_icon(icons.recording())
                using_stream = self._use_streaming()
                if using_stream:
                    sub = "connecting…"
                elif mode == "press_to_toggle":
                    sub = "press hotkey to stop"
                else:
                    sub = ""
                self.show_status("recording", sub)
                if self.cfg.get("play_sounds", True):
                    sounds.cue_start()
                t_start = time.time()
                log.info(
                    "Recording started (%s, %s) — device=%s rate=%dHz",
                    mode, "streaming" if using_stream else "batch",
                    self.recorder.effective_device, self.recorder.effective_rate,
                )
                # WS connects in parallel; audio queued.
                self._begin_streaming_async()

                self._wait_for_stop_trigger(mode, stop_event)

                elapsed_ms = (time.time() - t_start) * 1000
                self._transcribe_and_inject(elapsed_ms)

                if mode != "continuous" or stop_event.is_set():
                    break

                self.show_status("listening", "speak again — press hotkey to end")
                # Tiny gap before re-arming the recorder.
                time.sleep(0.15)
        finally:
            with self._session_lock:
                self._session_stop = None
                self._session_thread = None
            self._set_icon(icons.idle())
            # Only force-hide if there's no preview/error to display — those
            # have their own short auto-hide.
            if self._last_stage not in ("done", "error"):
                self.hide_status()
            log.info("Session ended")

    def _wait_for_stop_trigger(self, mode: str, stop_event: threading.Event) -> None:
        """Block until the right end-of-recording signal arrives."""
        if mode == "press_to_toggle":
            stop_event.wait()
            return

        # VAD-driven modes.
        rate = self.recorder.effective_rate or int(self.cfg.get("sample_rate", 16000))
        vad = EnergyVAD(
            sample_rate=rate,
            silence_ms=int(self.cfg.get("vad_silence_ms", 700)),
            threshold_dbfs=float(self.cfg.get("vad_threshold_dbfs", -42.0)),
        )
        end_event = threading.Event()

        def on_chunk(chunk, _vad=vad, _end=end_event):
            if _vad.feed(chunk):
                _end.set()

        self.recorder.add_listener(on_chunk)
        try:
            while not end_event.is_set() and not stop_event.is_set():
                time.sleep(0.05)
        finally:
            self.recorder.remove_listener(on_chunk)

    # ───── transcribe + inject ─────

    def _transcribe_and_inject(self, elapsed_ms: float) -> None:
        try:
            # Detach the recorder listener so no new chunks are queued.
            self._detach_stream_listener()
            # Capture whether streaming was actually negotiated.
            had_client = self._streaming_client is not None
            connect_was_inflight = self._streaming_connecting

            # Flush the audio pipe so any buffered chunks finish sending
            # BEFORE we call cli.end() (otherwise the final text would miss
            # the last seconds of speech).
            if self._streaming_pipe is not None:
                self._streaming_pipe.stop(flush_timeout=2.0)

            wav, duration, dbfs = self.recorder.stop()
            self.show_status("transcribing", f"{duration:.1f}s @ {dbfs:.0f} dBFS")
            self._set_icon(icons.transcribing())
            if self.cfg.get("play_sounds", True):
                sounds.cue_stop()
            if not wav or elapsed_ms < self.cfg.get("min_record_ms", 250):
                log.info("Skipped — too short (%.0f ms, %d bytes)", elapsed_ms, len(wav))
                self._teardown_streaming()
                return
            LAST_RECORDING.write_bytes(wav)
            log.info(
                "Audio: %.2fs, %.0f kB, level=%.1f dBFS  →  %s",
                duration, len(wav) / 1024, dbfs, LAST_RECORDING.name,
            )
            if dbfs < -50:
                log.warning(
                    "Very quiet input (%.1f dBFS). Whisper will likely hallucinate.",
                    dbfs,
                )

            # ── Get final text — from streaming if active, else batch HTTP.
            text = ""
            if had_client and self._streaming_client is not None:
                t0 = time.time()
                text = self._streaming_client.end()
                dt = time.time() - t0
                log.info("Streaming final in %.2fs: %r", dt, text[:400])
            else:
                if connect_was_inflight:
                    log.info("WS connect didn't complete in time — using batch")
                url = cfg_mod.server_url(self.cfg)
                log.info("Sending %.0f kB to %s …", len(wav) / 1024, url)
                t0 = time.time()
                text = tx.transcribe(
                    wav, server_url=url, model=self.cfg["model"], language=self.cfg["language"],
                )
                dt = time.time() - t0
                log.info("Batch got %d chars in %.2fs: %r", len(text), dt, text[:400])

            if text:
                self.show_preview(text)
                injector.inject(text, method=self.cfg["inject_method"])
            else:
                self.show_preview("(no speech detected)")
        except tx.TranscribeError as exc:
            log.error("Transcribe failed: %s", exc)
            self.show_status("error", str(exc)[:80])
        except Exception:
            log.error("Unexpected error:\n%s", traceback.format_exc())
            self.show_status("error", "unexpected error — see log")
        finally:
            self._teardown_streaming()
            # For hold mode, end-of-cycle. For session modes, the loop re-arms.
            if self._session_stop is None:
                self._set_icon(icons.idle())


def main() -> int:
    try:
        App().run()
        return 0
    except Exception:
        log.exception("Fatal error")
        return 1


if __name__ == "__main__":
    sys.exit(main())
