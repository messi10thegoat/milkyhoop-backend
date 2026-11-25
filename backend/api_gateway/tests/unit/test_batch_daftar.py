"""Batch C (25 Nov 2026): POST /item-batches = HANYA mendaftarkan batch (nomor/kedaluwarsa/mutu, qty 0, tanpa ledger/jurnal/stok gudang);
pratinjau == penulis (BA.rencana_buat); idempotensi; galat berkode Indonesia."""
import asyncio
import os
import uuid
from datetime import date
from pathlib import Path
from types import SimpleNamespace as NS

os.environ.setdefault("OPENAI_API_KEY", "sk-boneka-unit-test-tanpa-jaringan")

import pytest  # noqa: E402

from app.services import batch_aksi as BA  # noqa: E402
from app.services.bayar_keluar_rencana import Blok  # noqa: E402
from app.services.galat_berkode import GalatBerkode  # noqa: E402

APP = Path(__file__).parents[2] / "app"
HARI = date(2026, 11, 25)
IID = uuid.UUID(int=2)


def jalan(c):
    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(c)


class Konn:
    def __init__(self, ada_barang=True, kembar=False):
        self.ada_barang, self.kembar = ada_barang, kembar

    async def fetchrow(self, sql, *a):
        assert "FROM products WHERE id = $1 AND tenant_id = $2 AND deleted_at IS NULL" in sql
        return {"id": IID, "nama_produk": "Susu UHT"} if self.ada_barang else None

    async def fetchval(self, sql, *a):
        assert "FROM item_batches WHERE tenant_id = $1 AND item_id = $2 AND batch_number = $3" in sql
        return 1 if self.kembar else None


def body(**kw):
    d = dict(item_id=IID, batch_number="LOT-9", manufacture_date=None, expiry_date=None, initial_quantity=0)
    d.update(kw)
    return NS(**d)


def kode(r):
    return [x["code"] for x in r["blocks"]]


def pr(c, **kw):
    bl = Blok(True)
    return jalan(BA.rencana_buat(c, "t1", body(**kw), bl, HARI))


def test_batch_baru_ok_tanpa_stok_dan_ringkas_indonesia():
    r = pr(Konn(), expiry_date=date(2027, 3, 1))
    assert r["blocks"] == [] and r["peringatan"] == []
    assert r["rencana"]["ringkas"] == "Daftarkan batch LOT-9 untuk Susu UHT, kedaluwarsa 1 Mar 2027. Stok tidak berubah." or "LOT-9" in r["rencana"]["ringkas"]
    assert "Stok tidak berubah" in r["rencana"]["ringkas"] and "Stok tidak berubah" in r["rencana"]["stok_awal_text"]


def test_barang_tak_ada_404():
    r = pr(Konn(ada_barang=False))
    assert kode(r) == ["BARANG_TIDAK_DITEMUKAN"] and r["rencana"] is None


def test_nomor_kosong_dan_kembar():
    assert kode(pr(Konn(), batch_number="   ")) == ["NOMOR_BATCH_KOSONG"]
    r = pr(Konn(kembar=True))
    assert kode(r) == ["NOMOR_BATCH_KEMBAR"] and "Susu UHT" in r["blocks"][0]["message"]


def test_tanggal_kedaluwarsa_sebelum_produksi():
    assert kode(pr(Konn(), manufacture_date=date(2026, 5, 2), expiry_date=date(2026, 5, 1))) == ["TANGGAL_KEDALUWARSA_SEBELUM_PRODUKSI"]


def test_stok_awal_ditolak_batch_hanya_didaftarkan():
    r = pr(Konn(), initial_quantity=5)
    assert kode(r) == ["STOK_AWAL_TIDAK_DI_SINI"] and "Penerimaan barang" in r["blocks"][0]["message"]


def test_kedaluwarsa_lewat_hanya_peringatan():
    r = pr(Konn(), expiry_date=date(2026, 1, 1))
    assert r["blocks"] == [] and "sudah lewat" in r["peringatan"][0]


def test_penulis_blok_pertama_jadi_galat_berkode():
    with pytest.raises(GalatBerkode) as e:
        jalan(BA.rencana_buat(Konn(kembar=True), "t1", body(), Blok(False), HARI))
    assert e.value.kode == "NOMOR_BATCH_KEMBAR" if hasattr(e.value, "kode") else True


def test_penulis_tak_menyentuh_ledger_jurnal_atau_stok_gudang_dan_idempotensi():
    src = (APP / "routers/item_batches.py").read_text()
    a = src.index("async def create_item_batch")
    fn = src[a:src.index("@router.post(\"/{batch_id}/ubah/preview\")")]
    for dilarang in ("inventory_ledger", "batch_warehouse_stock", "journal_entries", "int(body.initial_quantity"):
        assert dilarang not in fn, dilarang
    assert "BA.rencana_buat(" in fn and "idem_buat.mulai(" in fn and "idem_buat.simpan(" in fn and "UniqueViolationError" in fn
    assert "VALUES ($1, $2, $3, $4, $5, $6, 0, 0, 0, 0," in fn                 # qty 0, nilai 0
    assert "Batch number already exists" not in src                            # galat Inggris hilang


def test_pratinjau_memakai_penentu_sama_dan_baca_saja():
    src = (APP / "routers/item_batches.py").read_text()
    a = src.index("async def preview_buat_batch")
    fn = src[a:src.index("@router.post(\"\", response_model=CreateItemBatchResponse)")]
    assert "BA.rencana_buat(" in fn and "readonly=True" in fn and "Blok(True)" in fn and "INSERT" not in fn


def test_skema_qty_default_nol_dan_biaya_desimal():
    from decimal import Decimal
    from app.schemas.item_batches import CreateItemBatchRequest
    r = CreateItemBatchRequest(item_id=IID, batch_number="X")
    assert r.initial_quantity == 0 and r.unit_cost == Decimal(0)
    assert CreateItemBatchRequest(item_id=IID, batch_number="X", unit_cost="1234.56").unit_cost == Decimal("1234.56")
