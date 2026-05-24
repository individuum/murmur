from __future__ import annotations

import threading

import numpy as np
import sounddevice as sd


_SR = 44100


def _tone(freq_hz: float, ms: int, volume: float = 0.18) -> np.ndarray:
    n = int(_SR * ms / 1000)
    t = np.arange(n) / _SR
    wave = np.sin(2 * np.pi * freq_hz * t) * volume
    # 8 ms fade in/out so we don't click on the speakers.
    fade = int(_SR * 0.008)
    env = np.ones(n, dtype=np.float32)
    env[:fade] = np.linspace(0.0, 1.0, fade)
    env[-fade:] = np.linspace(1.0, 0.0, fade)
    return (wave * env).astype(np.float32)


# Pre-render so PTT triggers don't hit synth latency.
_START = _tone(880.0, 90)     # higher pitch — "armed"
_STOP = _tone(523.0, 110)     # lower pitch — "done"


def _play(buf: np.ndarray) -> None:
    try:
        sd.play(buf, samplerate=_SR, blocking=False)
    except Exception:
        # Audio cues are non-essential — never crash recording over a beep.
        pass


def cue_start() -> None:
    threading.Thread(target=_play, args=(_START,), daemon=True).start()


def cue_stop() -> None:
    threading.Thread(target=_play, args=(_STOP,), daemon=True).start()
