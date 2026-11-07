"""BUG-081 (6 Nov 2026, temuan TESTER K-8; putusan MASTER tanpa migrasi): Tinjau ND dari faktur pembelian dulu hanya menulis "Jumlah yang
sudah dinotadebitkan per baris belum dilacak; periksa nota debit lain". Kini catatan per PRODUK "sudah diretur X dari Y" (ND terbit non-void)
+ ND DRAF sebagai peringatan, dalam SATUAN DASAR; X + draf > Y -> ND_RETUR_MELEBIHI_TAGIHAN (catatan, bukan blok -- penolak posting tetap
asal_retur). Per-baris (original_bill_item_id) = tiket sesudah putusan migrasi."""
import asyncio
import inspect
import os
from decimal import Decimal as D
from uuid import uuid4

os.environ.setdefault("OPENAI_API_KEY", "sk-boneka-unit-test-tanpa-jaringan")

import pytest  # noqa: E402

from app.services import nota_debit_gudang as NG  # noqa: E402
from app.services import satuan_dasar as SD  # noqa: E402

A, B, J = uuid4(), uuid4(), uuid4()
KTX = {"alias": {}, "barang": {str(A): {"nama": "Minyak", "dasar": "pcs", "konversi": {"dus": D(12)}},
                               str(B): {"nama": "Gula", "dasar": "kg", "konversi": {}}}}


def _bill(pid, qty, unit, nama):
    return {"product_id": pid, "quantity": D(qty), "unit": unit, "product_name": nama, "description": None}


TAGIHAN = [_bill(A, 2, "dus", "Minyak Kita"), _bill(B, 10, "kg", "Gula Pasir"), _bill(None, 1, None, "Ongkir"), _bill(J, 1, "jam", "Jasa")]


class _K:
    def __init__(self, nd):
        self.nd = nd

    async def fetch(self, sql, *a):
        assert "v.status <> 'void'" in sql and "COALESCE(v.return_bill_id, v.original_bill_id) = $2" in sql and "v.tenant_id = $1" in sql
        return self.nd


def _nd(nomor, status, pid, qty, unit):
    return {"credit_number": nomor, "status": status, "item_id": pid, "quantity": D(qty), "unit": unit}


@pytest.fixture(autouse=True)
def _ktx(monkeypatch):
    async def muat(conn, tid, pids):
        return KTX
    monkeypatch.setattr(SD, "muat", muat)


def _jalan(nd):
    return asyncio.run(NG.catatan_sudah_diretur(_K(nd), "t", uuid4(), TAGIHAN))


def test_tanpa_nd_lain_tanpa_catatan():
    assert _jalan([]) == []


def test_per_produk_satuan_dasar_terbit_dan_draf():
    c = _jalan([_nd("VC-1", "posted", A, 6, "pcs"), _nd("VC-2", "draft", A, 1, "dus"), _nd("VC-3", "applied", B, 2, "kg")])
    assert c == [
        {"code": "ND_SUDAH_DIRETUR", "message": "Minyak Kita: sudah diretur 6 dari 24 pcs; 12 lagi di draf VC-2.", "product_id": str(A)},
        {"code": "ND_SUDAH_DIRETUR", "message": "Gula Pasir: sudah diretur 2 dari 10 kg.", "product_id": str(B)},
    ]


def test_terbit_tambah_draf_melebihi_faktur_diberi_kode_peringatan():
    c = _jalan([_nd("VC-1", "posted", B, 8, "kg"), _nd("VC-4", "draft", B, 3, "kg"), _nd("VC-5", "draft", B, 1, "kg")])
    assert c == [{"code": "ND_RETUR_MELEBIHI_TAGIHAN",
                  "message": "Gula Pasir: sudah diretur 8 dari 10 kg; 4 lagi di draf VC-4, VC-5 -- melebihi jumlah di faktur.",
                  "product_id": str(B)}]


def test_produk_bukan_di_tagihan_atau_satuan_tak_terkonversi_dilewati():
    assert _jalan([_nd("VC-1", "posted", uuid4(), 5, "pcs"), _nd("VC-2", "posted", A, 1, "karung")]) == []


def test_rakit_dari_tagihan_memakai_catatan_baru_bukan_catatan_umum():
    from app.routers import vendor_credits as VC
    s = " ".join(inspect.getsource(VC.rakit_dari_tagihan).split())
    assert "notes.extend(await NG.catatan_sudah_diretur(conn, tid, bid, rows))" in s
    assert "ND_SISA_BARIS_BELUM_DILACAK" not in inspect.getsource(VC)
