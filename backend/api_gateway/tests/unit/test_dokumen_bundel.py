"""S1-U2 (20 Okt 2026, MASTER): pembentuk dokumen per-kind BERSAMA (services/dokumen_bundel), jangkar generik.

Bundel SO dan bundel faktur memakai pembentuk YANG SAMA (pemilihan id saja yang khas jangkar). Bukti nyata (SO lama == baru,
faktur ⊆ SO, jumlah == documents[] daftar): scripts/ops/uji-nyata-dokumen-faktur.{py,sh}.
"""
import inspect

import pytest

from app.middleware.permission_middleware import izin_untuk_jalur
from app.routers import dokumen as DK
from app.services import dokumen_bundel as DBN

PEMBENTUK = ("penawaran(", "proforma_so(", "kwitansi_uang_muka(", "kwitansi_penerimaan(", "surat_jalan(", "faktur(",
             "nota_kredit(", "rampungkan(")


@pytest.mark.parametrize("fn", (DK.susun_dokumen, DK.susun_dokumen_faktur))
def test_jangkar_memakai_pembentuk_bersama(fn):
    src = inspect.getsource(fn)
    for p in PEMBENTUK:
        assert f"DB.{p}" in src, p
    # tak ada SQL bentuk dokumen tersalin di jangkar (rekening/jenis kwitansi hanya di pembentuk)
    for salinan in ("LEFT JOIN LATERAL", "sql_jenis_efektif", "label_rekening", "label_metode", "keadaan_kirim"):
        assert salinan not in src, salinan


def test_subtipe_kwitansi_satu_definisi():
    assert DK.SUBTIPE_KWITANSI is DBN.SUBTIPE_KWITANSI


@pytest.mark.parametrize("kind,batal,paid,draf,harap", [
    ("invoice", False, False, True, "draf"),
    ("invoice", True, False, True, "batal"),   # batal menang
    ("invoice", False, False, False, "diposting"),
    ("invoice", False, True, False, "lunas"),
])
def test_status_draf_faktur(kind, batal, paid, draf, harap):
    assert DK.status_dokumen(kind, batal, paid, draf=draf) == harap


def test_draf_hanya_untuk_faktur():
    # Rekap SO draf tetap "terbit" (bundel SO tak berubah oleh ekstraksi). PENJUALAN-SERAGAM (21 Okt): jangkar PROFORMA juga memuat dirinya walau draf
    # (bundel SO tak pernah memuat proforma draf -> harness SO lama == baru tetap); kind lain tidak.
    assert 'draf=d["kind"] in ("invoice", "proforma")' in inspect.getsource(DBN.rampungkan)


def test_izin_rute_bundel_faktur():
    assert izin_untuk_jalur("/api/sales-invoices/0b3c/documents", "GET") == ("sales_invoice", "R")
    assert izin_untuk_jalur("/api/sales-orders/0b3c/documents", "GET") == ("sales_order", "R")


def test_kepala_faktur_dari_sumber_detail():
    src = inspect.getsource(DK.susun_dokumen_faktur)
    assert "_t43_dibayar_dan_sisa(" in src and "posisi_faktur(" in src and "sisa_baris(" in src
    assert '"default": {"group": "ki"' in src


class _Conn:
    def __init__(self, rows):
        self.rows = rows

    async def fetch(self, sql, *args):
        return self.rows


def test_kwitansi_uang_muka_nominal_jangkar():
    """Data nyata hari ini: setiap uang muka diterapkan PENUH ke satu faktur -> harness nyata buta untuk kasus sebagian
    (kontrol merah R2 lolos). Kasus sebagian dikunci di sini: nominal = bagian yang diterapkan, medan lain identik."""
    import asyncio
    from datetime import date
    from decimal import Decimal
    from uuid import uuid4
    rid = uuid4()
    row = {"id": rid, "deposit_number": "UM-2610-0001", "deposit_date": date(2026, 10, 1), "amount": Decimal("500000"),
           "payment_method": "bank_transfer", "bank_name": "BCA", "account_number": "123", "account_holder_name": "A",
           "account_name": "BCA Masuk", "coa_name": "Bank", "jenis": "DP"}
    penuh = asyncio.run(DBN.kwitansi_uang_muka(_Conn([row]), "t", [rid]))[rid]
    sebagian = asyncio.run(DBN.kwitansi_uang_muka(_Conn([row]), "t", [rid], {rid: Decimal("200000")}))[rid]
    assert penuh["amount"] == "500000.00" and sebagian["amount"] == "200000.00"
    assert {k: v for k, v in penuh.items() if k != "amount"} == {k: v for k, v in sebagian.items() if k != "amount"}
    assert penuh["receipt_kind"] == "uang_muka" and "subtype" not in penuh


def test_bundel_faktur_memberi_nominal_bagian_faktur():
    src = inspect.getsource(DK.susun_dokumen_faktur)
    assert "DB.kwitansi_uang_muka(conn, tid, list(jumlah), jumlah)" in src
    assert "status = 'active' GROUP BY 1" in src   # penerapan AKTIF saja
