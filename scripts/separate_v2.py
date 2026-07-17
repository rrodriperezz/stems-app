r"""Pipeline v2: separacion 6 stems con BS-Roformer-SW (vocals/drums/bass/guitar/piano/other).

Reemplaza a htdemucs/htdemucs_ft como modelo de produccion porque diferencia
bass y guitar como stems propios (SDR publicado: bass 14.6, guitar 9.0) en vez
de mandar la guitarra a "other".

Por cada archivo de entrada (.mp4/.mp3/.wav/...):
  1. Valida con ffprobe (tiene audio? duracion?).
  2. Extrae/convierte a WAV 44.1 kHz estereo con ffmpeg.
  3. Separa 6 stems con audio-separator + BS-Roformer-SW (GPU ROCm).
  4. Renombra stems a nombres limpios (vocals.wav, guitar.wav, ...).
  5. Mide tiempos y agrega el resultado a output/resultados.json.

Usar SOLO con .venv-gpu (audio-separator + torch ROCm):
  .venv-gpu\Scripts\python.exe scripts\separate_v2.py input\cancion.mp3
  .venv-gpu\Scripts\python.exe scripts\separate_v2.py input\video.mp4 --overlap 4
"""
import argparse
import json
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUTPUT = ROOT / "output"
MODELS_DIR = ROOT / "models"
RESULTS_JSON = OUTPUT / "resultados.json"
MODEL_FILE = "BS-Roformer-SW.ckpt"
MODEL_TAG = "bs-roformer-sw"

# patron de salida de audio-separator: <input>_(Stem)_<modelo>.wav
STEM_RE = re.compile(r"_\((\w+)\)_")


def probe(path: Path) -> dict:
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration:stream=codec_type,codec_name,sample_rate,channels",
        "-of", "json", str(path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"ffprobe fallo: {proc.stderr.strip()[:300]}")
    info = json.loads(proc.stdout)
    audio = [s for s in info.get("streams", []) if s.get("codec_type") == "audio"]
    if not audio:
        raise RuntimeError("el archivo no tiene pista de audio")
    return {
        "duration_sec": round(float(info["format"]["duration"]), 1),
        "audio_codec": audio[0].get("codec_name"),
        "sample_rate": audio[0].get("sample_rate"),
        "channels": audio[0].get("channels"),
    }


def extract_wav(src: Path, dst: Path) -> float:
    t0 = time.perf_counter()
    cmd = ["ffmpeg", "-y", "-v", "error", "-i", str(src),
           "-vn", "-acodec", "pcm_s16le", "-ar", "44100", "-ac", "2", str(dst)]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg fallo: {proc.stderr.strip()[:300]}")
    return round(time.perf_counter() - t0, 1)


def separate(wav: Path, out_dir: Path, overlap: int) -> tuple[float, list[Path]]:
    """Separa con audio-separator (API). Devuelve (segundos, stems renombrados)."""
    from audio_separator.separator import Separator

    import torch
    device = "cuda" if torch.cuda.is_available() else "cpu"
    if device == "cpu":
        print("AVISO: GPU no disponible, corre en CPU (lento en este venv)")

    separator = Separator(
        log_level=30,  # WARNING: sin spam de INFO
        model_file_dir=str(MODELS_DIR),
        output_dir=str(out_dir),
        output_format="WAV",
        mdxc_params={
            "segment_size": 256,
            "override_model_segment_size": False,
            "batch_size": 1,
            "overlap": overlap,
            "pitch_shift": 0,
        },
    )
    separator.load_model(model_filename=MODEL_FILE)

    t0 = time.perf_counter()
    produced = separator.separate(str(wav))
    elapsed = round(time.perf_counter() - t0, 1)

    # renombrar "<input>_(Guitar)_BS-Roformer-SW.wav" -> "guitar.wav"
    renamed = []
    for name in produced:
        f = out_dir / Path(name).name
        m = STEM_RE.search(f.name)
        if not m:
            renamed.append(f)
            continue
        clean = out_dir / f"{m.group(1).lower()}.wav"
        if clean.exists():
            clean.unlink()
        f.rename(clean)
        renamed.append(clean)
    return elapsed, renamed


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("input", type=Path)
    ap.add_argument("--overlap", type=int, default=8,
                    help="num_overlap de inferencia (mas = mejor calidad, mas lento; default 8)")
    args = ap.parse_args()

    src = args.input.resolve()
    if not src.exists():
        sys.exit(f"No existe: {src}")
    if not (MODELS_DIR / MODEL_FILE).exists():
        sys.exit(f"Falta el modelo {MODEL_FILE} en {MODELS_DIR} "
                 "(audio-separator lo descarga solo la primera vez si hay internet)")

    result = {
        "file": src.name,
        "model": MODEL_TAG,
        "overlap": args.overlap,
        "started": datetime.now().isoformat(timespec="seconds"),
    }
    try:
        print(f"[1/3] ffprobe {src.name}")
        result.update(probe(src))
        print(f"      duracion {result['duration_sec']} s, "
              f"audio {result['audio_codec']} {result['sample_rate']} Hz {result['channels']}ch")

        run_dir = OUTPUT / src.stem
        run_dir.mkdir(parents=True, exist_ok=True)
        stems_dir = run_dir / MODEL_TAG
        stems_dir.mkdir(parents=True, exist_ok=True)

        wav = run_dir / "audio.wav"
        if src.suffix.lower() == ".wav":
            wav = src
            result["t_extract_sec"] = 0.0
        else:
            print(f"[2/3] ffmpeg -> {wav.relative_to(ROOT)}")
            result["t_extract_sec"] = extract_wav(src, wav)

        print(f"[3/3] {MODEL_TAG} (overlap {args.overlap})")
        result["t_separate_sec"], stems = separate(wav, stems_dir, args.overlap)

        result["device"] = "cuda-api"
        result["stems_mb"] = {
            f.stem: round(f.stat().st_size / 1e6, 1) for f in sorted(stems)
        }
        result["stems_dir"] = str(stems_dir)
        result["ratio_proceso_vs_duracion"] = round(
            result["t_separate_sec"] / result["duration_sec"], 2)
        result["status"] = "ok"
        print(f"\nOK - {result['t_separate_sec']} s de separacion para "
              f"{result['duration_sec']} s de audio "
              f"(x{result['ratio_proceso_vs_duracion']} de la duracion)")
        print(f"Stems en: {stems_dir}")
    except Exception as e:
        result["status"] = "error"
        result["error"] = str(e)
        print(f"\nERROR: {e}", file=sys.stderr)

    OUTPUT.mkdir(exist_ok=True)
    history = json.loads(RESULTS_JSON.read_text(encoding="utf-8")) if RESULTS_JSON.exists() else []
    history.append(result)
    RESULTS_JSON.write_text(json.dumps(history, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Resultado registrado en {RESULTS_JSON}")
    if result["status"] == "error":
        sys.exit(1)


if __name__ == "__main__":
    main()
