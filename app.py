"""Stems Web: subis una cancion, se separa en stems (BS-Roformer-SW 6 stems, o
Demucs 4 stems) y queda en una biblioteca local con player multipista.

Correr:  .venv-gpu\\Scripts\\python.exe -m uvicorn app:app --app-dir . --port 8765
(o run.ps1; ver README.md)
"""
import io
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
import unicodedata
import zipfile
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

import soundfile as sf
import torch
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

ROOT = Path(__file__).resolve().parent

# En el paquete portable ffmpeg/ffprobe viven en ffmpeg/; se suman al PATH del proceso
# porque yt-dlp y audio-separator tambien los resuelven por PATH. En dev no hace nada.
_FFMPEG_DIR = ROOT / "ffmpeg"
if _FFMPEG_DIR.is_dir():
    os.environ["PATH"] = f"{_FFMPEG_DIR}{os.pathsep}{os.environ.get('PATH', '')}"

SONGS = ROOT / "data" / "songs"
UPLOADS = ROOT / "data" / "uploads"
TMP = ROOT / "data" / "tmp"
for d in (SONGS, UPLOADS, TMP):
    d.mkdir(parents=True, exist_ok=True)

STEMS = ("vocals", "drums", "bass", "other")
STEM_ORDER = ("vocals", "drums", "bass", "guitar", "piano", "other")
ALLOWED_EXT = {".mp3", ".wav", ".mp4", ".m4a", ".flac", ".ogg"}
DEFAULT_MODEL = "bs_roformer_sw"
# kind: demucs (API) | audio_separator (subproceso CLI)
# ratio: tiempo de proceso / duracion del audio (solo para barra de progreso)
MODELS = {
    "bs_roformer_sw": {"kind": "audio_separator", "file": "BS-Roformer-SW.ckpt",
                       "ratio": 0.48},
    "htdemucs_ft": {"kind": "demucs", "ratio": 0.35},
    "htdemucs":    {"kind": "demucs", "ratio": 0.15},
}
_SLUG_SUFFIX = {"htdemucs": "", "htdemucs_ft": "-ft", "bs_roformer_sw": "-sw"}

# Nitidez -> overlap por tipo de modelo (mas overlap = menos artefactos, mas lento;
# audio_separator usa entero, demucs fraccion 0-1).
QUALITY = {
    "rapido": {"audio_separator": 2,  "demucs": 0.10},
    "normal": {"audio_separator": 8,  "demucs": 0.25},
    "alta":   {"audio_separator": 16, "demucs": 0.50},
}
DEFAULT_QUALITY = "normal"
MODEL_DIR = ROOT / "models"
MAX_UPLOAD_MB = 300
ALLOWED_URL_HOSTS = {"youtube.com", "youtu.be", "m.youtube.com", "music.youtube.com"}
MAX_URL_DURATION = 1800   # cap de 30 min para descargas de youtube

app = FastAPI(title="Stems Web")

# La app es local (127.0.0.1). Este filtro corta DNS rebinding (Host ajeno) y CSRF
# (origen cruzado en metodos con efectos); no reemplaza a no exponerla a internet.
_LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}


@app.middleware("http")
async def only_local(request, call_next):
    if (request.url.hostname or "").lower() not in _LOCAL_HOSTS:
        return Response("solo acceso local", status_code=400)
    origin = request.headers.get("origin")
    if origin and request.method not in ("GET", "HEAD", "OPTIONS"):
        if (urlparse(origin).hostname or "").lower() not in _LOCAL_HOSTS:
            return Response("origen no permitido", status_code=403)
    return await call_next(request)


jobs: dict[str, dict] = {}          # trabajos en curso / con error
jobs_lock = threading.Lock()
job_q: queue.Queue = queue.Queue()
cancelled: set[str] = set()          # sids marcados para cancelar antes de procesar

_models: dict[str, object] = {}     # cache de modelos cargados
_device = "cuda" if torch.cuda.is_available() else "cpu"


def ratios_by_model(done: list[dict]) -> dict[str, float]:
    """Mediana de (tiempo_proc / duracion) por modelo sobre jobs terminados:
    calibra la barra de progreso/ETA al hardware real en vez de MODELS[ratio]."""
    by: dict[str, list[float]] = {}
    for m in done:
        mdl, d, t = m.get("model"), m.get("duration"), m.get("time_sec")
        if mdl and d and d > 0 and t:
            by.setdefault(mdl, []).append(t / d)
    return {mdl: sorted(xs)[len(xs) // 2] for mdl, xs in by.items()}


def slugify(name: str) -> str:
    base = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    base = re.sub(r"[^a-zA-Z0-9]+", "-", base).strip("-").lower() or "cancion"
    return base[:60]


def unique_id(slug: str) -> str:
    sid, n = slug, 2
    with jobs_lock:
        while (SONGS / sid).exists() or sid in jobs:
            sid = f"{slug}-{n}"
            n += 1
        return sid


def set_job(sid: str, **kw) -> None:
    with jobs_lock:
        jobs.setdefault(sid, {})
        jobs[sid].update(kw)


def resolve_song_dir(sid: str) -> Path:
    """Valida el sid ([a-z0-9-], anti path-traversal) y devuelve la carpeta de la
    cancion. Compartido por los endpoints de borrado y descarga zip."""
    if not re.fullmatch(r"[a-z0-9-]+", sid):
        raise HTTPException(400, "id invalido")
    d = (SONGS / sid).resolve()
    if d.parent != SONGS.resolve() or not d.is_dir():
        raise HTTPException(404, "no existe")
    return d


def ffmpeg(*args: str, timeout: float | None = None) -> None:
    # timeout = watchdog del worker (single-thread): un ffmpeg colgado por un archivo
    # corrupto bloquearia toda la cola.
    try:
        proc = subprocess.run(["ffmpeg", "-y", "-v", "error", *args],
                              capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"ffmpeg excedio el tiempo limite ({timeout:.0f}s)")
    except FileNotFoundError:
        raise RuntimeError("no se encontro ffmpeg (falta la carpeta ffmpeg/ del paquete)")
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg: {proc.stderr.strip()[:300]}")


def probe_duration(path: Path) -> float:
    try:
        proc = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "json", str(path)], capture_output=True, text=True, timeout=30)
    except subprocess.TimeoutExpired:
        raise RuntimeError("ffprobe se colgo leyendo el archivo")
    except FileNotFoundError:
        raise RuntimeError("no se encontro ffprobe (falta la carpeta ffmpeg/ del paquete)")
    if proc.returncode != 0:
        raise RuntimeError("ffprobe no pudo leer el archivo (corrupto o sin audio)")
    return float(json.loads(proc.stdout)["format"]["duration"])


def valid_yt_url(url: str) -> bool:
    try:
        u = urlparse(url.strip())
    except Exception:
        return False
    if u.scheme not in ("http", "https"):
        return False
    host = (u.netloc or "").lower().split(":")[0]
    if host.startswith("www."):
        host = host[4:]
    return host in ALLOWED_URL_HOSTS


def yt_metadata(url: str) -> tuple[str, float]:
    """(titulo, duracion_seg) sin descargar. Valida que el link sea accesible."""
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "yt_dlp", "--no-playlist", "--skip-download",
             "--no-warnings", "--print", "%(title)s", "--print", "%(duration)s", url],
            capture_output=True, text=True, timeout=60)
    except subprocess.TimeoutExpired:
        raise RuntimeError("yt-dlp tardo demasiado leyendo el link")
    if proc.returncode != 0:
        raise RuntimeError("no se pudo leer el link (privado, borrado o bloqueado)")
    lines = [l.strip() for l in proc.stdout.splitlines() if l.strip()]
    title = lines[0] if lines else "youtube"
    try:
        duration = float(lines[1]) if len(lines) > 1 else 0.0
    except ValueError:
        duration = 0.0
    return title, duration


def download_audio(url: str, sid: str) -> Path:
    """Descarga el mejor audio del link a data/uploads/<sid>.<ext> y devuelve el path."""
    out_tmpl = str(UPLOADS / f"{sid}.%(ext)s")
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "yt_dlp", "-f", "bestaudio/best", "--no-playlist",
             "--no-part", "--no-warnings", "--max-filesize", "250M", "-o", out_tmpl, url],
            capture_output=True, text=True, timeout=600)
    except subprocess.TimeoutExpired:
        raise RuntimeError("yt-dlp excedio el tiempo limite descargando")
    if proc.returncode != 0:
        raise RuntimeError(f"yt-dlp: {proc.stderr.strip()[:200]}")
    files = sorted(UPLOADS.glob(f"{sid}.*"))
    if not files:
        raise RuntimeError("yt-dlp no genero archivo de audio")
    return files[0]


def get_model(name: str):
    if name not in _models:
        from demucs.pretrained import get_model as load
        m = load(name)
        m.to(_device)
        _models[name] = m
    return _models[name]


def separate_demucs(sid: str, wav_path: Path, model_name: str,
                    overlap: float) -> dict[str, Path]:
    """Devuelve {stem: wav temporal}."""
    from demucs.apply import apply_model

    data, sr = sf.read(str(wav_path), dtype="float32", always_2d=True)
    wav = torch.from_numpy(data.T)
    ref = wav.mean(0)
    norm = (wav - ref.mean()) / ref.std()

    model = get_model(model_name)
    sources = apply_model(model, norm[None], device=_device,
                          split=True, overlap=overlap, progress=True)[0]
    if _device == "cuda":
        torch.cuda.synchronize()
    sources = sources * ref.std() + ref.mean()

    out = {}
    for stem_name, source in zip(model.sources, sources):
        tmp_stem = TMP / f"{sid}-{stem_name}.wav"
        sf.write(str(tmp_stem), source.cpu().numpy().T, sr)
        out[stem_name] = tmp_stem
    return out


def get_separator(model_file: str, overlap: int):
    """Cachea un Separator por (modelo, overlap): el overlap se fija al construirlo.
    API in-process, nunca el .exe (trampoline de uv con la ruta del venv embebida)."""
    key = f"as:{model_file}:{overlap}"
    if key not in _models:
        from audio_separator.separator import Separator
        sep = Separator(
            log_level=30,
            model_file_dir=str(MODEL_DIR),
            output_dir=str(TMP),
            output_format="WAV",
            mdxc_params={"segment_size": 256, "override_model_segment_size": False,
                         "batch_size": 1, "overlap": overlap, "pitch_shift": 0},
        )
        sep.load_model(model_filename=model_file)
        _models[key] = sep
    return _models[key]


def separate_audio_separator(sid: str, wav_path: Path, model_file: str,
                             overlap: int) -> dict[str, Path]:
    """Separa con audio-separator y mapea sus WAV ({sid}_(Stem)_Modelo.wav) por stem."""
    sep = get_separator(model_file, overlap)
    produced = sep.separate(str(wav_path))

    out = {}
    for name in produced:
        f = TMP / Path(name).name
        m = re.search(r"_\(([a-z]+)\)_", f.name.lower())
        if m:
            out[m.group(1)] = f
    if not out:
        raise RuntimeError("audio-separator no genero stems reconocibles")
    return out


def process(sid: str, src: Path, model_name: str, display_name: str,
            quality: str) -> None:
    t_start = time.perf_counter()
    wav_path = TMP / f"{sid}.wav"
    stem_files: dict[str, Path] = {}
    try:
        duration = probe_duration(src)
        set_job(sid, status="processing", phase="extrayendo audio",
                duration=round(duration, 1), started=time.time())

        ffmpeg("-i", str(src), "-vn", "-acodec", "pcm_s16le",
               "-ar", "44100", "-ac", "2", str(wav_path),
               timeout=max(120.0, duration * 4))

        cfg = MODELS[model_name]
        overlap = QUALITY.get(quality, QUALITY[DEFAULT_QUALITY])[cfg["kind"]]
        set_job(sid, phase=f"separando ({model_name} / {quality} en {_device})")
        if cfg["kind"] == "demucs":
            stem_files = separate_demucs(sid, wav_path, model_name, overlap)
        else:
            stem_files = separate_audio_separator(sid, wav_path, cfg["file"], overlap)

        set_job(sid, phase="codificando mp3")
        out_dir = SONGS / sid
        out_dir.mkdir(parents=True, exist_ok=True)
        stems = [s for s in STEM_ORDER if s in stem_files] + \
                [s for s in stem_files if s not in STEM_ORDER]
        for stem_name in stems:
            ffmpeg("-i", str(stem_files[stem_name]), "-codec:a", "libmp3lame",
                   "-b:a", "320k", str(out_dir / f"{stem_name}.mp3"),
                   timeout=max(120.0, duration * 2))

        meta = {
            "id": sid, "name": display_name, "model": model_name,
            "device": _device, "duration": round(duration, 1),
            "stems": stems, "quality": quality, "overlap": overlap,
            "time_sec": round(time.perf_counter() - t_start, 1),
            "processed_at": datetime.now().isoformat(timespec="seconds"),
        }
        (out_dir / "meta.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
        with jobs_lock:
            jobs.pop(sid, None)
        print(f"[ok] {sid}: {meta['time_sec']} s")
    except Exception as e:
        set_job(sid, status="error", error=str(e)[:300])
        print(f"[error] {sid}: {e}")
    finally:
        wav_path.unlink(missing_ok=True)
        src.unlink(missing_ok=True)
        for f in stem_files.values():
            f.unlink(missing_ok=True)


def worker() -> None:
    print(f"[worker] device={_device}")
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        print("[worker] AVISO: ffmpeg/ffprobe no estan en el PATH; los jobs van a fallar. "
              "Falta la carpeta ffmpeg/ del paquete o el PATH no la incluye.")
    while True:
        sid, src, model_name, display_name, quality, url = job_q.get()
        if sid in cancelled:                    # cancelado mientras estaba en cola
            cancelled.discard(sid)
            if src:
                src.unlink(missing_ok=True)
            with jobs_lock:
                jobs.pop(sid, None)
            print(f"[cancel] {sid}")
            continue
        if url:                                 # job de youtube: descargar primero
            try:
                set_job(sid, status="processing", phase="descargando de youtube",
                        started=time.time())
                src = download_audio(url, sid)
            except Exception as e:
                set_job(sid, status="error", error=str(e)[:300])
                print(f"[error] {sid}: descarga: {e}")
                continue
        process(sid, src, model_name, display_name, quality)


threading.Thread(target=worker, daemon=True).start()


@app.post("/api/upload")
async def upload(file: UploadFile = File(...), model: str = Form(DEFAULT_MODEL),
                 quality: str = Form(DEFAULT_QUALITY)):
    ext = Path(file.filename or "x").suffix.lower()
    if ext not in ALLOWED_EXT:
        raise HTTPException(422, f"Extension no soportada: {ext}")
    if model not in MODELS:
        raise HTTPException(422, f"Modelo desconocido: {model}")
    if quality not in QUALITY:
        raise HTTPException(422, f"Calidad desconocida: {quality}")

    display_name = Path(file.filename).stem
    sid = unique_id(slugify(display_name) + _SLUG_SUFFIX.get(model, f"-{model}"))
    dst = UPLOADS / f"{sid}{ext}"

    size = 0
    with dst.open("wb") as f:
        while chunk := await file.read(1 << 20):
            size += len(chunk)
            if size > MAX_UPLOAD_MB << 20:
                f.close()
                dst.unlink(missing_ok=True)
                raise HTTPException(422, f"Archivo mayor a {MAX_UPLOAD_MB} MB")
            f.write(chunk)

    set_job(sid, status="queued", phase="en cola", name=display_name,
            model=model, quality=quality, started=time.time(), duration=0)
    job_q.put((sid, dst, model, display_name, quality, None))
    return {"id": sid}


@app.post("/api/upload-url")
def upload_url(url: str = Form(...), model: str = Form(DEFAULT_MODEL),
               quality: str = Form(DEFAULT_QUALITY)):
    """Ingesta local por URL de YouTube (uso personal): valida dominio y duracion,
    despues se encola como un upload normal. Ver 05-legal-y-riesgos.md."""
    if model not in MODELS:
        raise HTTPException(422, f"Modelo desconocido: {model}")
    if quality not in QUALITY:
        raise HTTPException(422, f"Calidad desconocida: {quality}")
    if not valid_yt_url(url):
        raise HTTPException(422, "Solo se aceptan links de YouTube")

    try:
        title, duration = yt_metadata(url)
    except RuntimeError as e:
        raise HTTPException(422, str(e))
    if duration and duration > MAX_URL_DURATION:
        raise HTTPException(422, f"Dura {int(duration)//60} min; el maximo es {MAX_URL_DURATION//60} min")

    display_name = title or "youtube"
    sid = unique_id(slugify(display_name) + _SLUG_SUFFIX.get(model, f"-{model}"))
    set_job(sid, status="queued", phase="en cola (youtube)", name=display_name,
            model=model, quality=quality, started=time.time(), duration=0)
    job_q.put((sid, None, model, display_name, quality, url))
    return {"id": sid}


@app.get("/api/songs")
def songs():
    done = []
    for meta_file in SONGS.glob("*/meta.json"):
        try:
            done.append(json.loads(meta_file.read_text(encoding="utf-8")))
        except Exception:
            continue
    done.sort(key=lambda m: m.get("processed_at", ""), reverse=True)
    measured = ratios_by_model(done)

    inflight = []
    with jobs_lock:
        for sid, j in jobs.items():
            item = {"id": sid, **j}
            if j.get("status") == "processing" and j.get("duration"):
                model = j.get("model", "")
                ratio = measured.get(model) or MODELS.get(model, {}).get("ratio", 0.5)
                expected = j["duration"] * ratio + 10
                elapsed = time.time() - j["started"]
                item["progress"] = min(95, round(elapsed / expected * 100))
                item["eta_sec"] = max(0, round(expected - elapsed))
            inflight.append(item)
    inflight.sort(key=lambda j: j.get("started", 0), reverse=True)
    return {"processing": inflight, "done": done, "device": _device}


@app.get("/api/songs/{sid}/download.zip")
def download_zip(sid: str):
    song_dir = resolve_song_dir(sid)
    mp3s = sorted(song_dir.glob("*.mp3"))
    if not mp3s:
        raise HTTPException(404, "sin stems")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as zf:  # mp3 ya esta comprimido
        for f in mp3s:
            zf.write(f, arcname=f"{sid}/{f.name}")
    return Response(
        buf.getvalue(), media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{sid}-stems.zip"'})


@app.post("/api/jobs/{sid}/cancel")
def cancel_job(sid: str):
    """Cancela un job en cola; uno ya procesando no se interrumpe (worker single-thread)."""
    with jobs_lock:
        j = jobs.get(sid)
        if not j:
            raise HTTPException(404, "el job no existe")
        if j.get("status") != "queued":
            raise HTTPException(409, "ya esta procesando, no se puede cancelar")
        cancelled.add(sid)
        jobs.pop(sid, None)
    return {"cancelled": sid}


@app.delete("/api/songs/{sid}")
def delete_song(sid: str):
    song_dir = resolve_song_dir(sid)
    shutil.rmtree(song_dir, ignore_errors=True)
    return {"deleted": sid}


@app.get("/")
def index():
    return FileResponse(ROOT / "static" / "index.html")


app.mount("/songs", StaticFiles(directory=SONGS), name="songs")
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")
