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


def test_predikat_sql_satu_sumber_dengan_terbuka():
    p = SI._syarat_minggu_ini("$7", "$8")
    assert SI._STATUS_TERBUKA_AR in p and SI._SISA_AR_POSITIF in p
    assert "si.due_date >= $7::date" in p and "si.due_date <= $8::date" in p
    daftar, ringkas = inspect.getsource(SI.list_invoices), inspect.getsource(SI.get_invoice_summary)
    assert "_syarat_minggu_ini(" in daftar and "_syarat_minggu_ini(" in ringkas
    assert daftar.count("akhir_minggu(hari_ini)") >= 1 and ringkas.count("akhir_minggu(hari_ini)") >= 1


def test_kontrak_medan():
    lit = typing.get_args(typing.get_args(inspect.signature(SI.list_invoices).parameters["aksi"].annotation)[0])
    assert SI.AKSI_MINGGU_INI in lit and "segera_tempo" in lit          # segera_tempo TETAP
    assert "jatuh_tempo_minggu_ini" in InvoiceListItem.model_fields
    assert {"jatuh_tempo_minggu_ini_count", "jatuh_tempo_minggu_ini_value"} <= set(InvoiceSummary.model_fields)
