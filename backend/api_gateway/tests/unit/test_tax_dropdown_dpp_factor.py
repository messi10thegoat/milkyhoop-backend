"""Dropdown kode pajak membawa faktor DPP (temuan W3, 25 Sep 2026).

Dulu GET /api/tax-codes/dropdown tak mengirim dpp_factor_num/den (V289) -> pratinjau
LOKAL FE untuk PPN 12% ber-DPP 11/12 menghitung PPN penuh (angka server benar).
`direction` juga dikirim router tapi dibuang diam-diam oleh response_model.

Tes melewati jalur yang sama dengan FastAPI: hasil router divalidasi lewat
response_model rute (bukan kelas yang ditebak), dan DB tiruan hanya mengembalikan
kolom yang benar-benar di-SELECT -> SELECT tanpa kolom faktor = merah.
"""
import re
import uuid
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.routers import tax_codes as TC

TENANT = "kaos-biru-konveksi"

BARIS = [
    {"id": uuid.uuid4(), "code": "PPN-12-OUT", "name": "PPN 12%", "rate": Decimal("12.00"),
     "tax_type": "ppn", "direction": "output", "is_default": True,
     "dpp_factor_num": 11, "dpp_factor_den": 12},
    {"id": uuid.uuid4(), "code": "PPN-11-OUT", "name": "PPN 11%", "rate": Decimal("11.00"),
     "tax_type": "ppn", "direction": "output", "is_default": False,
     "dpp_factor_num": 1, "dpp_factor_den": 1},
]


class DB:
    def __init__(self):
        self.sql = None

    async def fetch(self, sql, *a):
        self.sql = sql
        kolom = re.search(r"SELECT(.*?)FROM", sql, re.S).group(1)
        nama = [k.strip() for k in kolom.split(",")]
        return [{k: b[k] for k in nama} for b in BARIS]


class Pool:
    def __init__(self, db):
        self.db = db

    def acquire(self):
        db = self.db

        class A:
            async def __aenter__(self):
                return db

            async def __aexit__(self, *e):
                return False
        return A()


def _rute_dropdown():
    cocok = [r for r in TC.router.routes if getattr(r, "path", "").endswith("/dropdown")]
    assert len(cocok) == 1
    return cocok[0]


@pytest.fixture
def db(monkeypatch):
    d = DB()

    async def _pool():
        return Pool(d)
    monkeypatch.setattr(TC, "get_pool", _pool)
    return d


def _panggil():
    req = SimpleNamespace(state=SimpleNamespace(user={"tenant_id": TENANT, "user_id": None}), headers={})
    return TC.get_tax_dropdown(req, tax_type=None, direction="output")


@pytest.mark.asyncio
async def test_faktor_dpp_sampai_ke_respons(db):
    hasil = await _panggil()
    model = _rute_dropdown().response_model
    keluar = model.model_validate(hasil).model_dump()  # = penyaringan response_model FastAPI
    per_kode = {i["code"]: i for i in keluar["items"]}
    assert (per_kode["PPN-12-OUT"]["dpp_factor_num"], per_kode["PPN-12-OUT"]["dpp_factor_den"]) == (11, 12)
    assert (per_kode["PPN-11-OUT"]["dpp_factor_num"], per_kode["PPN-11-OUT"]["dpp_factor_den"]) == (1, 1)
    assert per_kode["PPN-12-OUT"]["direction"] == "output"


@pytest.mark.asyncio
async def test_saring_tenant_dan_aktif_tetap(db):
    await _panggil()
    assert "tenant_id = $1" in db.sql and "is_active = true" in db.sql


def test_skema_menuntut_faktor_bilangan_bulat():
    item = _rute_dropdown().response_model.model_fields["items"].annotation.__args__[0]
    for medan in ("dpp_factor_num", "dpp_factor_den", "direction"):
        assert medan in item.model_fields
    assert item.model_fields["dpp_factor_num"].annotation is int
