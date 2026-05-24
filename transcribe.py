from __future__ import annotations

import requests


class TranscribeError(RuntimeError):
    pass


def transcribe(
    wav_bytes: bytes,
    server_url: str,
    model: str,
    language: str = "auto",
    timeout: float = 60.0,
) -> str:
    """POST audio to an OpenAI-compatible /v1/audio/transcriptions endpoint."""
    url = server_url.rstrip("/") + "/v1/audio/transcriptions"
    files = {"file": ("speech.wav", wav_bytes, "audio/wav")}
    data = {"model": model, "response_format": "json"}
    if language and language.lower() != "auto":
        data["language"] = language
    try:
        resp = requests.post(url, files=files, data=data, timeout=timeout)
    except requests.RequestException as exc:
        raise TranscribeError(f"network error: {exc}") from exc
    if resp.status_code != 200:
        raise TranscribeError(f"server {resp.status_code}: {resp.text[:200]}")
    payload = resp.json()
    text = (payload.get("text") or "").strip()
    return text


def list_models(server_url: str, timeout: float = 5.0) -> list[str]:
    url = server_url.rstrip("/") + "/v1/models"
    try:
        resp = requests.get(url, timeout=timeout)
        resp.raise_for_status()
    except requests.RequestException:
        return []
    return sorted(m["id"] for m in resp.json().get("data", []))
