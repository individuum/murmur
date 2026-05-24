<div align="center">

# Murmur

**Polished push-to-talk dictation for Windows — fully local, GPU-streaming, real-time preview.**

A tray app that captures speech, streams it to a Whisper server on your own
machine, and pastes the result into whichever window has focus. The text
appears live in a floating overlay as you talk.

![idle](docs/preview-idle.png) ![recording](docs/preview-recording.png) ![transcribing](docs/preview-transcribing.png)

</div>

---

## Why another Whisper dictation tool?

The Windows / Whisper space has a dozen of these. Most fall into two camps:
either they're **server-style batch** (hold key → record WAV → upload → wait
seconds for text), or they're **slick commercial** (Wispr Flow, SuperWhisper)
that ship audio to someone else's cloud.

Murmur sits in the gap: **truly streaming** (text appears word-by-word as you
speak, courtesy of a self-hosted WhisperLive container), **truly local**
(audio never leaves your machine), and **the UI doesn't look like an enterprise
ERP form**. It's the dictation client I wanted and couldn't find.

## Features

- **Live streaming preview** — words appear as you speak in a floating overlay,
  via a self-hosted [WhisperLive](https://github.com/collabora/WhisperLive)
  container talking over WebSocket
- **Batch fallback** — if streaming is off or the WhisperLive container is
  down, falls back transparently to an OpenAI-compatible HTTP server
  ([faster-whisper-server](https://github.com/fedirz/faster-whisper-server)
  or [speaches](https://github.com/speaches-ai/speaches)) for a one-shot
  upload-and-transcribe
- **Four recording modes** — hold-to-record, press-to-toggle, voice-activity-
  detection (auto-stop on silence), and continuous dictation sessions
- **Pastes anywhere** — works in terminals (Claude Code, Windows Terminal,
  VS Code terminal) where UI-automation-based tools fail, because it just uses
  clipboard + Ctrl+V (with restore)
- **DPI-aware, per-monitor** — correct on 4K @ 150% and multi-monitor setups
- **5 preview animations** — instant, typewriter (word or char), fade, cursor
- **Dynamic resize** — overlay grows downward as the transcript fills,
  scrollable, scroll-back disables auto-scroll until you return to the bottom
- **Custom hotkey** — single keys (`f12`), combos (`ctrl+alt+space`), or
  side-specific modifiers (`right_alt`) — whatever doesn't fight a Windows
  shortcut
- **Bilingual / translation** — auto-detect language or force one, optionally
  translate to English (Whisper's `translate` task)
- **Single-instance lock** — never get two copies fighting over the hotkey
- **Single-file `.exe`** — no Python required to use; ~32 MB

## Quick start

You need:

- Windows 10 / 11
- Python 3.12 (only to build — the resulting `.exe` is standalone)
- A Whisper server reachable on your network. Easiest setup uses Podman or
  Docker — see [Server setup](#server-setup) below.

```cmd
git clone https://github.com/individuum/murmur.git
cd murmur
run.bat
```

First launch builds a virtualenv (~1 min), then the microphone icon shows in
your system tray. Right-click → Settings to configure.

To package as a standalone `.exe`:

```cmd
build.bat
```

Drops `dist\Murmur.exe` (~32 MB) — copy that anywhere, double-click to run.
Put a shortcut in `shell:startup` to launch with Windows.

## Server setup

Murmur talks to two backends and you only strictly need one. Streaming gives
the live-preview experience; batch is the no-frills HTTP fallback.

### Streaming server (recommended) — WhisperLive on GPU

```bash
podman run -d --name whisperlive --gpus all -p 9090:9090 \
  ghcr.io/collabora/whisperlive-gpu:latest
```

> Heads-up: the upstream `whisperlive-gpu:latest` image (as of mid-2026) ships
> CUDA 13 libraries but its bundled ctranslate2 wheel still links against
> CUDA 12. If you see `libcublas.so.12 not found` in the container logs,
> install the right libs and commit a fixed image:
>
> ```bash
> podman run -d --name whisperlive-tmp --gpus all ghcr.io/collabora/whisperlive-gpu:latest
> podman exec whisperlive-tmp pip install --no-cache-dir \
>     nvidia-cublas-cu12==12.4.5.8 nvidia-cudnn-cu12==9.1.0.70
> podman commit whisperlive-tmp localhost/whisperlive-cuda12fix:latest
> podman rm -f whisperlive-tmp
> # then run that fixed image instead:
> podman run -d --name whisperlive --gpus all -p 9090:9090 \
>     localhost/whisperlive-cuda12fix:latest
> ```

### Batch fallback — faster-whisper-server

```bash
podman run -d --name whisper -p 8000:8000 --gpus all \
  ghcr.io/fedirz/faster-whisper-server:0.6.0-rc.4-cuda
```

Murmur defaults to `127.0.0.1:9090` (streaming) and `127.0.0.1:8000` (batch).
Override in Settings if your server lives elsewhere.

## Configuration

All settings live in `config.json` next to the `.exe` (or next to `app.py` in
dev). Right-click tray → Settings for a GUI; or edit the JSON directly.

| Key                  | Default                                              | Notes                                                    |
| -------------------- | ---------------------------------------------------- | -------------------------------------------------------- |
| `hotkey`             | `f12`                                                | Push-to-talk key. See [Hotkey choices](#hotkey-choices)  |
| `record_mode`        | `continuous`                                         | `hold_to_record`/`press_to_toggle`/`voice_activity_detection`/`continuous` |
| `language`           | `auto`                                               | `de`, `en`, `auto`, …                                    |
| `streaming_enabled`  | `false`                                              | Use WhisperLive for live preview                         |
| `streaming_model`    | `small`                                              | `tiny`/`base`/`small`/`medium`/`large-v3`/`large-v3-turbo` |
| `streaming_task`     | `transcribe`                                         | `translate` always outputs English                       |
| `streaming_host`     | `127.0.0.1`                                          | WhisperLive host                                         |
| `streaming_port`     | `9090`                                               |                                                          |
| `model`              | `deepdml/faster-whisper-large-v3-turbo-ct2`          | Batch model (HuggingFace name)                           |
| `server_host`/`port` | `127.0.0.1` / `8000`                                 | Batch server                                             |
| `audio_device`       | `null`                                               | Mic index (Settings picks this for you)                  |
| `vad_silence_ms`     | `700`                                                | Pause length that ends an utterance in VAD / continuous  |
| `preview_animation`  | `typewriter_word`                                    | `instant`/`typewriter_word`/`typewriter_char`/`fade`/`cursor` |
| `inject_method`      | `paste`                                              | `paste` works in terminals; `keystrokes` for picky apps  |
| `show_status_window` | `true`                                               | Floating overlay during dictation                        |
| `ui_scale`           | `1.0`                                                | Multiplier on top of system DPI                          |

### Hotkey choices

Murmur sees the hotkey but doesn't suppress it — Windows still receives the
keystroke. Avoid combos that already trigger Windows shortcuts:

| Avoid                | Why                                |
| -------------------- | ---------------------------------- |
| `ctrl+alt+space`     | Opens Windows IME picker — flashes on every auto-repeat |
| `win+anything`       | Reserved by the Windows shell      |
| `ctrl+shift+esc`     | Task Manager                       |

Good choices: `f8` … `f12`, `right_alt`, `right_ctrl`, `caps_lock`.

## How it works

```
                        ┌─────────────────────┐
       press hotkey ───▶│  Recorder (sounddev)│──┐
                        └─────────────────────┘  │
                                                 ▼
                                  ┌──────────────────────────┐
                                  │  AudioPipe (queue, FIFO) │
                                  │  — starts buffering NOW  │
                                  └──────────────────────────┘
                                                 │
                                                 │  (in parallel)
                                                 ▼
                        ┌─────────────────────────────────────┐
                        │  WhisperLiveClient WebSocket connect │
                        │  → resample 48k → 16k float32       │
                        │  → flush buffered chunks in order   │
                        │  → stream live                      │
                        └─────────────────────────────────────┘
                                                 │
                                                 ▼
                                  ┌──────────────────────────┐
                                  │  partial transcripts     │
                                  │  → update status overlay │
                                  │  (typewriter / fade…)    │
                                  └──────────────────────────┘
                                                 │
                  release / VAD-silence / 2nd tap▼
                                  ┌──────────────────────────┐
                                  │  end()                   │
                                  │  → final text            │
                                  │  → paste at cursor       │
                                  └──────────────────────────┘
```

Streaming and batch share the same Recorder + paste path. The choice of
backend is per-recording; if the streaming connect doesn't return inside
the configured timeout, the captured WAV gets sent to the batch server
instead.

## Project layout

```
app.py             # main loop, tray, glue
recorder.py        # sounddevice capture + listener fan-out
streaming.py       # WhisperLive WebSocket client + AudioPipe
transcribe.py      # batch HTTP client (OpenAI-compatible)
vad.py             # energy-based voice activity detection
hotkey.py          # global PTT listener (pynput)
inject.py          # clipboard paste / keystroke output
status_window.py   # floating preview overlay
settings_window.py # CTk settings panel
icons.py           # tray icons (rendered at runtime)
theme.py           # design tokens (colors / fonts / spacing)
monitor.py         # per-monitor work area (Win32)
paths.py           # frozen-vs-dev path resolution
config.py          # JSON config load / save / migrate
sounds.py          # short start/stop beep tones
resample.py        # numpy-only 48k → 16k for streaming
build.bat / murmur.spec   # PyInstaller bundling
run.bat / run-debug.bat   # dev launchers
```

## Acknowledgements

- [OpenAI Whisper](https://github.com/openai/whisper) — the model
- [SYSTRAN/faster-whisper](https://github.com/SYSTRAN/faster-whisper) —
  CTranslate2 inference
- [collabora/WhisperLive](https://github.com/collabora/WhisperLive) —
  streaming server
- [fedirz/faster-whisper-server](https://github.com/fedirz/faster-whisper-server)
  — batch server
- [TomSchimansky/CustomTkinter](https://github.com/TomSchimansky/CustomTkinter)
  — UI toolkit

## License

[LGPL-3.0-or-later](./LICENSE) — see also [COPYING](./COPYING) (GPL-3.0, which LGPL incorporates by reference).

In short: you can use Murmur in your own (including proprietary) project, but
modifications to **Murmur itself** must be shared back under the same license.
