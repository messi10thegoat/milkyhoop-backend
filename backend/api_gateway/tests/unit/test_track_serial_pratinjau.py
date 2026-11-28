"""KEPALA FE 28 Nov: lines[].track_serial (bool) di pratinjau kirim faktur, pemindahan, penyesuaian -- penentu SAMA dgn serial_gerak.wajib."""
import asyncio
from pathlib import Path

from app.services import serial_gerak as SG

APP = Path(__file__).resolve().parents[2] / "app"


class Kon:
    def __init__(self, seri):
        self.seri = seri

    async def fetch(self, sql, *a):
        return [{"id": i} for i in a[1] if i in self.seri]


def test_wajib_banyak(monkeypatch):
    async def nyala(conn, tid):
        return {SG.FLAG}
    import app.services.tenant_features as TF
    monkeypatch.setattr(TF, "fitur_aktif", nyala)
    assert asyncio.run(SG.wajib_banyak(Kon({"a"}), "t", ["a", "b", None])) == {"a"}
    assert asyncio.run(SG.wajib_banyak(Kon(set()), "t", [])) == set()

    async def mati(conn, tid):
        return set()
    monkeypatch.setattr(TF, "fitur_aktif", mati)
    assert asyncio.run(SG.wajib_banyak(Kon({"a"}), "t", ["a"])) == set()


def test_terpasang_di_tiga_pratinjau():
    assert '"track_serial": str(r["item_id"]) in seri' in (APP / "services/faktur_kirim_pratinjau.py").read_text()
    assert '"track_serial": str(iid) in seri' in (APP / "services/pemindahan_barang.py").read_text()
    s = (APP / "services/stok_sesuai.py").read_text()
    assert '_g["track_serial"]' in s and '"track_serial": str(b["product_id"]) in _seri' in s
