"""Import already-processed WAV stems (Day 1 experiment) into the web library.

Usage: python import_existing.py <stems_dir> <id> <name> <model> <duration_s> <processing_time_s> <device>
"""
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
STEMS = ("vocals", "drums", "bass", "other")


def main() -> None:
    stems_dir, sid, name, model, duration, time_sec, device = sys.argv[1:8]
    src = Path(stems_dir)
    out = ROOT / "data" / "songs" / sid
    out.mkdir(parents=True, exist_ok=True)

    for stem in STEMS:
        wav = src / f"{stem}.wav"
        if not wav.exists():
            sys.exit(f"missing {wav}")
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(wav),
                        "-codec:a", "libmp3lame", "-b:a", "320k",
                        str(out / f"{stem}.mp3")], check=True)

    meta = {"id": sid, "name": name, "model": model, "device": device,
            "duration": float(duration), "time_sec": float(time_sec),
            "processed_at": datetime.now().isoformat(timespec="seconds")}
    (out / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2),
                                   encoding="utf-8")
    print(f"[ok] {sid}")


if __name__ == "__main__":
    main()
