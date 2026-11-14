"""ND-GUDANG (3 Nov 2026; desain OK MASTER, putusan B biaya WAC): retur barang Nota Debit keluar dari gudang ASAL tagihan (kartu BILL − BILL_VOID
+ GRN tertaut), dibatasi sisa retur dan stok gudang itu; tolak 400 berkalimat (teks_galat) -- penentu SATU nota_debit_gudang untuk penulis
(kunci tagihan + FOR UPDATE stok) dan pratinjau (tanpa kunci). Dulu: gudang PERTAMA tanpa cek -> 500 / gudang salah diam-diam."""
import ast
import asyncio
import inspect
import uuid
from decimal import Decimal as D

import pytest
from fastapi import HTTPException

from app.routers import vendor_credits as RVC
from app.services import nota_debit_gudang as NG
from app.services import nota_debit_rencana as NDR
from app.services import teks_galat as tg

TID, BILL, G1, G2, P = "t", uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4()


def test_kalimat_persis_pola_nk():
    # FE ND hanya punya jenis Retur barang (CONVERSATIONAL/MASTER 3 Nov) -> kalimat tak menunjuk alasan lain
    assert tg.nd_barang_belum_masuk("Kaos", "PB-1") == "Barang Kaos belum pernah masuk lewat tagihan PB-1, jadi tidak bisa diretur ke vendor."
    assert tg.nd_retur_melebihi("Kaos", D("3"), D("2")) == "Retur Kaos 3 melebihi yang masuk dan belum diretur (2)."
    assert tg.nd_stok_kurang("Kaos", "Gudang B", D("1"), D("2")) == "Stok Kaos di Gudang B tinggal 1; retur 2 tidak bisa diposting."


class Kon:
    """Koneksi tiruan: jawaban per potongan SQL; mencatat urutan SQL (kunci sebelum hitung)."""
    def __init__(self, bill_rows=(), void_rows=(), grn_rows=(), sudah=0, stok=None, pertama=G1):
        self.q, self.bill_rows, self.grn_rows, self.sudah, self.stok, self.pertama = [], list(bill_rows) + list(void_rows), list(grn_rows), sudah, stok or {}, pertama

    async def execute(self, sql, *a):
        self.q.append(" ".join(sql.split()))

    async def fetch(self, sql, *a):
        s = " ".join(sql.split()); self.q.append(s)
        if "source_type IN ('BILL', 'BILL_VOID')" in s:
            return [{"warehouse_id": g, "q": q} for g, q in self.bill_rows]
        if "goods_receipt_bill_links" in s:
            return [{"warehouse_id": g, "q": q} for g, q in self.grn_rows]
        return []

    async def fetchval(self, sql, *a):
        s = " ".join(sql.split()); self.q.append(s)
        if "FROM warehouses WHERE tenant_id = $1 AND NOT is_system ORDER BY is_default DESC NULLS LAST, created_at" in s:
            return self.pertama
        if "VENDOR_CREDIT_VOID" in s:
            return self.sudah
        if "FROM warehouse_stock" in s:
            return self.stok.get(a[1])
        if "SELECT name FROM warehouses" in s:
            return {G1: "Gudang A", G2: "Gudang B"}.get(a[0])
        return None

    async def fetchrow(self, sql, *a):
        self.q.append(" ".join(sql.split()))
        return {"id": P, "item_code": "K1", "nama_produk": "Kaos", "track_inventory": True}


def _vc(bill=BILL):
    return {"id": uuid.uuid4(), "original_bill_id": bill, "original_bill_number": "PB-1" if bill else None}


def _jalan(kon, vc, qty, kunci=False):
    bl = NDR.Blok(not kunci)
    out = asyncio.run(NG.rencana_keluar(kon, TID, vc, [{"id": "r1", "item_id": P, "quantity": qty}], kunci=kunci, bl=bl))
    return out, bl.respons()


def test_gudang_masuk_terbesar_bill_dan_grn():
    kon = Kon(bill_rows=[(G1, D("3"))], grn_rows=[(G2, D("5"))], stok={G2: D("10")})
    out, blok = _jalan(kon, _vc(), 2)
    assert blok == [] and out[0]["gudang_id"] == G2 and out[0]["gudang"] == "Gudang B" and out[0]["qty"] == D("2")


def test_bill_void_tak_dihitung_masuk():
    """Kartu BILL − BILL_VOID = 0 -> barang dianggap tak pernah masuk (tagihan dibatalkan)."""
    kon = Kon(bill_rows=[(G1, D("0"))], stok={G1: D("10")})
    _, blok = _jalan(kon, _vc(), 1)
    assert blok == [{"code": "ND_BARANG_BELUM_MASUK", "message": tg.nd_barang_belum_masuk("Kaos", "PB-1")}]
    assert any("g.status <> 'void'" in s and "l.status = 'active'" in s for s in kon.q)  # GRN batal / tautan nonaktif tak dihitung


def test_melebihi_sisa_retur_nd_lain():
    kon = Kon(bill_rows=[(G1, D("5"))], sudah=D("4"), stok={G1: D("10")})
    _, blok = _jalan(kon, _vc(), 2)
    assert blok == [{"code": "ND_RETUR_MELEBIHI_SISA", "message": tg.nd_retur_melebihi("Kaos", D("2"), D("1"))}]


def test_stok_gudang_kurang_400_bukan_500():
    kon = Kon(bill_rows=[(G1, D("5"))], stok={G1: D("1")})
    _, blok = _jalan(kon, _vc(), 2)
    assert blok == [{"code": "ND_STOK_KURANG", "message": tg.nd_stok_kurang("Kaos", "Gudang A", D("1"), D("2"))}]


def test_tanpa_tagihan_asal_gudang_default_tetap_cek_stok():
    kon = Kon(stok={G1: D("0")})
    _, blok = _jalan(kon, _vc(bill=None), 1)
    assert blok == [{"code": "ND_STOK_KURANG", "message": tg.nd_stok_kurang("Kaos", "Gudang A", D("0"), D("1"))}]
    out, blok = _jalan(Kon(stok={G1: D("5")}), _vc(bill=None), 1)
    assert blok == [] and out[0]["gudang_id"] == G1


def test_penulis_melempar_400_kalimat():
    with pytest.raises(HTTPException) as e:
        _jalan(Kon(bill_rows=[(G1, D("5"))], stok={G1: D("1")}), _vc(), 2, kunci=True)
    assert e.value.status_code == 400 and e.value.detail == tg.nd_stok_kurang("Kaos", "Gudang A", D("1"), D("2"))


def test_penulis_kunci_tagihan_sebelum_hitung_dan_for_update_stok():
    kon = Kon(bill_rows=[(G1, D("5"))], stok={G1: D("5")})
    _jalan(kon, _vc(), 1, kunci=True)
    i_kunci = next(i for i, s in enumerate(kon.q) if "pg_advisory_xact_lock" in s)
    i_hitung = next(i for i, s in enumerate(kon.q) if "source_type IN ('BILL', 'BILL_VOID')" in s)
    assert i_kunci < i_hitung and any("FROM warehouse_stock" in s and s.endswith("FOR UPDATE") for s in kon.q)
    kon2 = Kon(bill_rows=[(G1, D("5"))], stok={G1: D("5")})
    _jalan(kon2, _vc(), 1, kunci=False)
    assert not any("pg_advisory" in s or "FOR UPDATE" in s for s in kon2.q)  # pratinjau READ ONLY tanpa kunci


def test_jumlah_beberapa_baris_produk_sama_dibatasi_bersama():
    kon = Kon(bill_rows=[(G1, D("3"))], stok={G1: D("10")})
    bl = NDR.Blok(True)
    asyncio.run(NG.rencana_keluar(kon, TID, _vc(), [{"id": "a", "item_id": P, "quantity": 2}, {"id": "b", "item_id": P, "quantity": 2}],
                                  kunci=False, bl=bl))
    assert [b["code"] for b in bl.respons()] == ["ND_RETUR_MELEBIHI_SISA"]


def test_penulis_dan_pratinjau_memakai_penentu_yang_sama():
    src_w = inspect.getsource(RVC.post_vendor_credit)
    assert "ORDER BY created_at LIMIT 1" not in src_w and '_r["retur"]' in src_w  # BUG-053: rencana_retur(keluar)
    src_r = inspect.getsource(NDR.rencana_posting)
    assert "NG.rencana_keluar(" in src_r and "kunci=not pratinjau" in src_r
    src_h = inspect.getsource(NDR.rencana_retur) + inspect.getsource(NDR.stok_retur)
    assert "FROM warehouses" not in src_h and '"gudang_id"' in src_h and '_r["retur"]' in src_w and 'warehouse_id=_b["gudang_id"]' in src_w


def test_tanpa_asal_memakai_gudang_default_seperti_fpb():
    """Putusan MASTER: ND tanpa tagihan asal = gudang is_default (tempat FPB memasukkan stok), BUKAN gudang pertama dibuat."""
    src = inspect.getsource(NG.rencana_keluar)
    assert "ORDER BY is_default DESC NULLS LAST, created_at LIMIT 1" in src and "ORDER BY created_at LIMIT 1" not in src
    from app.services import bills_service as BS
    assert "is_default = true" in inspect.getsource(BS)
