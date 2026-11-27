"""Serial S4 (27 Nov 2026): baca dibuka (teks server), tulis lepas lama diparkir 409 berarah, pratinjau ND menilai serial,
pembalikan void memakai jenis gerak yang SAH di CHECK chk_sm_type (V048)."""
import asyncio
import inspect
import re
from pathlib import Path

import pytest
from fastapi import HTTPException

from app.services import serial_baca as SB, serial_gerak as SG

MIG = Path(__file__).resolve().parents[3] / "migrations"


def test_jenis_gerak_sah_di_check_v048():
    isi = (MIG / "V048__serial_tracking.sql").read_text()
    sah = set(re.findall(r"'(\w+)'", isi[isi.index("chk_sm_type"):isi.index("chk_sm_type") + 400]))
    assert {SG.GERAK_MASUK, SG.GERAK_KELUAR, SG.GERAK_BALIK} <= sah


def test_status_teks_lengkap_sesuai_check_v513():
    isi = (MIG / "V513__serial_taut_ledger.sql").read_text()
    baris = next(l for l in isi.splitlines() if "CHECK (status IN" in l)
    assert set(re.findall(r"'(\w+)'", baris)) == SB.STATUS_SAH
    assert SB.status_teks("available") == "Tersedia" and SB.status_teks(None) == "-"
    assert SB.gerak_teks({"from_status": None}) == "Masuk"
    assert SB.gerak_teks({"from_status": "available", "to_status": "sold"}) == "Tersedia → Terjual"


def test_gudang_hanya_saat_di_tangan():
    from datetime import datetime
    r = {"id": 1, "item_id": 2, "serial_number": "A1", "status": "sold", "warehouse_id": 3, "warehouse_name": "Utama",
         "received_date": None, "created_at": datetime(2026, 11, 27), "dok_no": "FJ-1"}
    b = SB.baris(r)
    assert b["gudang_text"] == "-" and b["dokumen_terakhir_text"] == "FJ-1" and b["status_text"] == "Terjual"
    assert SB.baris({**r, "status": "available"})["gudang_text"] == "Utama"


@pytest.mark.parametrize("nama", ["create_item_serial", "bulk_create_serials", "transfer_serial", "adjust_serial_status"])
def test_tulis_lama_diparkir_berarah(nama):
    from app.routers import item_serials as R
    rute = next(r for r in R.router.routes if r.endpoint.__name__ == nama)
    assert rute.methods == {"POST"} and len(rute.dependencies) == 1
    with pytest.raises(HTTPException) as e:
        asyncio.run(rute.dependencies[0].dependency())
    assert e.value.status_code == 409 and e.value.detail["code"] == "FEATURE_NOT_AVAILABLE"
    assert "Penerimaan Barang" in e.value.detail["message"] or "Pemindahan Barang" in e.value.detail["message"]


def test_baca_tidak_diparkir():
    from app.routers import item_serials as R
    for r in R.router.routes:
        if "GET" in r.methods:
            assert not r.dependencies, r.path
    src = inspect.getsource(R)
    assert "get_available_serials(" not in src and "get_serial_history(" not in src   # fungsi DB V048 rusak tak dipanggil


class Kon:
    def __init__(self, baris):
        self.baris = baris

    async def fetch(self, sql, *a):
        return self.baris


def test_blok_tersedia():
    g = "G1"
    k = Kon([{"serial_number": "A1", "status": "available", "warehouse_id": g},
             {"serial_number": "A2", "status": "sold", "warehouse_id": g}])
    assert asyncio.run(SG.blok_tersedia(k, "t", "p", "Kipas", g, ["a1"])) is None
    code, pesan = asyncio.run(SG.blok_tersedia(k, "t", "p", "Kipas", g, ["A1", "A2", "Z9"]))
    assert code == "SERIAL_TAK_TERSEDIA" and "tidak dikenal: Z9" in pesan and "tidak tersedia di gudang ini: A2" in pesan
    assert asyncio.run(SG.blok_tersedia(Kon([]), "t", "p", "Kipas", g, [])) is None


def test_pratinjau_nd_menilai_serial():
    from app.services import nota_debit_gudang as NG
    src = inspect.getsource(NG.rencana_keluar)
    assert "track_serial FROM products" in src and "SG.blok_serial(" in src and "SG.blok_tersedia(" in src
