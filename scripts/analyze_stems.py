r"""Per-stem energy analysis: how much of the mix energy lands in each stem.

Reports RMS dBFS per stem and % of total energy, plus energy in the bass band
(40-250 Hz) to check whether the bass really ends up in the bass stem.

Usage:
  .venv\Scripts\python.exe scripts\analyze_stems.py output\Walls\cuda-api\htdemucs_ft
"""
import argparse
import sys
from pathlib import Path

import numpy as np
import soundfile as sf


def band_energy(x: np.ndarray, sr: int, lo: float, hi: float) -> float:
    """Energy in the [lo, hi] Hz band via FFT (mono sum)."""
    mono = x.mean(axis=1)
    spec = np.abs(np.fft.rfft(mono)) ** 2
    freqs = np.fft.rfftfreq(len(mono), 1 / sr)
    return float(spec[(freqs >= lo) & (freqs < hi)].sum())


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("stems_dir", type=Path)
    args = ap.parse_args()

    files = sorted(args.stems_dir.glob("*.wav"))
    if not files:
        sys.exit(f"No WAVs in {args.stems_dir}")

    rows = []
    total_energy = 0.0
    total_low = 0.0
    for f in files:
        data, sr = sf.read(str(f), dtype="float32", always_2d=True)
        rms = float(np.sqrt((data ** 2).mean()))
        energy = float((data ** 2).sum())
        low = band_energy(data, sr, 40, 250)
        mid = band_energy(data, sr, 250, 4000)
        total_energy += energy
        total_low += low
        dbfs = 20 * np.log10(rms) if rms > 0 else -120.0
        rows.append((f.stem, dbfs, energy, low, mid))

    print(f"\n{args.stems_dir}")
    print(f"{'stem':<45} {'RMS dBFS':>9} {'% energy':>10} {'% low 40-250Hz':>15}")
    for name, dbfs, energy, low, mid in rows:
        pct = 100 * energy / total_energy if total_energy else 0
        pct_low = 100 * low / total_low if total_low else 0
        print(f"{name:<45} {dbfs:>9.1f} {pct:>9.1f}% {pct_low:>14.1f}%")


if __name__ == "__main__":
    main()
