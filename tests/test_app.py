"""Tests de app.py. Correr con el venv GPU (importa torch):

    .venv-gpu\\Scripts\\python.exe -m pytest tests -q

Cubren la logica pura (slugify, calibracion de ratio) y los endpoints nuevos
(descarga zip) + el guard anti path-traversal compartido por zip y borrado.
No tocan el pipeline de separacion (no encolan jobs reales).
"""
import io
import json
import sys
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import app  # noqa: E402

# base_url local: el middleware only_local rechaza hosts que no sean 127.0.0.1/localhost
client = TestClient(app.app, base_url="http://127.0.0.1")


# ---------- logica pura ----------

def test_slugify_basico():
    assert app.slugify("Hello World!") == "hello-world"


def test_slugify_acentos():
    assert app.slugify("Cafe Tacvba - Ingrata") == "cafe-tacvba-ingrata"
    assert app.slugify("Mana Corazon Espinado") == "mana-corazon-espinado"


def test_slugify_vacio_o_solo_simbolos():
    assert app.slugify("") == "cancion"
    assert app.slugify("***") == "cancion"


def test_ratios_by_model_mediana_y_filtros():
    done = [
        {"model": "htdemucs", "duration": 100, "time_sec": 20},  # 0.2
        {"model": "htdemucs", "duration": 100, "time_sec": 40},  # 0.4
        {"model": "htdemucs", "duration": 100, "time_sec": 30},  # 0.3 -> mediana
        {"model": "bs_roformer_sw", "duration": 10, "time_sec": 5},  # 0.5
        {"model": "x", "duration": 0, "time_sec": 5},      # descartado (dur=0)
        {"model": None, "duration": 10, "time_sec": 5},    # descartado (sin modelo)
        {"model": "y", "duration": 10, "time_sec": None},  # descartado (sin tiempo)
    ]
    r = app.ratios_by_model(done)
    assert r["htdemucs"] == pytest.approx(0.3)
    assert r["bs_roformer_sw"] == pytest.approx(0.5)
    assert "x" not in r and "y" not in r


def test_ratios_by_model_vacio():
    assert app.ratios_by_model([]) == {}


# ---------- fixture de cancion falsa ----------

@pytest.fixture
def fake_song():
    sid = "zz-pytest-fixture"
    d = app.SONGS / sid
    d.mkdir(parents=True, exist_ok=True)
    for stem in ("vocals", "drums", "bass"):
        (d / f"{stem}.mp3").write_bytes(b"ID3fake-mp3-bytes")
    meta = {"id": sid, "name": "Pytest Fixture", "model": "bs_roformer_sw",
            "device": "cpu", "duration": 12.0, "time_sec": 6.0,
            "stems": ["vocals", "drums", "bass"], "processed_at": "2026-06-13T00:00:00"}
    (d / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    yield sid
    import shutil
    shutil.rmtree(d, ignore_errors=True)


# ---------- endpoints ----------

def test_songs_lista_incluye_la_cancion(fake_song):
    data = client.get("/api/songs").json()
    assert "device" in data
    assert any(s["id"] == fake_song for s in data["done"])


def test_download_zip_ok(fake_song):
    r = client.get(f"/api/songs/{fake_song}/download.zip")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/zip"
    z = zipfile.ZipFile(io.BytesIO(r.content))
    names = z.namelist()
    assert f"{fake_song}/vocals.mp3" in names
    assert len(names) == 3


def test_download_zip_id_invalido():
    assert client.get("/api/songs/Bad_Id/download.zip").status_code == 400


def test_download_zip_inexistente():
    assert client.get("/api/songs/no-existe-xyz/download.zip").status_code == 404


def test_delete_id_invalido():
    assert client.delete("/api/songs/Bad_Id").status_code == 400


def test_delete_inexistente():
    assert client.delete("/api/songs/no-existe-xyz").status_code == 404


def test_delete_borra_de_verdad(fake_song):
    assert client.delete(f"/api/songs/{fake_song}").status_code == 200
    assert not (app.SONGS / fake_song).exists()


def test_middleware_rechaza_host_ajeno():
    # DNS rebinding: Host que no es local -> 400 antes de tocar cualquier endpoint
    r = client.get("/api/songs", headers={"host": "evil.example"})
    assert r.status_code == 400


def test_middleware_rechaza_origin_cruzado_en_post():
    # CSRF: POST con Origin de otro sitio -> 403 (aunque el sid ni exista)
    r = client.post("/api/jobs/lo-que-sea/cancel", headers={"origin": "http://evil.example"})
    assert r.status_code == 403


def test_middleware_acepta_origin_local():
    r = client.post("/api/jobs/no-existe-xyz/cancel", headers={"origin": "http://127.0.0.1:8765"})
    assert r.status_code == 404   # pasa el middleware y cae en el 404 real del endpoint


def test_upload_rechaza_calidad_invalida():
    # quality desconocida -> 422 antes de encolar (no dispara separacion real)
    files = {"file": ("x.mp3", b"fake", "audio/mpeg")}
    r = client.post("/api/upload", files=files, data={"quality": "ultraturbo"})
    assert r.status_code == 422


def test_quality_mapea_overlap_por_tipo():
    assert app.QUALITY["alta"]["audio_separator"] == 16
    assert app.QUALITY["normal"]["audio_separator"] == 8
    assert app.QUALITY[app.DEFAULT_QUALITY]["audio_separator"] == 8
