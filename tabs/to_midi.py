"""audio (stem de guitarra) -> notas MIDI. Etapa 1 (AMT) del pipeline a tablatura.

Usa basic-pitch (Spotify, MIT). Saca .mid + notes.csv (start, end, pitch, nombre, velocity).
NO da cuerda/traste todavia (eso es Etapa 2). Monofonico (solos) sale bien;
acordes/distorsion salen rough -> tratar la salida como BORRADOR para corregir por oido.

Uso (desde la carpeta tabs/):
  .venv-tabs\\Scripts\\python.exe to_midi.py <audio.wav|mp3> [opciones]

Opciones (con defaults pensados para guitarra):
  --out-dir DIR        carpeta de salida (default: tabs/output)
  --min-note-ms 70     nota mas corta a detectar (ms). Bajar p/ solos rapidos.
  --min-freq 70        frecuencia minima Hz (~mi grave = 82, drop D = 73)
  --max-freq 1600      frecuencia maxima Hz (trastes agudos)
  --onset 0.5          umbral de ataque (0-1). Subir = menos notas fantasma.
  --frame 0.3          umbral de sostenido (0-1).

Simplificacion (post-proceso, para que sea tocable en guitarra):
  --min-velocity 0     descarta notas con velocity < N (1-127). Mata bleed de otros
                       instrumentos y notas fantasma debiles. Probar 25-45.
  --mono               colapsa a monofonico: en cada instante queda solo la nota mas
                       fuerte (la melodia/lead), descarta acordes y armonias simultaneas.
                       Ideal para solos: baja drasticamente la densidad de notas.
  --min-gap-ms 0       espaciado minimo entre ataques (ms): si dos notas arrancan mas
                       cerca que esto, queda la mas fuerte. Limita notas/seg (mata
                       retriggers y runs irreales). Probar 100-180 si sigue muy denso.
"""
import argparse
import csv
from pathlib import Path

NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
MONO_OVERLAP_TOL = 0.03  # s: solape menor a esto no cuenta como choque (runs legato)


def midi_to_name(pitch: int) -> str:
    return f"{NOTE_NAMES[pitch % 12]}{pitch // 12 - 1}"


def velocity(note) -> int:
    # note = (start_s, end_s, pitch_midi, amplitude, pitch_bends)
    return max(1, min(127, round(float(note[3]) * 127)))


def filter_min_velocity(notes, min_vel):
    return [n for n in notes if velocity(n) >= min_vel]


def thin_by_gap(notes, gap_s):
    """Impone un espaciado minimo entre ataques: gana la nota mas fuerte.

    Recorre por tiempo de inicio; si una nota arranca a menos de gap_s de la ultima
    aceptada, se queda la de mayor amplitud. Cap efectivo de notas por segundo.
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
    """Deja una linea monofonica: voraz por amplitud, gana la nota mas fuerte.

    Recorre de mas fuerte a mas debil y acepta una nota solo si no se solapa
    (con tolerancia) con ninguna ya aceptada. Resultado: nunca suenan dos a la vez;
    las notas debiles tapadas por una lead (armonias/bleed) se caen.
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
    """Reconstruye el .mid desde la lista filtrada (programa = guitarra acustica)."""
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
    ap = argparse.ArgumentParser(description="Transcribe audio a MIDI (basic-pitch).")
    ap.add_argument("audio", help="archivo de audio (idealmente un stem de guitarra)")
    ap.add_argument("--out-dir", default=None)
    ap.add_argument("--min-note-ms", type=float, default=70.0)
    ap.add_argument("--min-freq", type=float, default=70.0)
    ap.add_argument("--max-freq", type=float, default=1600.0)
    ap.add_argument("--onset", type=float, default=0.5)
    ap.add_argument("--frame", type=float, default=0.3)
    ap.add_argument("--min-velocity", type=int, default=0,
                    help="descarta notas con velocity < N (1-127); mata bleed/fantasmas")
    ap.add_argument("--mono", action="store_true",
                    help="colapsa a monofonico (solo la nota mas fuerte por instante)")
    ap.add_argument("--min-gap-ms", type=float, default=0.0,
                    help="espaciado minimo entre ataques (ms); gana la mas fuerte")
    args = ap.parse_args()

    audio = Path(args.audio)
    if not audio.exists():
        raise SystemExit(f"no existe: {audio}")
    out_dir = Path(args.out_dir) if args.out_dir else Path(__file__).resolve().parent / "output"
    out_dir.mkdir(parents=True, exist_ok=True)

    from basic_pitch.inference import predict

    print(f"[transcribiendo] {audio.name} (puede tardar la primera vez: carga el modelo)")
    _, midi_data, notes = predict(
        str(audio),
        onset_threshold=args.onset,
        frame_threshold=args.frame,
        minimum_note_length=args.min_note_ms,
        minimum_frequency=args.min_freq,
        maximum_frequency=args.max_freq,
        melodia_trick=True,
    )

    # Post-proceso de simplificacion (opcional). Si no se pide nada, queda igual que antes.
    raw_count = len(notes)
    simplified = args.min_velocity > 0 or args.mono or args.min_gap_ms > 0
    if args.min_velocity > 0:
        notes = filter_min_velocity(notes, args.min_velocity)
    if args.mono:
        notes = reduce_mono(notes)
    if args.min_gap_ms > 0:
        notes = thin_by_gap(notes, args.min_gap_ms / 1000.0)

    # Los stems separados se llaman todos "guitar.mp3" -> usar el nombre de la carpeta
    # de la cancion como prefijo para no pisar salidas entre canciones.
    STEM_NAMES = {"guitar", "vocals", "drums", "bass", "piano", "other"}
    name = f"{audio.parent.name}_{audio.stem}" if audio.stem.lower() in STEM_NAMES else audio.stem
    mid_path = out_dir / f"{name}.mid"
    csv_path = out_dir / f"{name}.notes.csv"

    # Si filtramos, hay que reconstruir el .mid desde la lista filtrada (el midi_data
    # de basic-pitch es el crudo). Sin filtro, escribimos el crudo tal cual (sin regresion).
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
        print(f"[ok] {len(notes_sorted)} notas (de {raw_count} crudas, "
              f"-{raw_count - len(notes_sorted)} simplificadas)")
    else:
        print(f"[ok] {len(notes_sorted)} notas detectadas")
    print(f"  MIDI: {mid_path}")
    print(f"  CSV:  {csv_path}")
    print("  (abri el .mid en TuxGuitar/Guitar Pro/DAW; el CSV es la lista cruda de notas)")


if __name__ == "__main__":
    main()
