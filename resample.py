"""Lightweight audio resampling — numpy-only, no scipy dep.

Linear interpolation is "good enough" for speech transcription. Whisper
itself is quite robust to small spectral artifacts, and avoiding scipy
keeps the bundled .exe ~150 MB smaller.
"""
from __future__ import annotations

import numpy as np


def resample_int16(audio: np.ndarray, src_rate: int, dst_rate: int) -> np.ndarray:
    """Resample int16 mono audio to a different rate.

    Returns int16. Linear interpolation; mono only.
    """
    if src_rate == dst_rate or audio.size == 0:
        return audio.astype(np.int16, copy=False)
    if audio.ndim > 1:
        audio = audio[:, 0]
    n_in = audio.shape[0]
    n_out = int(round(n_in * dst_rate / src_rate))
    if n_out <= 1:
        return np.zeros(0, dtype=np.int16)
    x_old = np.arange(n_in, dtype=np.float64)
    x_new = np.linspace(0.0, n_in - 1, n_out, dtype=np.float64)
    out = np.interp(x_new, x_old, audio.astype(np.float64))
    return out.astype(np.int16)


def int16_to_float32(audio: np.ndarray) -> np.ndarray:
    """Convert int16 mono samples to float32 in [-1, 1]."""
    return (audio.astype(np.float32) / 32768.0)
