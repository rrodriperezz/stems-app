# stems-app

Local web app that splits any song into separate stems (vocals, drums, bass, guitar, piano and the rest) on your own GPU, then plays them back in a multitrack mixer in the browser. No cloud, no upload limits, no subscription.

## Features

- **Two separation engines:** BS-Roformer-SW (6 stems, best quality) through `audio-separator`, or Demucs `htdemucs` / `htdemucs_ft` (4 stems, faster).
- **Quality presets** that trade speed for fewer artifacts by changing the chunk overlap.
- **Upload a file or paste a YouTube link** (mp3, wav, mp4, m4a, flac, ogg; up to 300 MB or 30 minutes).
- **Job queue** with live progress, estimated time per model and cancellation.
- **Multitrack player:** mute, solo and volume per stem, loops, and tempo and pitch changed independently (SoundTouch).
- **Library** of separated songs, each downloadable as a zip.
- **Guitar to MIDI** (`tabs/`): turns the guitar stem into a `.mid` file and a notes CSV with Spotify's basic-pitch, as a first draft for tablature.

## Security

The server binds to `127.0.0.1` only. A middleware rejects any request whose `Host` is not local (DNS rebinding) and any state-changing request from a foreign `Origin` (CSRF). YouTube links are checked against an allowlist of hosts before `yt-dlp` sees them, and every path that comes from a song id is resolved and checked so it stays inside the library folder.

## Run it

Requires Windows, ffmpeg on `PATH` and Python 3.12 managed by [uv](https://docs.astral.sh/uv/). A GPU is optional: without CUDA or ROCm it runs on the CPU, just slower.

```powershell
uv venv --python 3.12 .venv-gpu
uv pip install --python .venv-gpu torch torchaudio "audio-separator==0.44.2" "demucs==4.0.1" fastapi "uvicorn[standard]" python-multipart soundfile yt-dlp
.\run.ps1
```

`run.ps1` opens http://127.0.0.1:8765. The BS-Roformer checkpoint (667 MB) is downloaded on first use into `models/`.

## Tests

```powershell
.venv-gpu\Scripts\python.exe -m pytest tests
```

## Portable installer

`packaging/` builds a self-contained Windows package (embedded Python, CUDA torch and every dependency) and an Inno Setup installer, so the app runs on a machine with nothing installed.

## Layout

```
app.py            FastAPI server: queue, separation, library, downloads
static/           browser UI and multitrack player
tabs/             guitar stem to MIDI (separate venv)
scripts/          environment checks, GPU test, CLI separation
packaging/        portable package and installer
tests/            API tests
```

Separated audio is never committed: songs and stems are usually copyrighted, so `data/`, `input/` and `output/` are ignored.
