"""Chip Faktur "Jatuh tempo minggu ini" (paket listpage P3, 16 Okt 2026, MASTER). Predikat SQL satu sumber
(routers/sales_invoices._syarat_minggu_ini) dipakai ?aksi= DAN /summary; kembaran Python faktur_daftar.jatuh_tempo_minggu_ini.
Harness nyata (baris daftar ?aksi= == baris berflag == summary count, +search): scripts/ops/uji-nyata-chip-minggu.py."""
import inspect
import typing
from datetime import date, timedelta

import pytest

from app.routers import sales_invoices as SI
from app.schemas.sales_invoices import InvoiceListItem, InvoiceSummary
from app.services import faktur_daftar as FD
from app.services.dashboard_v2 import akhir_minggu

KAMIS = date(2026, 10, 15)          # Kamis -> Minggu 18 Okt
MINGGU = date(2026, 10, 18)


@pytest.mark.parametrize("hari,due,terbuka,harap", [
    (KAMIS, KAMIS, True, True), (KAMIS, MINGGU, True, True), (KAMIS, MINGGU + timedelta(days=1), True, False),
    (KAMIS, KAMIS - timedelta(days=1), True, False),     # telat bukan "minggu ini"
    (KAMIS, KAMIS, False, False), (KAMIS, None, True, False),
    (MINGGU, MINGGU, True, True), (MINGGU, MINGGU + timedelta(days=1), True, False),   # Minggu = hari terakhir minggu
])
def test_kembaran_python(hari, due, terbuka, harap):
    assert akhir_minggu(KAMIS) == MINGGU
    assert FD.jatuh_tempo_minggu_ini(terbuka, due, hari) is harap


def test_chip_minggu_ini_usang_alias_segera_tempo():
    """22 Okt 2026 (KONTRAK-dashboard-pengingat §7, putusan pemilik): SATU definisi "segera" = N hari tenant. ?aksi=jatuh_tempo_minggu_ini
    dan summary jatuh_tempo_minggu_ini_* = ALIAS USANG segera_tempo (dipertahankan sampai FE T1 terverifikasi); predikat akhir-minggu dicabut."""
    daftar, ringkas = inspect.getsource(SI.list_invoices), inspect.getsource(SI.get_invoice_summary)
    assert "if aksi == AKSI_MINGGU_INI" in daftar and 'aksi = "segera_tempo"' in daftar
    assert 'aksi_row["segera_tempo_count"]' in ringkas and 'aksi_row["segera_tempo_value"]' in ringkas
    assert "_syarat_minggu_ini(" not in daftar and "_syarat_minggu_ini(" not in ringkas


def test_kontrak_medan():
    lit = typing.get_args(typing.get_args(inspect.signature(SI.list_invoices).parameters["aksi"].annotation)[0])
    assert SI.AKSI_MINGGU_INI in lit and "segera_tempo" in lit          # segera_tempo TETAP
    assert "jatuh_tempo_minggu_ini" in InvoiceListItem.model_fields
    assert {"jatuh_tempo_minggu_ini_count", "jatuh_tempo_minggu_ini_value"} <= set(InvoiceSummary.model_fields)
