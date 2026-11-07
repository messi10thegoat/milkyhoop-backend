"""Satu bentuk aksi_tersedia (schemas/aksi_tersedia): helper + penjaga -- medan skema bernama aksi_tersedia di mana pun
WAJIB bertipe AksiTersediaPeta (tak boleh ada bentuk kedua)."""
import importlib
import pathlib
import pkgutil

from app.schemas.aksi_tersedia import AksiTersedia, AksiTersediaPeta, aksi


def test_helper_alasan_hanya_bila_tak_tersedia():
    assert aksi(True, "x") == {"tersedia": True, "alasan": None}
    assert aksi(False, "Barang sudah nonaktif.") == {"tersedia": False, "alasan": "Barang sudah nonaktif."}
    assert AksiTersedia.model_validate(aksi(False, "a")).alasan == "a"


def test_semua_medan_aksi_tersedia_satu_bentuk():
    salah, ketemu = [], 0
    for m in pkgutil.iter_modules([str(pathlib.Path(__file__).resolve().parents[2] / "app" / "schemas")]):
        mod = importlib.import_module(f"app.schemas.{m.name}")
        for nama in dir(mod):
            kelas = getattr(mod, nama)
            fields = getattr(kelas, "model_fields", None)
            if isinstance(fields, dict) and "aksi_tersedia" in fields and getattr(kelas, "__module__", "") == mod.__name__:
                ketemu += 1
                if fields["aksi_tersedia"].annotation not in (AksiTersediaPeta, __import__("typing").Optional[AksiTersediaPeta]):
                    salah.append(f"{mod.__name__}.{nama}: {fields[aksi_tersedia].annotation}")
    assert ketemu >= 0 and salah == []
