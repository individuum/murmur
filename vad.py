"""Simple energy-based voice activity detection.

Tracks RMS of incoming audio. Once it sees speech (RMS above threshold for at
least `min_speech_ms`), it watches for `silence_ms` of below-threshold audio to
declare end-of-speech. The first ~200 ms is also used to calibrate the noise
floor so a noisy environment doesn't immediately register as silence.
"""
from __future__ import annotations

import numpy as np


class EnergyVAD:
    def __init__(
        self,
        sample_rate: int,
        threshold_dbfs: float = -42.0,
        silence_ms: int = 700,
        min_speech_ms: int = 200,
        calibration_ms: int = 250,
    ) -> None:
        self.sample_rate = sample_rate
        self.silence_target = int(sample_rate * silence_ms / 1000)
        self.min_speech_target = int(sample_rate * min_speech_ms / 1000)
        self.calibration_target = int(sample_rate * calibration_ms / 1000)
        self._base_threshold = 10.0 ** (threshold_dbfs / 20.0) * 32768.0
        self.reset()

    def reset(self) -> None:
        self._speech_samples = 0
        self._silence_samples = 0
        self._has_speech = False
        self._calib_samples = 0
        self._calib_max_rms = 0.0
        self._threshold = self._base_threshold

    def feed(self, samples: np.ndarray) -> bool:
        """Feed an audio block (int16 mono). Returns True on end-of-speech."""
        n = len(samples)
        if n == 0:
            return False
        rms = float(np.sqrt(np.mean(samples.astype(np.float32) ** 2)))

        # Calibrate threshold for the first `calibration_ms` so we sit above
        # ambient noise — never let it drop below the configured base, though.
        if self._calib_samples < self.calibration_target:
            self._calib_samples += n
            self._calib_max_rms = max(self._calib_max_rms, rms)
            if self._calib_samples >= self.calibration_target:
                # Threshold = max(base, 1.6 × calibrated noise floor)
                self._threshold = max(self._base_threshold, self._calib_max_rms * 1.6)
            # During calibration, never declare end-of-speech.
            return False

        is_speech = rms > self._threshold
        if is_speech:
            self._speech_samples += n
            self._silence_samples = 0
            if self._speech_samples >= self.min_speech_target:
                self._has_speech = True
        else:
            if self._has_speech:
                self._silence_samples += n
                if self._silence_samples >= self.silence_target:
                    return True
        return False
