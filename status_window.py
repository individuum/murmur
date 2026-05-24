"""Floating status overlay — multi-line streaming transcript with
configurable per-character animations.

    ┌──────────────────────────────────────────────────────┐
    │  ●  Recording                            0.8 s       │
    │  ────────────────────────────────────────────────    │
    │  ▮▮▮▮▮▯▯▯▯▯▯▯▯▯▯▯▯▯▯▯▯▯▯▯▯▯▯▯▯▯                       │
    │                                                      │
    │  Hallo, dies ist ein Test der Live-                 │
    │  Transkription mit dem neuen schicken               │
    │  Status-Overlay, der jetzt mehrere Zeilen           │
    │  anzeigt und automatisch scrollt während neuer ▌    │
    │                                                      │
    └──────────────────────────────────────────────────────┘
"""
from __future__ import annotations

import collections
import math
from typing import Deque

import customtkinter as ctk

import monitor
import theme as T


WIDTH = 580
INITIAL_HEIGHT = 140     # compact at pop-in, grows as transcript fills
MIN_HEIGHT = 140
MAX_HEIGHT = 720         # capped at ~22 visible text lines

# Approximate pixel cost per text line at body(16). The font is logical-size;
# CTk widget scaling multiplies for physical px, but our geometry() call also
# goes through window scaling, so units stay self-consistent.
LINE_HEIGHT_PX = 26
CHROME_HEIGHT_PX = 110   # top bar + hairline + level meter + paddings

# Pop-in animation tuning
POP_IN_STEPS = 12     # frames
POP_IN_INTERVAL_MS = 18
POP_IN_SLIDE_PX = 18  # logical pixels, eases up into final position


STAGE_COLOR = {
    "idle":          T.TEXT_TERTIARY,
    "armed":         T.ACCENT,
    "listening":     T.LISTEN,
    "recording":     T.DANGER,
    "transcribing":  T.WARNING,
    "done":          T.SUCCESS,
    "error":         T.DANGER,
}
STAGE_LABEL = {
    "idle":          "Idle",
    "armed":         "Armed",
    "listening":     "Listening",
    "recording":     "Recording",
    "transcribing":  "Transcribing",
    "done":          "Done",
    "error":         "Error",
}

# Animation modes
ANIM_INSTANT          = "instant"
ANIM_TYPEWRITER_CHAR  = "typewriter_char"
ANIM_TYPEWRITER_WORD  = "typewriter_word"
ANIM_FADE             = "fade"
ANIM_CURSOR           = "cursor"


class StatusWindow(ctk.CTkToplevel):
    def __init__(self, master, animation: str = ANIM_TYPEWRITER_WORD) -> None:
        super().__init__(master)
        self.title("Murmur")
        self.overrideredirect(True)
        self.attributes("-topmost", True)
        self.attributes("-alpha", 0.97)
        self.resizable(False, False)
        # Trick to get *real* rounded corners on Windows:
        # set the Toplevel's bg to a unique "magic" color and ask the WM to
        # treat that color as fully transparent. The rounded inner CTkFrame
        # then renders only its interior, with everything outside its arc
        # invisible (and click-through).
        self._TRANSPARENT_CHROMA = "#010203"
        self.configure(fg_color=self._TRANSPARENT_CHROMA)
        try:
            self.config(bg=self._TRANSPARENT_CHROMA)
            self.wm_attributes("-transparentcolor", self._TRANSPARENT_CHROMA)
        except Exception:
            pass

        self._current_height = INITIAL_HEIGHT
        self.geometry(f"{WIDTH}x{INITIAL_HEIGHT}+0+0")

        self._animation = animation

        outer = ctk.CTkFrame(self, fg_color=T.BG_CARD, corner_radius=T.R_XL)
        outer.pack(fill="both", expand=True)

        # Top row: LED + stage + timer
        top = ctk.CTkFrame(outer, fg_color="transparent", height=28)
        top.pack(fill="x", padx=18, pady=(14, 0))
        top.pack_propagate(False)

        self.led = ctk.CTkLabel(
            top, text="●", text_color=STAGE_COLOR["idle"],
            font=T.title(22, "bold"), width=20,
        )
        self.led.pack(side="left")

        self.label = ctk.CTkLabel(
            top, text=STAGE_LABEL["idle"], text_color=T.TEXT_PRIMARY,
            font=T.title(16, "bold"), anchor="w",
        )
        self.label.pack(side="left", padx=(12, 0))

        self.timer = ctk.CTkLabel(
            top, text="", text_color=T.TEXT_SECONDARY,
            font=T.body(13), anchor="e", width=90,
        )
        self.timer.pack(side="right")

        # Hairline
        ctk.CTkFrame(outer, fg_color=T.SEPARATOR, height=1).pack(
            fill="x", padx=18, pady=(10, 10),
        )
        # Level meter
        self.level = ctk.CTkProgressBar(
            outer, height=4, corner_radius=2,
            progress_color=T.ACCENT, fg_color=T.BG_INPUT,
        )
        self.level.pack(fill="x", padx=18)
        self.level.set(0.0)

        # Multi-line scrolling transcript — bigger font, comfortable line height
        # Scrollbars enabled so user can scroll back through what was said.
        self.text = ctk.CTkTextbox(
            outer, fg_color=T.BG_CARD, text_color=T.TEXT_PRIMARY,
            border_width=0, wrap="word", height=180,
            font=T.body(16), activate_scrollbars=True,
            scrollbar_button_color=T.BG_HOVER,
            scrollbar_button_hover_color=T.SEPARATOR,
        )
        self.text.pack(fill="both", expand=True, padx=16, pady=(14, 16))
        self.text.configure(state="disabled")
        # Detect user-initiated scrolls so we stop auto-scrolling to end
        # while they're reading earlier content.
        self.text._textbox.bind("<MouseWheel>", self._on_user_scroll, add=True)
        self.text._textbox.bind("<Button-4>", self._on_user_scroll, add=True)
        self.text._textbox.bind("<Button-5>", self._on_user_scroll, add=True)
        # Belt-and-suspenders on the underlying tk.Text — wrap and spacing.
        try:
            self.text._textbox.configure(
                wrap="word",
                spacing1=2,    # space above each logical line
                spacing2=2,    # between wrapped sub-lines
                spacing3=4,    # below each logical line
                padx=2, pady=2,
            )
        except Exception:
            pass

        # Configure colour tags for animation effects.
        for shade, color in (
            ("dim0", T.TEXT_TERTIARY),
            ("dim1", "#7a7a82"),
            ("dim2", "#8b8b92"),
            ("dim3", T.TEXT_SECONDARY),
            ("bright", T.TEXT_PRIMARY),
        ):
            self.text._textbox.tag_configure(shade, foreground=color)
        # Cursor tag — used for the blinking caret in cursor mode.
        self.text._textbox.tag_configure("cursor", foreground=T.ACCENT)

        # State
        self._stage = "idle"
        self._t0 = 0.0
        self._anim_after = None
        self._auto_hide_after = None
        self._phase = 0

        # Pop-in animation state
        self._pop_in_step = 0
        self._pop_in_after = None
        self._target_x = 0
        self._target_y = 0
        self._final_alpha = 0.97

        # Resize is driven by a periodic ticker that runs only while the
        # window is visible — independent of text-update callbacks so it
        # also fires during long typewriter animations.
        self._resize_loop_after = None

        # Auto-scroll only when the user is already at the bottom. As soon as
        # they scroll up, we stop dragging them back to the end on each new
        # token. Resets when they scroll back to the bottom or a new cycle
        # starts.
        self._user_at_bottom = True

        # Animation state
        self._displayed_text = ""
        self._reveal_queue: Deque[str] = collections.deque()
        self._reveal_after = None
        self._fade_tags: Deque[tuple[str, int]] = collections.deque()
        self._fade_after = None
        self._cursor_visible = False
        self._cursor_after = None

        self.withdraw()

    # ───── public API ─────

    def set_animation(self, mode: str) -> None:
        self._animation = mode if mode in (
            ANIM_INSTANT, ANIM_TYPEWRITER_CHAR, ANIM_TYPEWRITER_WORD,
            ANIM_FADE, ANIM_CURSOR,
        ) else ANIM_TYPEWRITER_WORD

    def set_stage(self, stage: str, sub: str = "", reset_timer: bool = False) -> None:
        import time
        self._cancel_auto_hide()
        prev = self._stage
        self._stage = stage

        color = STAGE_COLOR.get(stage, STAGE_COLOR["idle"])
        self.led.configure(text_color=color)
        self.label.configure(text=STAGE_LABEL.get(stage, stage))
        self.level.configure(progress_color=color)

        if reset_timer or prev in ("idle", "done", "error"):
            self._t0 = time.time()
            # New cycle — wipe text buffer and animation queues, shrink back
            # to the initial compact size, reset scroll behavior.
            self._wipe_text()
            self._current_height = INITIAL_HEIGHT
            self._user_at_bottom = True

        if stage == "recording":
            self.level.set(0.0)
            if sub:
                self._set_text_immediate(sub, italic=True)
        elif stage == "transcribing":
            if sub:
                self._set_text_immediate(sub, italic=True)
        elif stage == "listening":
            self.level.set(0.0)
            if sub:
                self._set_text_immediate(sub, italic=True)
        elif stage == "done":
            self.level.set(1.0)
            if sub:
                self.set_preview(sub)
        elif stage == "error":
            self.level.set(0.0)
            if sub:
                self._set_text_immediate(sub, italic=True)

        # If we were hidden, play the pop-in animation; if already visible,
        # just re-anchor and continue.
        was_visible = self.winfo_viewable()
        self._reposition_to_active_monitor()
        if not was_visible:
            self._play_pop_in()
        else:
            self.deiconify()
            self.lift()
        self._start_anim()
        self._start_resize_loop()

        if stage in ("done", "error"):
            self._auto_hide_after = self.after(2400, self.hide)

    def set_level(self, dbfs: float) -> None:
        if self._stage not in ("recording", "listening"):
            return
        clamped = max(-60.0, min(-5.0, dbfs))
        self.level.set((clamped + 60.0) / 55.0)

    def set_preview(self, text: str) -> None:
        """Stream a (possibly growing) transcript into the text area.

        Behavior:
        - No-op if the new text matches the displayed text (modulo trailing
          whitespace) → eliminates spurious refreshes from the server.
        - Pure growth (new suffix appended) → animate per the chosen mode.
        - Minor revision (common prefix > 50%) → snap prefix instantly,
          animate the new suffix.
        - Major rewrite (Whisper changed its mind, common < 50%) → snap to
          the whole new text instantly with no animation. Re-typing from
          scratch would look like the window is constantly rebuilding.
        """
        if not text:
            return
        text = text.strip().replace("\r", "")
        if text.rstrip() == self._displayed_text.rstrip():
            return

        common = 0
        for a, b in zip(self._displayed_text, text):
            if a == b:
                common += 1
            else:
                break

        diverged = common < len(self._displayed_text)
        new_suffix = text[common:]
        old_len = len(self._displayed_text)

        if self._animation == ANIM_INSTANT:
            self._set_text_immediate(text)
            return

        # Heuristic: if more than half of the old text was replaced AND the
        # new content has any meaningful length, treat it as a major rewrite
        # and just snap. Animating word-by-word from scratch on each rewrite
        # is what makes the window feel like it's "rebuilding all the time".
        if diverged and old_len > 8 and common < max(old_len, len(text)) * 0.5:
            self._set_text_immediate(text)
            return

        if diverged:
            # Minor revision — snap the prefix, animate the new suffix.
            self._stop_typewriter()
            self.text.configure(state="normal")
            try:
                idx = f"1.0+{common}c"
                self.text.delete(idx, "end")
            except Exception:
                self.text.delete("1.0", "end")
                self.text.insert("end", text[:common])
            self.text.configure(state="disabled")
            self._displayed_text = text[:common]

        if not new_suffix:
            return

        if self._animation == ANIM_TYPEWRITER_CHAR:
            self._enqueue_reveal(list(new_suffix), word_mode=False)
        elif self._animation == ANIM_TYPEWRITER_WORD:
            self._enqueue_reveal(self._tokenize_words(new_suffix), word_mode=True)
        elif self._animation == ANIM_FADE:
            self._fade_append(new_suffix)
            self._displayed_text = text
            self._auto_scroll()
        elif self._animation == ANIM_CURSOR:
            self._set_text_immediate(text, with_cursor=True)
        else:
            self._set_text_immediate(text)

    def hide(self) -> None:
        self._cancel_auto_hide()
        self._cancel_pop_in()
        self._stop_anim()
        self._stop_typewriter()
        self._stop_cursor()
        self._stop_resize_loop()
        self._stage = "idle"
        self.withdraw()

    # ───── internals — text manipulation ─────

    def _wipe_text(self) -> None:
        self._stop_typewriter()
        self._stop_cursor()
        self._displayed_text = ""
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        self.text.configure(state="disabled")

    def _set_text_immediate(self, text: str, italic: bool = False, with_cursor: bool = False) -> None:
        self._stop_typewriter()
        self._stop_cursor()
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        self.text.insert("end", text)
        self.text.configure(state="disabled")
        self._displayed_text = text
        self._auto_scroll()
        if with_cursor:
            self._start_cursor()

    def _enqueue_reveal(self, tokens: list, word_mode: bool) -> None:
        for t in tokens:
            self._reveal_queue.append(t)
        self._reveal_word_mode = word_mode
        if self._reveal_after is None:
            self._reveal_tick()

    def _reveal_tick(self) -> None:
        if not self._reveal_queue:
            self._reveal_after = None
            return
        # Adaptive speed — if the queue is large we burn through it faster
        # so the reveal stays close to the actual stream.
        n = len(self._reveal_queue)
        if self._reveal_word_mode:
            # Words: emit 1 normally, 2-3 if backlog.
            take = 1 if n < 6 else (2 if n < 18 else 4)
            delay = 70 if n < 4 else 35
        else:
            # Chars: emit 1 normally, more if backlog grows.
            take = 1 if n < 20 else (3 if n < 60 else 6)
            delay = 28 if n < 20 else 14

        emit = "".join(self._reveal_queue.popleft() for _ in range(min(take, n)))
        self.text.configure(state="normal")
        self.text.insert("end", emit)
        self.text.configure(state="disabled")
        self._displayed_text += emit
        self._auto_scroll()
        self._reveal_after = self.after(delay, self._reveal_tick)

    def _stop_typewriter(self) -> None:
        if self._reveal_after is not None:
            try:
                self.after_cancel(self._reveal_after)
            except Exception:
                pass
            self._reveal_after = None
        self._reveal_queue.clear()

    def _fade_append(self, suffix: str) -> None:
        # Insert with shading tag and step it brighter over time.
        tag = f"fade_{len(self._fade_tags)}"
        self.text._textbox.tag_configure(tag, foreground=T.TEXT_TERTIARY)
        self.text.configure(state="normal")
        start = self.text.index("end-1c")
        self.text.insert("end", suffix)
        end = self.text.index("end-1c")
        self.text._textbox.tag_add(tag, start, end)
        self.text.configure(state="disabled")
        self._fade_tags.append((tag, 0))
        if self._fade_after is None:
            self._fade_tick()

    def _fade_tick(self) -> None:
        if not self._fade_tags:
            self._fade_after = None
            return
        # Brighten each pending tag a step.
        shades = (T.TEXT_TERTIARY, "#7a7a82", "#8b8b92", T.TEXT_SECONDARY, T.TEXT_PRIMARY)
        keep: Deque[tuple[str, int]] = collections.deque()
        for tag, step in self._fade_tags:
            step += 1
            if step >= len(shades):
                try:
                    self.text._textbox.tag_configure(tag, foreground=T.TEXT_PRIMARY)
                except Exception:
                    pass
                continue
            try:
                self.text._textbox.tag_configure(tag, foreground=shades[step])
            except Exception:
                pass
            keep.append((tag, step))
        self._fade_tags = keep
        self._fade_after = self.after(55, self._fade_tick)

    def _start_cursor(self) -> None:
        if self._cursor_after is not None:
            return
        self._cursor_tick()

    def _stop_cursor(self) -> None:
        if self._cursor_after is not None:
            try:
                self.after_cancel(self._cursor_after)
            except Exception:
                pass
            self._cursor_after = None
        # Remove cursor glyph if present.
        try:
            self.text.configure(state="normal")
            last = self.text.get("end-2c", "end-1c")
            if last == "▌":
                self.text.delete("end-2c", "end-1c")
            self.text.configure(state="disabled")
        except Exception:
            pass
        self._cursor_visible = False

    def _cursor_tick(self) -> None:
        try:
            self.text.configure(state="normal")
            last = self.text.get("end-2c", "end-1c")
            if last == "▌":
                self.text.delete("end-2c", "end-1c")
                self._cursor_visible = False
            else:
                start = self.text.index("end-1c")
                self.text.insert("end", "▌")
                end = self.text.index("end-1c")
                self.text._textbox.tag_add("cursor", start, end)
                self._cursor_visible = True
            self.text.configure(state="disabled")
            self._auto_scroll()
        except Exception:
            pass
        self._cursor_after = self.after(520, self._cursor_tick)

    def _on_user_scroll(self, _event=None) -> None:
        # Re-evaluate _user_at_bottom shortly after the wheel event has updated
        # the view (Tk dispatches the scroll after this handler).
        self.after(30, self._check_at_bottom)

    def _check_at_bottom(self) -> None:
        try:
            _top, bottom = self.text._textbox.yview()
            self._user_at_bottom = bottom > 0.97
        except Exception:
            self._user_at_bottom = True

    def _auto_scroll(self) -> None:
        # Only drag the view to the end if the user is already there.
        try:
            _top, bottom = self.text._textbox.yview()
            if bottom > 0.97 or self._user_at_bottom:
                self.text.see("end")
                self._user_at_bottom = True
        except Exception:
            pass

    def _start_resize_loop(self) -> None:
        if self._resize_loop_after is None:
            self._resize_loop_tick()

    def _stop_resize_loop(self) -> None:
        if self._resize_loop_after is not None:
            try:
                self.after_cancel(self._resize_loop_after)
            except Exception:
                pass
            self._resize_loop_after = None

    def _resize_loop_tick(self) -> None:
        """Runs every ~180 ms while the window is visible. Adjusts height to
        fit the current text content. Independent of text-update callbacks
        so it keeps working through long typewriter animations."""
        self._apply_resize()
        self._resize_loop_after = self.after(180, self._resize_loop_tick)

    def _apply_resize(self) -> None:
        try:
            res = self.text._textbox.count("1.0", "end-1c", "displaylines")
            lines = int(res[0]) if res else 1
        except Exception:
            lines = 1
        lines = max(1, lines)
        desired = CHROME_HEIGHT_PX + lines * LINE_HEIGHT_PX + 16
        new_h = max(MIN_HEIGHT, min(MAX_HEIGHT, desired))
        # Small threshold so growth is smooth but micro-jitter is suppressed.
        if abs(new_h - self._current_height) < 12:
            return
        self._current_height = new_h
        try:
            self.geometry(f"{WIDTH}x{new_h}+{self._target_x}+{self._target_y}")
        except Exception:
            pass

    # ───── internals — stage timer & animation ─────

    def _cancel_auto_hide(self) -> None:
        if self._auto_hide_after is not None:
            try:
                self.after_cancel(self._auto_hide_after)
            except Exception:
                pass
            self._auto_hide_after = None

    def _start_anim(self) -> None:
        if self._anim_after is None:
            self._tick()

    def _stop_anim(self) -> None:
        if self._anim_after is not None:
            try:
                self.after_cancel(self._anim_after)
            except Exception:
                pass
            self._anim_after = None
        try:
            self.attributes("-alpha", 0.97)
        except Exception:
            pass

    def _tick(self) -> None:
        import time
        if self._stage in ("recording", "transcribing", "listening"):
            self.timer.configure(text=f"{time.time() - self._t0:0.1f} s")
        elif self._stage == "done":
            self.timer.configure(text="✓")
        elif self._stage == "error":
            self.timer.configure(text="⚠")
        else:
            self.timer.configure(text="")

        self._phase = (self._phase + 1) % 240
        # Don't touch alpha while the pop-in animation owns it.
        if self._pop_in_after is None:
            alpha = 0.94 + 0.03 * (0.5 + 0.5 * math.sin(self._phase / 18.0))
            try:
                self.attributes("-alpha", alpha)
            except Exception:
                pass

        if self._stage == "transcribing":
            t = (self._phase % 60) / 60.0
            self.level.set(0.15 + 0.85 * (1.0 - abs(2.0 * t - 1.0)))

        self._anim_after = self.after(50, self._tick)

    def _reposition_to_active_monitor(self) -> None:
        """Re-center horizontally on the active monitor; anchor the TOP edge
        so the window grows downward as more transcript arrives (instead of
        jumping around)."""
        x, y, w, h = monitor.active_work_area()
        self._target_x = x + (w - WIDTH) // 2
        # Anchor at roughly screen-center for INITIAL_HEIGHT, then grow down.
        self._target_y = y + (h - INITIAL_HEIGHT) // 2
        try:
            self.geometry(f"{WIDTH}x{self._current_height}+{self._target_x}+{self._target_y}")
        except Exception:
            pass

    # ───── pop-in animation ─────

    def _cancel_pop_in(self) -> None:
        if self._pop_in_after is not None:
            try:
                self.after_cancel(self._pop_in_after)
            except Exception:
                pass
            self._pop_in_after = None

    def _play_pop_in(self) -> None:
        """Fade alpha in from 0 → final_alpha and slide down ~18px with
        ease-out cubic. ~220 ms total."""
        self._cancel_pop_in()
        self._pop_in_step = 0
        try:
            self.attributes("-alpha", 0.0)
        except Exception:
            pass
        # Start the window POP_IN_SLIDE_PX above its target.
        try:
            self.geometry(
                f"{WIDTH}x{self._current_height}+{self._target_x}+{self._target_y - POP_IN_SLIDE_PX}"
            )
        except Exception:
            pass
        self.deiconify()
        self.lift()
        self._pop_in_tick()

    def _pop_in_tick(self) -> None:
        n = POP_IN_STEPS
        i = self._pop_in_step
        if i > n:
            self._pop_in_after = None
            return
        t = i / n
        # Ease-out cubic: 1 - (1-t)^3 → fast start, settles smoothly.
        eased = 1.0 - (1.0 - t) ** 3
        alpha = self._final_alpha * eased
        y = self._target_y - int(POP_IN_SLIDE_PX * (1.0 - eased))
        try:
            self.attributes("-alpha", alpha)
            self.geometry(f"{WIDTH}x{self._current_height}+{self._target_x}+{y}")
        except Exception:
            pass
        self._pop_in_step += 1
        self._pop_in_after = self.after(POP_IN_INTERVAL_MS, self._pop_in_tick)

    # ───── tokenizer ─────

    @staticmethod
    def _tokenize_words(s: str) -> list:
        """Split into word tokens preserving inline whitespace."""
        out, buf = [], ""
        in_space = False
        for ch in s:
            is_space = ch.isspace()
            if in_space != is_space and buf:
                out.append(buf)
                buf = ""
            buf += ch
            in_space = is_space
        if buf:
            out.append(buf)
        return out
