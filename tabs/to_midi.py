"""audio (guitar stem) -> MIDI notes. Stage 1 (AMT) of the tablature pipeline.

Uses basic-pitch (Spotify, MIT). Outputs .mid + notes.csv (start, end, pitch, name, velocity).
Does NOT assign string/fret yet (that is Stage 2). Monophonic parts (solos) come out well;
chords/distortion come out rough -> treat the output as a DRAFT to fix by ear.

Usage (from the tabs/ folder):
  .venv-tabs\\Scripts\\python.exe to_midi.py <audio.wav|mp3> [options]

Options (defaults tuned for guitar):
  --out-dir DIR        output folder (default: tabs/output)
  --min-note-ms 70     shortest note to detect (ms). Lower it for fast solos.
  --min-freq 70        minimum frequency Hz (~low E = 82, drop D = 73)
  --max-freq 1600      maximum frequency Hz (high frets)
  --onset 0.5          onset threshold (0-1). Higher = fewer ghost notes.
  --frame 0.3          sustain threshold (0-1).

Simplification (post-processing, so it is playable on guitar):
  --min-velocity 0     drops notes with velocity < N (1-127). Removes bleed from other
                       instruments and weak ghost notes. Try 25-45.
  --mono               collapses to monophonic: at each instant only the loudest note
                       remains (the melody/lead); chords and simultaneous harmonies are dropped.
                       Ideal for solos: drastically lowers note density.
  --min-gap-ms 0       minimum spacing between onsets (ms): if two notes start closer
                       than this, the louder one wins. Caps notes per second (removes
                       retriggers and unrealistic runs). Try 100-180 if it is still too dense.
"""
import argparse
import csv
from pathlib import Path

NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
MONO_OVERLAP_TOL = 0.03  # s: overlaps shorter than this do not count as a clash (legato runs)


def midi_to_name(pitch: int) -> str:
    return f"{NOTE_NAMES[pitch % 12]}{pitch // 12 - 1}"


def velocity(note) -> int:
    # note = (start_s, end_s, pitch_midi, amplitude, pitch_bends)
    return max(1, min(127, round(float(note[3]) * 127)))


def filter_min_velocity(notes, min_vel):
    return [n for n in notes if velocity(n) >= min_vel]


def thin_by_gap(notes, gap_s):
    """Enforce a minimum spacing between onsets: the louder note wins.

    Walks notes by start time; if a note starts less than gap_s after the last
    accepted one, the one with higher amplitude is kept. Effectively caps notes per second.
    """
    out = []
    for n in sorted(notes, key=lambda x: float(x[0])):
        if out and float(n[0]) - float(out[-1][0]) < gap_s:
            if float(n[3]) > float(out[-1][3]):
                out[-1] = n
        else:
            out.append(n)
    return out


def reduce_mono(notes):
    """Keep a single monophonic line: greedy by amplitude, the louder note wins.

    Walks from loudest to quietest and accepts a note only if it does not overlap
    (within tolerance) any note already accepted. Result: two notes never sound at once;
    weak notes masked by a lead (harmonies/bleed) are dropped.
    """
    kept = []
    for n in sorted(notes, key=lambda x: float(x[3]), reverse=True):
        s, e = float(n[0]), float(n[1])
        clash = any(
            s < float(k[1]) - MONO_OVERLAP_TOL and float(k[0]) < e - MONO_OVERLAP_TOL
            for k in kept)
        if not clash:
            kept.append(n)
    return kept


def notes_to_midi(notes, path: Path):
    """Rebuild the .mid from the filtered list (program = acoustic guitar)."""
    import pretty_midi

    pm = pretty_midi.PrettyMIDI()
    inst = pretty_midi.Instrument(program=25, name="guitar")  # 25 = Acoustic Guitar (steel)
    for n in notes:
        inst.notes.append(pretty_midi.Note(
            velocity=velocity(n), pitch=int(n[2]),
            start=float(n[0]), end=float(n[1])))
    pm.instruments.append(inst)
    pm.write(str(path))


def main() -> None:
    ap = argparse.ArgumentParser(description="Transcribe audio to MIDI (basic-pitch).")
    ap.add_argument("audio", help="audio file (ideally a guitar stem)")
    ap.add_argument("--out-dir", default=None)
    ap.add_argument("--min-note-ms", type=float, default=70.0)
    ap.add_argument("--min-freq", type=float, default=70.0)
    ap.add_argument("--max-freq", type=float, default=1600.0)
    ap.add_argument("--onset", type=float, default=0.5)
    ap.add_argument("--frame", type=float, default=0.3)
    ap.add_argument("--min-velocity", type=int, default=0,
                    help="drop notes with velocity < N (1-127); removes bleed/ghost notes")
    ap.add_argument("--mono", action="store_true",
                    help="collapse to monophonic (only the loudest note at each instant)")
    ap.add_argument("--min-gap-ms", type=float, default=0.0,
                    help="minimum spacing between onsets (ms); the louder note wins")
    args = ap.parse_args()

    audio = Path(args.audio)
    if not audio.exists():
        raise SystemExit(f"not found: {audio}")
    out_dir = Path(args.out_dir) if args.out_dir else Path(__file__).resolve().parent / "output"
    out_dir.mkdir(parents=True, exist_ok=True)

    from basic_pitch.inference import predict

    print(f"[transcribing] {audio.name} (the first run can be slow: it loads the model)")
    _, midi_data, notes = predict(
        str(audio),
        onset_threshold=args.onset,
        frame_threshold=args.frame,
        minimum_note_length=args.min_note_ms,
        minimum_frequency=args.min_freq,
        maximum_frequency=args.max_freq,
        melodia_trick=True,
    )

    # Optional simplification post-processing. If nothing is requested, output is unchanged.
    raw_count = len(notes)
    simplified = args.min_velocity > 0 or args.mono or args.min_gap_ms > 0
    if args.min_velocity > 0:
        notes = filter_min_velocity(notes, args.min_velocity)
    if args.mono:
        notes = reduce_mono(notes)
    if args.min_gap_ms > 0:
        notes = thin_by_gap(notes, args.min_gap_ms / 1000.0)

    # Separated stems are all named "guitar.mp3" -> prefix with the song folder name
    # so outputs from different songs do not overwrite each other.
    STEM_NAMES = {"guitar", "vocals", "drums", "bass", "piano", "other"}
    name = f"{audio.parent.name}_{audio.stem}" if audio.stem.lower() in STEM_NAMES else audio.stem
    mid_path = out_dir / f"{name}.mid"
    csv_path = out_dir / f"{name}.notes.csv"

    # When filtering, the .mid must be rebuilt from the filtered list (basic-pitch's
    # midi_data is the raw one). Without filters, the raw output is written as is (no regression).
    if simplified:
        notes_to_midi(notes, mid_path)
    else:
        midi_data.write(str(mid_path))

    notes_sorted = sorted(notes, key=lambda n: (n[0], n[2]))
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["start_s", "end_s", "midi", "note", "velocity"])
        for n in notes_sorted:
            w.writerow([f"{float(n[0]):.3f}", f"{float(n[1]):.3f}", int(n[2]),
                        midi_to_name(int(n[2])), velocity(n)])

    if simplified:
        print(f"[ok] {len(notes_sorted)} notes (from {raw_count} raw, "
              f"-{raw_count - len(notes_sorted)} simplified)")
    else:
        print(f"[ok] {len(notes_sorted)} notes detected")
    print(f"  MIDI: {mid_path}")
    print(f"  CSV:  {csv_path}")
    print("  (open the .mid in TuxGuitar/Guitar Pro/a DAW; the CSV is the raw note list)")


if __name__ == "__main__":
    main()
