"""Nota Debit U3c (23 Okt 2026, KONTRAK-nota-debit-u3c-20261023.md): daftar/detail/bundel seragam, KEMBAR Nota Kredit (S2-NK).
Simetri diuji LANGSUNG terhadap fungsi NK; pill hanya belum_terpakai; remaining_amount rumus lama; kamus bundel FE pembelian."""
import typing
from pathlib import Path
from uuid import uuid4

import pytest

from app.routers import credit_notes as CN
from app.services import nota_debit_daftar as NDD
from app.services import nota_kredit_daftar as NKD
from app.services import so_riwayat as SR
from app.services import teks_galat as tg

APP = Path(__file__).resolve().parents[2] / "app"
STATUS = ("draft", "posted", "partial", "applied", "void")


def test_pill_hanya_belum_terpakai_sama_nk():
    assert NDD.AKSI_ND == NKD.AKSI_NK == ("belum_terpakai",)
    assert NDD.KEADAAN_ND == NKD.KEADAAN_NK
    with pytest.raises(ValueError):
        NDD.sql_aksi("perlu_diposting")


@pytest.mark.parametrize("status", STATUS)
@pytest.mark.parametrize("tautan", [None, uuid4()])
@pytest.mark.parametrize("sisa", [None, 0, 1000])
def test_aksi_kembar_nk(status, tautan, sisa):
    assert NDD.aksi_nd(status, tautan, sisa) == NKD.aksi_nk(status, tautan, sisa)


@pytest.mark.parametrize("status", STATUS)
@pytest.mark.parametrize("dipakai,kembali", [(0, 0), (0, 500), (500, 0), (300, 200)])
@pytest.mark.parametrize("tautan", [None, uuid4()])
def test_keadaan_dan_teks_kembar_nk(status, dipakai, kembali, tautan):
    sd = NDD.status_detail_nd(status, dipakai, kembali)
    assert sd == CN.status_detail_nk(status, dipakai, kembali)
    assert NDD.keadaan_nd(status, sd, tautan) == NKD.keadaan_nk(status, sd, tautan)
    assert NDD.status_text_nd(status, sd, tautan) == NKD.status_text_nk(status, sd, tautan)


def test_bertaut_posted_terpakai():
    t = uuid4()
    assert NDD.keadaan_nd("posted", "posted", t) == "terpakai" and NDD.status_text_nd("posted", "posted", t) == "Terpakai"
    assert NDD.aksi_nd("posted", t, 1000) is None


def test_peta_status_vc_sama_cn():
    assert tg._STATUS["vc"] == tg._STATUS["cn"] and tg._DOKUMEN["vc"] == "Nota debit"


def test_sql_aksi_memakai_sisa_jurnal_u3a():
    from app.services.nota_debit import sql_sisa_nd
    s = NDD.sql_aksi("belum_terpakai")
    assert sql_sisa_nd() in s and "original_bill_id IS NULL" in s and "NOT IN ('draft', 'void')" in s


def test_sisa_lama_tak_berubah():
    assert NDD.sisa_lama("void", 1000, 0, 0) == 0
    assert NDD.sisa_lama("posted", 1000, 300, 200) == 500
    assert NDD.sisa_lama("draft", 1000, None, None) == 1000
    r = (APP / "routers/vendor_credits.py").read_text()
    assert r.count("NDD.sisa_lama(") == 2  # daftar + detail, satu rumus


def test_syarat_cari_per_kata_dan_nomor_parameter():
    assert NDD.syarat_cari("  ", 2) == ("TRUE", [])
    sql, p = NDD.syarat_cari("abc  def", 5, a="vc")
    assert p == ["%abc%", "%def%"] and "$5" in sql and "$6" in sql and "$7" not in sql and "vc.original_bill_number" in sql


def test_dokumen_dan_kelompok_kamus_fe_pembelian():
    v = {"id": uuid4(), "credit_number": "VC-1", "credit_date": None, "total_amount": 1000, "status": "posted"}
    assert NDD.dok_nota_debit(v)["kind"] == "debit_note" and NDD.dok_nota_debit(v)["status"] == "diposting"
    assert NDD.dok_nota_debit({**v, "status": "draft"})["status"] == "draf"
    assert NDD.dok_nota_debit({**v, "status": "void"})["status"] == "batal" and NDD.dok_nota_debit({**v, "status": "void"})["batal"]
    src = (APP / "services/nota_debit_daftar.py").read_text()
    assert 'g["key"] in ("po", "faktur")' in src and '"key": "nota_debit"' in src  # kamus FE: po · faktur · nota_debit, TERAKHIR


def test_medan_baris_lengkap():
    v = {"id": uuid4(), "status": "posted", "amount_applied": 0, "amount_received": 0, "original_bill_id": None, "original_bill_number": None}
    m = NDD.medan_baris(v, 1000, {"terpakai": 0, "dikembalikan": 0}, [], None)
    assert m["aksi"] == "belum_terpakai" and m["aksi_semua"] == ["belum_terpakai"] and m["sisa_keadaan"] == "utuh"
    assert set(m) >= {"aksi", "aksi_semua", "sisa_keadaan", "status_text", "terpakai", "dikembalikan", "original_bill_id",
                      "original_bill_number", "purchase_order_id", "purchase_order_number", "documents", "doc_count"}


def test_rute_dan_skema():
    from app.routers import vendor_credits as VR
    from app.schemas import vendor_credits as VS
    lit = [p.annotation for p in __import__("inspect").signature(VR.list_vendor_credits).parameters.values() if p.name == "aksi"][0]
    assert set(typing.get_args(typing.get_args(lit)[0])) == {"belum_terpakai"}
    for f in ("aksi", "aksi_semua", "sisa_keadaan", "status_text", "terpakai", "dikembalikan", "documents", "doc_count"):
        assert f in VS.VendorCreditListItem.model_fields and f in VS.VendorCreditDetail.model_fields
    assert "sum_sisa" in VS.VendorCreditListResponse.model_fields
    paths = {r.path for r in VR.router.routes}
    assert {"/{vendor_credit_id}/documents", "/{vendor_credit_id}/history"} <= paths
    src = (APP / "routers/vendor_credits.py").read_text()
    assert 'f"{NDD.AKSI_BELUM_TERPAKAI}_count"' in src and 'f"{NDD.AKSI_BELUM_TERPAKAI}_value"' in src
    assert '"paid": ' in src and 'if terbit else None' in src  # jangkar null bukan-posted


def test_audit_entitas_nota_debit():
    assert SR.ENTITAS_AUDIT["vendor_credits"] == "debit_note"


def test_kelompok_nd_menerima_id_str_dan_uuid():
    import asyncio
    vid = uuid4()

    class K:
        async def fetch(self, sql, *a):
            assert all(type(x).__name__ == "UUID" for x in a[1])
            return [{"id": vid, "credit_number": "VC-1", "credit_date": None, "total_amount": 1, "status": "draft", "original_bill_id": None}]
    for kunci in (vid, str(vid)):
        h = asyncio.run(NDD.kelompok_nd(K(), "t", [{"id": kunci}]))
        assert [g["key"] for g in h[kunci]] == ["nota_debit"]
