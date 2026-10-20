"""S2-NK (20 Okt 2026): daftar/detail nota kredit seragam -- aksi belum_terpakai, sisa jurnal, sisa_keadaan, status_text,
with_totals, summary ?search=. Harness nyata: scripts/ops/uji-nyata-nk-seragam.{py,sh} (sisa jurnal == sisa_kredit_nk)."""
import inspect
from decimal import Decimal

import pytest

from app.routers import credit_notes as CN
from app.services import nota_kredit_daftar as N

FE_R288 = {"draft": "Draf", "posted": "Terbit", "partial": "Sebagian terpakai", "applied": "Terpakai", "void": "Batal",
           "refunded": "Dikembalikan", "partially_refunded": "Sebagian dikembalikan"}


@pytest.mark.parametrize("status,inv,sisa,harap", [
    ("draft", None, Decimal("0"), None), ("void", None, Decimal("0"), None), ("posted", "f", Decimal("0"), None),
    ("posted", None, Decimal("10"), "belum_terpakai"), ("partial", None, Decimal("1"), "belum_terpakai"),
    ("applied", None, Decimal("0"), None), ("posted", None, None, None),
])
def test_aksi_nk(status, inv, sisa, harap):
    assert N.aksi_nk(status, inv, sisa) == harap


@pytest.mark.parametrize("sd", sorted(FE_R288))
def test_status_text_bebas_sama_dengan_fe(sd):
    assert N.status_text_nk(sd if sd in ("draft", "posted", "partial", "applied", "void") else "partial", sd, None) == FE_R288[sd]
    assert N.keadaan_nk(sd, sd, None) in N.KEADAAN_NK


def test_bertaut_posted_terpakai():
    # putusan MASTER 20 Okt: NK bertaut 'posted' (kredit sudah memotong faktur) = "Terpakai" (NetSuite Fully Applied)
    assert N.status_text_nk("posted", "posted", "inv-1") == "Terpakai"
    assert N.keadaan_nk("posted", "posted", "inv-1") == "terpakai"
    assert N.status_text_nk("posted", "posted", None) == "Terbit"


def test_sql_sisa_nk_jurnal_bukan_cache():
    s = N.sql_sisa_nk()
    assert "is_effective_journal(je.id)" in s and "account_type = 'RECEIVABLE'" in s and "je.tenant_id = $1" in s
    assert "amount_applied" not in s and "amount_refunded" not in s and "total_amount" not in s
    assert "original_invoice_id IS NOT NULL THEN 0" in s


def test_daftar_summary_detail_satu_sumber():
    daftar, ringkas, detail = (inspect.getsource(f) for f in (CN.list_credit_notes, CN.get_credit_notes_summary, CN.get_credit_note))
    for src in (daftar, ringkas):
        assert "_syarat_cari_nk(search," in src
    assert "_nkd.sql_aksi(aksi)" in daftar and "_nkd.sql_aksi(_nkd.AKSI_BELUM_TERPAKAI)" in ringkas
    for src in (daftar, detail):
        for f in ("_nkd.aksi_nk(", "_nkd.keadaan_nk(", "_nkd.status_text_nk(", "_nkd.sisa_nk_jurnal_many("):
            assert f in src, f
    assert "with_totals is True" in daftar
    # penjaga lama tak berubah: remaining_amount tetap sisa_kredit_nk
    assert "sisa_kredit_nk(" in daftar and "sisa_kredit_nk(" in detail
