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
    # Rekap SO draf tetap "terbit" (bundel SO tak berubah oleh ekstraksi)
    assert 'draf=d["kind"] == "invoice"' in inspect.getsource(DBN.rampungkan)


def test_izin_rute_bundel_faktur():
    assert izin_untuk_jalur("/api/sales-invoices/0b3c/documents", "GET") == ("sales_invoice", "R")
    assert izin_untuk_jalur("/api/sales-orders/0b3c/documents", "GET") == ("sales_order", "R")


def test_kepala_faktur_dari_sumber_detail():
    src = inspect.getsource(DK.susun_dokumen_faktur)
    assert "_t43_dibayar_dan_sisa(" in src and "posisi_faktur(" in src and "sisa_baris(" in src
    assert '"default": {"group": "ki"' in src
