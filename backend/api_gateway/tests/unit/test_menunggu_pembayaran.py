"""MENUNGGU-PEMBAYARAN (23 Okt 2026, KONTRAK-menunggu-bayar): pill SO menunggu_pembayaran (proforma terbit belum lunas APA PUN
jenisnya), alias usang menunggu_dp, saring Proforma belum_dibayar, pengingat dashboard."""
import inspect
from datetime import date
from decimal import Decimal as D

import pytest

from app.services import pengingat as P
from app.services import proforma_aksi as PA
from app.services import so_aksi as A
from app.services import so_posisi as SP

PEL, DP, TER = {"purpose": "PELUNASAN"}, {"purpose": "DP"}, {"purpose": "TERMIN"}


@pytest.mark.parametrize("st,sisa,bb,fak,harap", [
    ("confirmed", D("10"), [PEL], False, True),        # pelunasan PENUH belum dibayar (dulu di luar semua pill bayar)
    ("confirmed", D("10"), [DP], False, True),
    ("confirmed", D("10"), [TER], True, True),         # termin + faktur: tetap (hanya PELUNASAN yang dilewati)
    ("confirmed", D("10"), [PEL], True, False),        # PELUNASAN + faktur terbit: faktur yang menagih
    ("confirmed", D("10"), [PEL, DP], True, True),
    ("confirmed", D("0"), [DP], False, False),         # sisa SO 0
    ("cancelled", D("10"), [DP], False, False),
    ("completed", D("10"), [DP], False, False),
    ("draft", D("10"), [DP], False, False),
    ("confirmed", D("10"), [], False, False),
])
def test_menunggu_pembayaran_murni(st, sisa, bb, fak, harap):
    assert SP.menunggu_pembayaran(st, sisa, bb, fak) is harap


def test_teks_posisi_menunggu_dp_tak_berubah():
    assert "menunggu_dp(status, sisa, belum_bayar, ada_faktur)" in inspect.getsource(SP.posisi)


def test_kunci_dan_alias():
    assert "menunggu_pembayaran" in A.AKSI and "menunggu_dp" not in A.AKSI
    assert A.KUNCI_BAYAR == ("perlu_minta_dp", "menunggu_pembayaran")
    assert A.AKSI_USANG == {"menunggu_dp": "menunggu_pembayaran"}
    assert A.aksi_baris(["telat_kirim", "menunggu_pembayaran"]) == ["telat_kirim", "menunggu_dp"]
    f = {1: {"aksi": ["menunggu_pembayaran"], "total": D("5"), "nilai": {"menunggu_pembayaran": D("3")}},
         2: {"aksi": ["perlu_ditagih"], "total": D("7"), "nilai": {}}}
    assert A.id_aksi(f, "menunggu_dp") == A.id_aksi(f, "menunggu_pembayaran") == [1]
    r = A.ringkasan(f)
    assert (r["menunggu_pembayaran_count"], r["menunggu_pembayaran_value"]) == (1, 3.0)
    assert (r["menunggu_dp_count"], r["menunggu_dp_value"]) == (1, 3.0)


def test_kunci_sah_memeriksa_tipe():
    from fastapi import Query
    assert A.kunci_sah("menunggu_dp") and A.kunci_sah("menunggu_pembayaran") and A.kunci_sah("telat_kirim")
    assert not A.kunci_sah("asing") and not A.kunci_sah(None) and not A.kunci_sah(Query(None))


def test_rute_so_menerima_kunci_baru_dan_alias():
    import typing
    from app.routers import sales_orders as SO
    ann = inspect.signature(SO.list_sales_orders).parameters["aksi"].annotation
    lit = typing.get_args([a for a in typing.get_args(ann) if a is not type(None)][0])
    assert "menunggu_pembayaran" in lit and "menunggu_dp" in lit
    src = inspect.getsource(SO.list_sales_orders)
    assert "so_aksi.kunci_sah(aksi)" in src and "so_aksi.aksi_baris(" in src


def test_schema_summary_memuat_kunci_baru_dan_alias():
    from app.schemas.sales_orders import SalesOrderSummary
    f = SalesOrderSummary.model_fields
    assert {"menunggu_pembayaran_count", "menunggu_pembayaran_value", "menunggu_dp_count", "menunggu_dp_value"} <= set(f)


def test_pengingat_registri_menunggu_pembayaran():
    kunci = {(m, k): lbl for m, k, lbl, *_ in P.PENGINGAT}
    assert kunci[("sales_orders", "menunggu_pembayaran")] == "Menunggu bayar"
    assert ("sales_orders", "menunggu_dp") not in kunci
    assert P.KUNCI_BELUM_DITAGIH == (("sales_orders", "perlu_minta_dp"),)  # menunggu_pembayaran = sudah ditagih


def test_proforma_belum_dibayar_gabungan_kunci_terbit_bukan_pill():
    assert PA.BELUM_DIBAYAR == ("menunggu_bayar", "segera_tempo", "lewat_tempo") and PA.SARING == ("belum_dibayar",)
    assert "belum_dibayar" not in PA.AKSI
    h = date(2026, 10, 23)
    for due in (None, date(2026, 10, 1), date(2026, 10, 25), date(2026, 12, 1)):
        assert PA.aksi_proforma("issued", "confirmed", False, due, h, 7) in PA.BELUM_DIBAYAR
    assert PA.aksi_proforma("issued", "confirmed", True, None, h, 7) is None       # lunas
    assert PA.aksi_proforma("issued", "cancelled", False, None, h, 7) is None      # SO batal
    assert PA.aksi_proforma("draft", "confirmed", False, None, h, 7) not in PA.BELUM_DIBAYAR


def test_rute_proforma_saring_belum_dibayar():
    import typing
    from app.routers import proformas as PR
    ann = inspect.signature(PR.list_proformas).parameters["aksi"].annotation
    lit = typing.get_args([a for a in typing.get_args(ann) if a is not type(None)][0])
    assert "belum_dibayar" in lit
    src = inspect.getsource(PR.list_proformas)
    assert "isinstance(aksi, str) and aksi in _pa.SARING" in src
