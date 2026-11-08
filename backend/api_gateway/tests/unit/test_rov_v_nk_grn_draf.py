"""ROW (v) 9 Nov 2026 (MASTER): BUG-147 status_text NK yang kelebihannya pindah ke saldo kredit pelanggan (KRD) = "Jadi saldo kredit" (satu
pembentuk status_text_nk: daftar, detail, dokumen, pratinjau aksi); BUG-136 (putusan MASTER (1)) GRN yang sisanya tertutup faktur DRAF:
status_text "<billing> · faktur draf" (partisi pill & billing_state tetap) + faktur draf tampil di DOKUMEN berstatus "draf" (satu definisi
dgn draft_bills ROW r = SQL_DRAF_TAGIHAN)."""
import asyncio
import inspect
import uuid
from datetime import date
from decimal import Decimal as D

import pytest

from app.routers import credit_notes as RC
from app.routers import dokumen as RD
from app.services import grn_daftar as GD
from app.services import nota_kredit_daftar as NKD
from app.services import nota_kredit_rencana as NKR


# ---------------------------------------------------------------- BUG-147
@pytest.mark.parametrize("status, detail, fid, kredit, harap", [
    ("posted", "posted", "f1", True, "Jadi saldo kredit"),      # bertaut posted = applied efektif + KRD
    ("posted", "applied", None, True, "Jadi saldo kredit"),
    ("posted", "posted", "f1", False, "Terpakai"),               # tanpa KRD: kata lama
    ("posted", "partial", None, True, "Sebagian terpakai"),
    ("void", "void", "f1", True, "Batal"),
    ("posted", "posted", None, False, "Terbit"),
])
def test_status_text_nk_saldo_kredit(status, detail, fid, kredit, harap):
    assert NKD.status_text_nk(status, detail, fid, saldo_kredit=kredit) == harap


def test_bawaan_tanpa_argumen_perilaku_lama():
    assert NKD.status_text_nk("posted", "posted", "f1") == "Terpakai"


def test_pratinjau_sesudah_kelebihan_jadi_saldo_kredit_keadaan_tetap():
    k = NKR.teks_keadaan_nk({"status": "posted", "status_detail": "posted", "original_invoice_id": "f1",
                             "customer_credit": {"deposit_number": None, "status": "posted", "remaining": 300000.0}})
    assert (k["status_text"], k["sisa_keadaan"]) == ("Jadi saldo kredit", "terpakai")
    k2 = NKR.teks_keadaan_nk({"status": "posted", "status_detail": "posted", "original_invoice_id": "f1", "customer_credit": None})
    assert k2["status_text"] == "Terpakai"


def test_daftar_detail_dokumen_memakai_fakta_krd():
    src = inspect.getsource(RC)
    assert src.count('saldo_kredit=row.get("created_deposit_id") is not None') == 1
    assert src.count('saldo_kredit=cn["created_deposit_id"] is not None') == 1
    d = inspect.getsource(RD)
    assert 'saldo_kredit=c["created_deposit_id"] is not None' in d and "cn.amount_refunded, cn.original_invoice_id, cn.created_deposit_id" in d


# ---------------------------------------------------------------- BUG-136 (a)
@pytest.mark.parametrize("bs, ak, harap", [
    ("belum_ditagih", "draf_tagihan", "Belum ditagih · faktur draf"),
    ("sebagian_ditagih", "draf_tagihan", "Sebagian ditagih · faktur draf"),
    ("belum_ditagih", "belum_ditagih", "Belum ditagih"),
    ("sudah_ditagih", None, "Sudah ditagih"),
    ("batal", None, "Dibatalkan"),
    (None, None, "Diterima"),
])
def test_teks_tagih_satu_pembentuk(bs, ak, harap):
    assert GD.teks_tagih(bs, ak) == harap


def test_medan_draf_menutup_sisa_teks_dua_dimensi_billing_tetap():
    r = {"status": "posted", "qty_dasar": D("5"), "qty_tertaut": D("0"), "qty_draf": D("5"), "grni_terbuka": D("100")}
    m = GD.medan(r)
    assert (m["billing_state"], m["aksi"], m["status_text"]) == ("belum_ditagih", "draf_tagihan", "Belum ditagih · faktur draf")
    m2 = GD.medan({**r, "qty_draf": D("2")})
    assert (m2["aksi"], m2["status_text"]) == ("belum_ditagih", "Belum ditagih")


# ---------------------------------------------------------------- BUG-136 (b)
GR, PO, B1, B2 = uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4()


class K:
    def __init__(self, posted=True):
        self.posted, self.sql = posted, []

    async def fetch(self, sql, *a):
        self.sql.append(sql)
        if "FROM purchase_orders" in sql:
            return [{"id": PO, "po_number": "PO-1", "po_date": date(2026, 11, 1), "total_amount": D("10"), "status": "received", "sent_at": None}]
        if "goods_receipt_bill_links" in sql:
            return [{"gr_id": GR, "id": B1, "invoice_number": "BILL-1", "issue_date": date(2026, 11, 2), "amount": D("4"), "keadaan": "posted"}] if self.posted else []
        if sql == GD.SQL_DRAF_TAGIHAN:
            return [{"gr_id": GR, "id": B2, "invoice_number": "BILL-2", "issue_date": date(2026, 11, 3), "amount": D("6"), "created_at": None}]
        return []


def _kel(monkeypatch, kon):
    async def lapis(conn, tid, h):
        return h
    monkeypatch.setattr(GD, "lapis_kirim", lapis)
    g = {"id": GR, "purchase_order_id": PO, "status": "posted", "receipt_number": "GRN-1", "receipt_date": date(2026, 11, 2), "amount": D("10")}
    return asyncio.run(GD.kelompok_grn_banyak(kon, "t1", [g]))[str(GR)]


def test_dokumen_grn_memuat_faktur_draf_berstatus_draf(monkeypatch):
    kel = _kel(monkeypatch, K())
    f = next(k for k in kel if k["key"] == "faktur")
    assert [(d["number"], d["status"]) for d in f["docs"]] == [("BILL-1", "diposting"), ("BILL-2", "draf")]


def test_dokumen_grn_hanya_draf_tetap_ada_kelompok_faktur(monkeypatch):
    kel = _kel(monkeypatch, K(posted=False))
    assert [(d["number"], d["status"]) for k in kel if k["key"] == "faktur" for d in k["docs"]] == [("BILL-2", "draf")]
    assert [d["type"] for d in GD.documents_baris(kel)] == ["purchase_order", "goods_receipt", "bill"]


def test_draf_satu_definisi_dengan_draft_bills():
    assert "b.issue_date, b.amount" in GD.SQL_DRAF_TAGIHAN and GD.PRED_DRAF in GD.SQL_DRAF_TAGIHAN
    assert "GD.SQL_DRAF_TAGIHAN" not in inspect.getsource(GD.kelompok_grn_banyak)  # modul sendiri: nama tanpa awalan
    assert "SQL_DRAF_TAGIHAN" in inspect.getsource(GD.kelompok_grn_banyak)
