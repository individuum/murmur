"""WebSocket client for the WhisperLive streaming transcription server.

Protocol (simplified):

    1. Open WebSocket to ws://host:port
    2. Send JSON config: {uid, language, task, model, use_vad, ...}
    3. Server responds with {"message": "SERVER_READY"} (or WAIT if busy)
    4. Stream raw float32 mono 16 kHz PCM as binary frames
    5. Server pushes {"segments": [{start, end, text, completed}, ...]}
       at low latency as it decodes
    6. Client sends {"message": "END_OF_AUDIO"} to flush + close

Usage:

    cli = WhisperLiveClient("ws://127.0.0.1:9090", "de", "small")
    cli.set_on_text(lambda text, final: print(text))
    cli.connect()
    while recording:
        chunk = recorder.next_chunk()           # int16 mono 16k
        cli.send_audio_int16(chunk, src_rate)
    final_text = cli.end()
"""
from __future__ import annotations

import json
import logging
import queue
import threading
import time
import uuid
from typing import Callable, Optional

import numpy as np
import websocket  # websocket-client package

from resample import int16_to_float32, resample_int16


log = logging.getLogger("whisper-ptt.streaming")


class StreamingError(RuntimeError):
    pass


class AudioPipe:
    """FIFO queue between the recorder callback and a (possibly-not-yet-
    connected) WhisperLiveClient.

    The producer (recorder callback) calls `feed(chunk)` — never blocks.
    A dedicated drain thread waits for the client to be attached via
    `set_client(...)`, then forwards every queued + future chunk in order.

    This lets `recorder.start()` run instantly while the slow WS handshake
    happens in parallel; nothing is lost and nothing is reordered.
    """

    def __init__(self, src_rate: int) -> None:
        self._queue: queue.Queue = queue.Queue()
        self._client: Optional["WhisperLiveClient"] = None
        self._client_ready = threading.Event()
        self._stop = threading.Event()
        self._rate = src_rate
        self._thread = threading.Thread(
            target=self._run, name="AudioPipe", daemon=True,
        )

    def start(self) -> None:
        self._thread.start()

    def feed(self, chunk) -> None:  # noqa: ANN001
        if self._stop.is_set():
            return
        self._queue.put(chunk)

    def set_client(self, client: "WhisperLiveClient") -> None:
        self._client = client
        self._client_ready.set()

    def cancel(self) -> None:
        """Stop without flushing — used when the WS connect failed."""
        self._stop.set()
        self._client_ready.set()  # unblock the wait
        self._queue.put(None)

    def stop(self, flush_timeout: float = 0.5) -> None:
        """Signal end of input; wait briefly for the drain thread to finish."""
        self._queue.put(None)
        self._thread.join(timeout=flush_timeout)
        self._stop.set()

    def _run(self) -> None:
        # Wait for the client (or cancel).
        self._client_ready.wait()
        if self._stop.is_set() or self._client is None:
            return
        # Drain forever — chunks are queued FIFO so order is preserved.
        while True:
            chunk = self._queue.get()
            if chunk is None:
                return
            if self._stop.is_set():
                return
            try:
                self._client.send_audio_int16(chunk, self._rate)
            except Exception:
                # Best-effort; don't tear down on a single send error.
                pass


class WhisperLiveClient:
    TARGET_RATE = 16000  # WhisperLive expects 16 kHz mono float32

    def __init__(
        self,
        url: str,
        language: str = "auto",
        model: str = "small",
        timeout: float = 30.0,
        task: str = "transcribe",
    ) -> None:
        self.url = url
        self.language = None if (language or "auto").lower() == "auto" else language
        self.model = model
        self.timeout = timeout
        self.task = task if task in ("transcribe", "translate") else "transcribe"
        self.uid = uuid.uuid4().hex

        self._ws: Optional[websocket.WebSocket] = None
        self._recv_thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._closed = threading.Event()

        self._on_text: Optional[Callable[[str, bool], None]] = None
        self._segments: list[dict] = []
        self._last_text = ""
        self._last_final_text = ""

    # ───── public API ─────

    def set_on_text(self, fn: Callable[[str, bool], None]) -> None:
        """Register a callback for partial-text updates.
        Signature: fn(text, is_final_overall: bool)."""
        self._on_text = fn

    def connect(self) -> None:
        """Open WS, send config, block until SERVER_READY."""
        try:
            self._ws = websocket.create_connection(self.url, timeout=self.timeout)
        except Exception as exc:
            raise StreamingError(f"connect to {self.url} failed: {exc}") from exc

        config = {
            "uid": self.uid,
            "language": self.language,
            "task": self.task,
            "model": self.model,
            "use_vad": False,
            # Server-side limits — generous defaults.
            "max_clients": 4,
            "max_connection_time": 1800,
            "send_last_n_segments": 10,
            "no_speech_thresh": 0.45,
            "clip_audio": False,
            "same_output_threshold": 10,
        }
        try:
            self._ws.send(json.dumps(config))
        except Exception as exc:
            raise StreamingError(f"sending config failed: {exc}") from exc

        self._recv_thread = threading.Thread(target=self._recv_loop, daemon=True)
        self._recv_thread.start()

        if not self._ready.wait(timeout=self.timeout):
            self.close()
            raise StreamingError(f"server at {self.url} did not become ready in {self.timeout}s")

    def send_audio_int16(self, audio: np.ndarray, src_rate: int) -> None:
        """Send a chunk of int16 mono audio (resampled to 16k float32 internally)."""
        if self._ws is None or self._stop.is_set():
            return
        if audio is None or audio.size == 0:
            return
        if src_rate != self.TARGET_RATE:
            audio = resample_int16(audio, src_rate, self.TARGET_RATE)
        f32 = int16_to_float32(audio)
        try:
            self._ws.send(f32.tobytes(), websocket.ABNF.OPCODE_BINARY)
        except Exception as exc:
            log.warning("streaming send failed: %s", exc)
            self._stop.set()

    def end(self, flush_wait_ms: int = 3000) -> str:
        """Signal end-of-audio, wait for the server to flush final segments,
        then close. Returns the final assembled text.

        WhisperLive expects the literal bytes b"END_OF_AUDIO" as a binary
        frame — that's how it distinguishes end-of-stream from audio chunks.

        Patience policy:
        - If no text has arrived yet, wait the full `flush_wait_ms` (the
          server may still be processing buffered audio).
        - Once text starts arriving, exit 600 ms after the last update.
        """
        if self._ws is not None and not self._stop.is_set():
            try:
                self._ws.send(b"END_OF_AUDIO", websocket.ABNF.OPCODE_BINARY)
            except Exception:
                pass
        deadline = time.time() + flush_wait_ms / 1000.0
        last_seen = self._last_text
        last_change = time.time()
        while time.time() < deadline:
            if self._last_text != last_seen:
                last_seen = self._last_text
                last_change = time.time()
                continue
            have_text = bool(self._last_text)
            # Only allow early exit AFTER we've seen at least one update.
            if have_text and time.time() - last_change > 0.6:
                break
            # Connection died and we got SOMETHING — done. If nothing yet,
            # keep waiting up to deadline since the server may have queued
            # segments mid-flight.
            if self._closed.is_set() and have_text:
                break
            time.sleep(0.05)
        self.close()
        return self._last_text or self._last_final_text

    def close(self) -> None:
        self._stop.set()
        try:
            if self._ws is not None:
                self._ws.close()
        except Exception:
            pass
        self._ws = None
        self._closed.set()

    # ───── internals ─────

    def _recv_loop(self) -> None:
        try:
            while not self._stop.is_set() and self._ws is not None:
                try:
                    msg = self._ws.recv()
                except websocket.WebSocketConnectionClosedException:
                    break
                except Exception as exc:
                    log.debug("ws recv error: %s", exc)
                    break
                if not msg:
                    continue
                self._handle_message(msg)
        finally:
            self._stop.set()
            self._closed.set()

    def _handle_message(self, raw) -> None:
        if isinstance(raw, bytes):
            return
        try:
            data = json.loads(raw)
        except Exception:
            return
        if data.get("uid") and data["uid"] != self.uid:
            return  # Not our session.

        msg_kind = data.get("message") or data.get("status")
        if msg_kind == "SERVER_READY":
            self._ready.set()
            return
        if msg_kind == "WAIT":
            log.warning("Server full — waiting in queue")
            return
        if msg_kind in ("DISCONNECT", "DISCONNECTED"):
            self._stop.set()
            return

        # Segments payload — replace our segment cache + push update.
        segments = data.get("segments")
        if segments is not None:
            self._segments = segments
            text = self._assemble_text(segments)
            # Track "final" status: only true if every segment is completed.
            is_final = bool(segments) and all(s.get("completed") for s in segments)
            self._last_text = text
            if is_final:
                self._last_final_text = text
            if self._on_text is not None:
                try:
                    self._on_text(text, is_final)
                except Exception:
                    log.exception("on_text callback raised")

    @staticmethod
    def _assemble_text(segments: list[dict]) -> str:
        parts = []
        for seg in segments:
            t = (seg.get("text") or "").strip()
            if t:
                parts.append(t)
        return " ".join(parts).strip()
