"""Minimal pipeline from the Day 1 experiment.

For each input file (.mp4/.mp3/.wav/...):
  1. Validates it with ffprobe (does it have audio? how long?).
  2. Extracts/converts to 44.1 kHz stereo WAV with ffmpeg.
  3. Separates 4 stems with Demucs (htdemucs by default).
  4. Measures times and sizes; appends the result to output/resultados.json.

Usage:
  python scripts/separate.py input/song.mp4 [--device auto|cpu|cuda] [--model htdemucs]
"""
import argparse
import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUTPUT = ROOT / "output"
RESULTS_JSON = OUTPUT / "resultados.json"
STEMS = ("vocals", "drums", "bass", "other")


def probe(path: Path) -> dict:
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration:stream=codec_type,codec_name,sample_rate,channels",
        "-of", "json", str(path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {proc.stderr.strip()[:300]}")
    info = json.loads(proc.stdout)
    audio = [s for s in info.get("streams", []) if s.get("codec_type") == "audio"]
    if not audio:
        raise RuntimeError("the file has no audio track")
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
        raise RuntimeError(f"ffmpeg failed: {proc.stderr.strip()[:300]}")
    return round(time.perf_counter() - t0, 1)


def resolve_device(requested: str) -> str:
    if requested != "auto":
        return requested
    import torch
    return "cuda" if torch.cuda.is_available() else "cpu"


def separate(wav: Path, out_dir: Path, model: str, device: str) -> float:
    """Run demucs as a subprocess (same venv) — reproducible by hand with the same command."""
    t0 = time.perf_counter()
    cmd = [sys.executable, "-m", "demucs", "-n", model, "-d", device,
           "-o", str(out_dir), str(wav)]
    print(f"  $ {' '.join(cmd)}")
    proc = subprocess.run(cmd)
    if proc.returncode != 0:
        raise RuntimeError(f"demucs exited with code {proc.returncode}")
    return round(time.perf_counter() - t0, 1)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("input", type=Path)
    ap.add_argument("--device", default="auto", choices=("auto", "cpu", "cuda"))
    ap.add_argument("--model", default="htdemucs")
    args = ap.parse_args()

    src = args.input.resolve()
    if not src.exists():
        sys.exit(f"Not found: {src}")

    result = {
        "file": src.name,
        "model": args.model,
        "started": datetime.now().isoformat(timespec="seconds"),
    }
    try:
        print(f"[1/3] ffprobe {src.name}")
        result.update(probe(src))
        print(f"      duration {result['duration_sec']} s, "
              f"audio {result['audio_codec']} {result['sample_rate']} Hz {result['channels']}ch")

        device = resolve_device(args.device)
        result["device"] = device
        run_dir = OUTPUT / src.stem / device
        run_dir.mkdir(parents=True, exist_ok=True)

        wav = run_dir / "audio.wav"
        if src.suffix.lower() == ".wav":
            wav = src
            result["t_extract_sec"] = 0.0
        else:
            print(f"[2/3] ffmpeg -> {wav.relative_to(ROOT)}")
            result["t_extract_sec"] = extract_wav(src, wav)

        print(f"[3/3] demucs {args.model} on {device}")
        result["t_separate_sec"] = separate(wav, run_dir, args.model, device)

        stems_dir = run_dir / args.model / wav.stem
        sizes = {}
        for stem in STEMS:
            f = stems_dir / f"{stem}.wav"
            sizes[stem] = round(f.stat().st_size / 1e6, 1) if f.exists() else None
        result["stems_mb"] = sizes
        result["stems_dir"] = str(stems_dir)
        result["ratio_proceso_vs_duracion"] = round(
            result["t_separate_sec"] / result["duration_sec"], 2)
        result["status"] = "ok"
        print(f"\nOK — {result['t_separate_sec']} s of separation for "
              f"{result['duration_sec']} s of audio "
              f"(x{result['ratio_proceso_vs_duracion']} of the duration)")
        print(f"Stems in: {stems_dir}")
    except Exception as e:
        result["status"] = "error"
        result["error"] = str(e)
        print(f"\nERROR: {e}", file=sys.stderr)

    OUTPUT.mkdir(exist_ok=True)
    history = json.loads(RESULTS_JSON.read_text(encoding="utf-8")) if RESULTS_JSON.exists() else []
    history.append(result)
    RESULTS_JSON.write_text(json.dumps(history, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Result saved to {RESULTS_JSON}")
    if result["status"] == "error":
        sys.exit(1)


if __name__ == "__main__":
    main()
