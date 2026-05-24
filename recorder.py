from __future__ import annotations

import io
import threading
import wave
from typing import Optional

import numpy as np
import sounddevice as sd


class Recorder:
    """Push-to-talk audio recorder.

    Records 16-bit PCM mono. Prefers the configured sample rate, but if the
    selected device + host API doesn't support it (common on WASAPI), falls
    back to the device's native rate. The whisper server resamples internally,
    so any sane rate works.
    """

    def __init__(self, sample_rate: int = 16000, device: Optional[int | str] = None) -> None:
        self.preferred_rate = sample_rate
        self.effective_rate = sample_rate
        self.device = device
        self.effective_device: Optional[int | str] = device
        self._stream: Optional[sd.InputStream] = None
        self._chunks: list[np.ndarray] = []
        self._listeners: list = []
        self._lock = threading.Lock()

    def current_level_dbfs(self, window_ms: int = 60) -> float:
        """RMS over the last `window_ms` of audio, in dBFS. -120 if no audio yet."""
        with self._lock:
            if not self._chunks:
                return -120.0
            n_target = max(1, int(self.effective_rate * window_ms / 1000))
            collected: list[np.ndarray] = []
            total = 0
            for chunk in reversed(self._chunks):
                collected.append(chunk)
                total += len(chunk)
                if total >= n_target:
                    break
        audio = np.concatenate(list(reversed(collected)))[-n_target:]
        rms = float(np.sqrt(np.mean(audio.astype(np.float32) ** 2)))
        return 20.0 * np.log10(max(rms, 1.0) / 32768.0)

    def add_listener(self, fn) -> None:
        """Register a callback that receives every audio chunk as int16 mono."""
        with self._lock:
            self._listeners.append(fn)

    def remove_listener(self, fn) -> None:
        with self._lock:
            try:
                self._listeners.remove(fn)
            except ValueError:
                pass

    def _callback(self, indata, frames, time, status):  # noqa: ANN001
        if status:
            # Overflows etc. — non-fatal, we just keep recording.
            pass
        # indata is shape (frames, channels); we record mono so squeeze.
        # We MUST copy: sounddevice reuses the buffer after this callback
        # returns. Any reference still held (e.g. queued for a different
        # thread to consume) would read garbage. This was a silent bug that
        # caused WhisperLive to receive noise → empty transcripts.
        chunk = (indata[:, 0] if indata.ndim > 1 else indata).copy()
        with self._lock:
            self._chunks.append(chunk)
            listeners = list(self._listeners)
        for fn in listeners:
            try:
                fn(chunk)
            except Exception:
                pass

    def _device_info(self, idx: Optional[int]) -> dict:
        if idx is None:
            idx = sd.default.device[0]
        return sd.query_devices(idx)

    def _alternative_indices(self, original_idx: Optional[int]) -> list[int]:
        """Find other PortAudio indices for the same physical device name
        (e.g. the MME copy of a WASAPI mic). MME auto-resamples and tends to
        accept any sample rate, which makes it a great fallback."""
        if original_idx is None:
            return []
        try:
            target_name = sd.query_devices(original_idx)["name"].strip()
        except Exception:
            return []
        hostapis = sd.query_hostapis()
        # Try MME first (most permissive), then DirectSound, then WASAPI.
        priority = {"MME": 0, "Windows DirectSound": 1, "Windows WASAPI": 2}
        candidates = []
        for idx, dev in enumerate(sd.query_devices()):
            if idx == original_idx:
                continue
            if dev.get("max_input_channels", 0) <= 0:
                continue
            if dev["name"].strip() != target_name:
                continue
            host = hostapis[dev["hostapi"]]["name"]
            candidates.append((priority.get(host, 99), idx))
        candidates.sort()
        return [idx for _p, idx in candidates]

    def start(self) -> None:
        if self._stream is not None:
            return
        with self._lock:
            self._chunks.clear()

        # Build (device_idx, rate) attempt list — chosen device at its native
        # rate first, then same mic through other host APIs (MME = most forgiving),
        # then system default device as last resort.
        rates = [self.preferred_rate]
        try:
            rates.insert(0, int(self._device_info(self.device).get("default_samplerate") or 48000))
        except Exception:
            pass
        rates.extend([48000, 44100, 16000])
        rates = list(dict.fromkeys(rates))

        device_indices = [self.device]
        device_indices.extend(self._alternative_indices(self.device))
        if None not in device_indices:
            device_indices.append(None)

        last_err: Optional[Exception] = None
        for dev_idx in device_indices:
            for rate in rates:
                try:
                    stream = sd.InputStream(
                        samplerate=rate,
                        channels=1,
                        dtype="int16",
                        device=dev_idx,
                        callback=self._callback,
                        blocksize=0,
                    )
                    stream.start()
                    self._stream = stream
                    self.effective_rate = rate
                    self.effective_device = dev_idx
                    return
                except sd.PortAudioError as exc:
                    last_err = exc
                    # In case the constructor partially succeeded.
                    try:
                        stream.close()  # type: ignore[name-defined]
                    except Exception:
                        pass
        raise last_err if last_err else RuntimeError("No audio configuration worked")

    def stop(self) -> tuple[bytes, float, float]:
        """Stop recording and return (wav_bytes, duration_seconds, rms_dbfs).

        rms_dbfs is the audio level in dBFS — silence is around -96 dB,
        speech at a healthy level is typically -25 to -10 dB.
        """
        if self._stream is None:
            return b"", 0.0, -120.0
        try:
            self._stream.stop()
            self._stream.close()
        finally:
            self._stream = None
        with self._lock:
            if not self._chunks:
                return b"", 0.0, -120.0
            audio = np.concatenate(self._chunks, axis=0)
        duration = len(audio) / float(self.effective_rate)
        rms = float(np.sqrt(np.mean(audio.astype(np.float32) ** 2)))
        # 32768 is the int16 full scale.
        dbfs = 20.0 * np.log10(max(rms, 1.0) / 32768.0)
        return _to_wav(audio, self.effective_rate), duration, dbfs

    def is_recording(self) -> bool:
        return self._stream is not None


def _to_wav(samples: np.ndarray, sample_rate: int) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)  # int16
        wf.setframerate(sample_rate)
        wf.writeframes(samples.tobytes())
    return buf.getvalue()


_PREFERRED_HOSTS = ("Windows WASAPI", "Windows DirectSound", "MME")
_SKIP_PREFIXES = (
    "microsoft soundmapper",
    "primärer",
    "primary sound",
    "input (",  # PortAudio's WASAPI loopback entries for output devices
    "stereomix",
    "headphone",
)


def list_input_devices() -> list[tuple[int, str]]:
    """Enumerate physical input devices using a single host API.

    Picks the first available host API from WASAPI > DirectSound > MME — so
    each mic appears exactly once with its full (untruncated) name.
    """
    hostapis = sd.query_hostapis()
    chosen_api = None
    for pref in _PREFERRED_HOSTS:
        for api_idx, api in enumerate(hostapis):
            if api["name"] == pref:
                chosen_api = api_idx
                break
        if chosen_api is not None:
            break
    if chosen_api is None:
        chosen_api = sd.default.hostapi

    out: list[tuple[int, str]] = []
    seen: set[str] = set()
    for dev_idx, dev in enumerate(sd.query_devices()):
        if dev["hostapi"] != chosen_api:
            continue
        if dev.get("max_input_channels", 0) <= 0:
            continue
        name = dev["name"].strip()
        low = name.lower()
        if any(low.startswith(p) for p in _SKIP_PREFIXES):
            continue
        if name in seen:
            continue
        seen.add(name)
        out.append((dev_idx, name))
    return sorted(out, key=lambda t: t[1].lower())
