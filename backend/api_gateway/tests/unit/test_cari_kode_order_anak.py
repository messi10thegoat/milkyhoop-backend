"""Cari dokumen anak SO lewat nomor / kode order / judul order SO induk (4 Okt 2026, MASTER; temuan WORKSPACE:
pencarian faktur hanya mencocokkan so2.order_number, 5 modul lain tak mengenal SO sama sekali). Satu helper, jalur induk
= SUMBER_SO, tenant eksplisit di SETIAP subkueri. Kenyataan (data nyata) dibuktikan harness di salinan read-only."""
import inspect
import re

import pytest

from app.routers import credit_notes, customer_deposits, deliveries, proformas, receive_payments, sales_invoices
from app.services import kode_order as KO

JENIS = ["sales_invoice", "proforma", "customer_deposit", "delivery", "credit_note", "receive_payment"]


@pytest.mark.parametrize("jenis", JENIS)
def test_mencocokkan_nomor_kode_dan_judul_so_induk(jenis):
    s = KO.sql_cari_so_induk(jenis, "d", "$1", "$7")
    for kol in ("order_number", "order_code", "order_title"):
        assert f"so_c.{kol} ILIKE $7" in s, (jenis, kol)


@pytest.mark.parametrize("jenis", JENIS)
def test_tenant_eksplisit_di_setiap_tabel(jenis):
    """BYPASSRLS: tiap tabel yang disentuh subkueri WAJIB berfilter tenant $1."""
    s = KO.sql_cari_so_induk(jenis, "d", "$1", "$7")
    alias = re.findall(r"(?:FROM|JOIN) \w+ (\w+_c)\b", s)
    assert alias, s
    for a in alias:
        assert f"{a}.tenant_id = $1" in s, (jenis, a, s)


def test_jalur_induk_sama_dengan_sumber_so():
    assert set(JENIS) == set(KO.SUMBER_SO)
    assert "proforma_id" in KO.sql_cari_so_induk("customer_deposit", "d", "$1", "$2")  # DP lewat proforma
    assert "original_invoice_id" in KO.sql_cari_so_induk("credit_note", "d", "$1", "$2")  # NK lewat faktur asal
    assert "rpa_c.status = 'active'" in KO.sql_cari_so_induk("receive_payment", "d", "$1", "$2")
    with pytest.raises(ValueError):
        KO.sql_cari_so_induk("quote", "d", "$1", "$2")


@pytest.mark.parametrize("modul,fungsi,jenis", [
    (sales_invoices, "list_invoices", "sales_invoice"),
    (proformas, "list_proformas", "proforma"),
    (deliveries, "list_deliveries", "delivery"),
    (customer_deposits, "list_customer_deposits", "customer_deposit"),
    (credit_notes, "list_credit_notes", "credit_note"),
    (receive_payments, "list_receive_payments", "receive_payment"),
])
def test_daftar_memakai_helper(modul, fungsi, jenis):
    src = inspect.getsource(getattr(modul, fungsi))
    assert f'sql_cari_so_induk("{jenis}"' in src, fungsi
    assert "so2.order_number" not in src  # subkueri lama (nomor saja) sudah diganti
