"""Pembayaran Keluar berbasis WRAPPER + turunan alokasi (PEMBELIAN-SERAGAM unit (2), 22 Okt 2026). Nyata: scripts/ops/uji-nyata-bayar-keluar."""
import inspect
from decimal import Decimal

import pytest

from app.routers import bill_payments as BP
from app.schemas import bill_payments as SBP
from app.services import bills_service as BS
from app.services import pembayaran_keluar_daftar as PK
from app.services import penerimaan_daftar as PD


@pytest.mark.parametrize("status,bruto,dia,harap", [
    ("posted", 1000, 1000, ("penuh", 1000, 0, None)),
    ("posted", 1000, 400, ("sebagian", 400, 600, "belum_dialokasikan")),
    ("posted", 1000, 0, ("belum", 0, 1000, "belum_dialokasikan")),
    ("posted", 1000, 1200, ("penuh", 1200, 0, None)),            # lebih alokasi (diskon): belum tak negatif
    ("voided", 1000, 1000, ("tanpa", 0, 0, None)),
    ("draft", 1000, 0, ("tanpa", 0, 0, None)),
])
def test_alokasi_fakta(status, bruto, dia, harap):
    f = PK.alokasi_fakta(status, Decimal(bruto), Decimal(dia))
    assert (f["alokasi_keadaan"], f["dialokasikan"], f["belum_dialokasikan"], f["aksi"]) == harap
    t = PK.dengan_turunan(f)
    if status == "posted":
        assert t["allocated_amount"] == f["dialokasikan"] and t["unapplied_amount"] == f["belum_dialokasikan"]
    else:
        assert "allocated_amount" not in t  # cache wrapper tak ditimpa untuk non-posted


def test_teks_sama_dengan_penerimaan():
    assert PK.TEKS == PD.TEKS and PK.AKSI == PD.AKSI


def test_sql_turunan():
    assert "a.payment_id = bp.id" in PK.sql_dialokasi() and "bp.status = 'posted'" in PK.sql_dialokasi()
    assert PK.sql_bruto() == "(bp.total_amount + COALESCE(bp.discount_amount, 0))"
    assert "GREATEST(" in PK.sql_belum()


def test_daftar_dan_summary_berbasis_wrapper():
    daftar = inspect.getsource(BP.list_bill_payments)
    ring = inspect.getsource(BP.get_bill_payments_summary)
    for src in (daftar, ring):
        assert "settlements" not in src and "account_type = 'PAYABLE'" not in src  # bukan lagi jurnal ber-debit AP
    assert "FROM bill_payments_v2 bp" in daftar and "WHERE bp.tenant_id = $1" in daftar
    assert "_pk.sql_dialokasi('bp')" in daftar and "_pk.dengan_turunan(_pk.alokasi_fakta(" in daftar
    assert "ORDER BY {sort_col} {order}, q.id {order}" in daftar      # tiebreak id
    # 3 = ringkasan + per metode + pill belum_dialokasikan (kontrak seragam 22 Okt 2026); semuanya wrapper
    assert ring.count("FROM bill_payments_v2") == 3 and "!= 'voided'" in ring


def test_detail_memakai_turunan_yang_sama():
    src = inspect.getsource(BP.get_bill_payment)
    assert "_pk.dengan_turunan(_pk.alokasi_fakta(" in src
    assert 'allocated_amount=_f.get("allocated_amount", payment["allocated_amount"] or 0)' in src


def test_skema_membawa_medan_baru():
    for m in (SBP.BillPaymentListItem, SBP.BillPaymentDetail):
        for k in ("dialokasikan", "belum_dialokasikan", "aksi", "alokasi_keadaan", "alokasi_teks"):
            assert k in m.model_fields, (m.__name__, k)


def test_void_status_di_dalam_transaksi():
    """Dulu UPDATE withholding + status='voided' di LUAR `async with conn.transaction()` (autocommit sesudah jurnal void)."""
    src = inspect.getsource(BP.void_bill_payment).split("\n")
    trx = next(i for i, l in enumerate(src) if "async with conn.transaction():" in l)
    ind_trx = len(src[trx]) - len(src[trx].lstrip())
    for kunci in ("UPDATE withholding_tax_records", "UPDATE bill_payments_v2"):
        i = next(i for i, l in enumerate(src) if kunci in l)
        eks = next(j for j in range(i, 0, -1) if "await conn.execute(" in src[j])
        assert len(src[eks]) - len(src[eks].lstrip()) > ind_trx, kunci


def test_record_payment_mengisi_cache_alokasi():
    src = inspect.getsource(BS.BillsService.record_payment)
    assert "allocated_amount, unapplied_amount" in src and "$10, 0" in src
