"""GPU (ROCm) vs CPU test using the Demucs API.

It exists because torchaudio 2.9 (the one shipped with the ROCm wheels) no longer saves/loads
audio without torchcodec, so this script reads and writes WAV with soundfile.
Use ONLY with the .venv-gpu venv. For the CPU baseline there is scripts/separate.py + .venv.

Usage:
  .venv-gpu\\Scripts\\python.exe scripts\\gpu_test.py output\\test-cc-nightowl\\cpu\\audio.wav --device cuda
"""
import argparse
import sys
import time
from pathlib import Path

import soundfile as sf
import torch
from demucs.apply import apply_model
from demucs.pretrained import get_model

ROOT = Path(__file__).resolve().parent.parent


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("wav", type=Path, help="44.1 kHz stereo WAV (already extracted)")
    ap.add_argument("--device", default="cuda", choices=("cuda", "cpu"))
    ap.add_argument("--model", default="htdemucs")
    args = ap.parse_args()

    print(f"torch {torch.__version__}")
    if args.device == "cuda":
        if not torch.cuda.is_available():
            sys.exit("GPU NOT available to torch (torch.cuda.is_available() == False)")
        print(f"GPU: {torch.cuda.get_device_name(0)}")

    data, sr = sf.read(str(args.wav), dtype="float32", always_2d=True)  # (time, ch)
    wav = torch.from_numpy(data.T)  # (ch, time)
    duration = wav.shape[1] / sr
    print(f"audio: {duration:.1f} s @ {sr} Hz")

    t0 = time.perf_counter()
    model = get_model(args.model)
    model.to(args.device)
    t_load = time.perf_counter() - t0

    ref = wav.mean(0)
    norm = (wav - ref.mean()) / ref.std()

    t0 = time.perf_counter()
    sources = apply_model(model, norm[None], device=args.device, split=True,
                          overlap=0.25, progress=True)[0]
    if args.device == "cuda":
        torch.cuda.synchronize()
    t_sep = time.perf_counter() - t0
    sources = sources * ref.std() + ref.mean()

    out_dir = ROOT / "output" / args.wav.parent.parent.name / f"{args.device}-api" / args.model
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, source in zip(model.sources, sources):
        sf.write(str(out_dir / f"{name}.wav"), source.cpu().numpy().T, sr)

    print(f"\nmodel load   : {t_load:.1f} s")
    print(f"separation   : {t_sep:.1f} s for {duration:.1f} s of audio "
          f"(x{t_sep / duration:.2f} of the duration)")
    print(f"stems in     : {out_dir}")


if __name__ == "__main__":
    main()
