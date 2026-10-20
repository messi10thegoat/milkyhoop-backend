"""S2-UM (20 Okt 2026): daftar/detail uang muka seragam -- aksi, sisa_keadaan, status_text, with_totals, summary ?search=.
Harness nyata: scripts/ops/uji-nyata-um-seragam.{py,sh}."""
import inspect
from decimal import Decimal

import pytest

from app.routers import customer_deposits as CD
from app.services import uang_muka_daftar as U

FE_R288 = {"draft": "Draf", "posted": "Diterima", "partial": "Sebagian terpakai", "applied": "Terpakai", "void": "Batal",
           "refunded": "Dikembalikan", "partially_refunded": "Sebagian dikembalikan"}


def test_sisa_semua_diturunkan_dari_satu_sql():
    s = U.sql_sisa_semua("$9")
    assert "$2" not in s and "$3" not in s and "$9" in s
    assert "unnest(" not in s and "cd0.status <> 'draft'" in s
    # selain substitusi, teksnya == SQL_SISA_UANG_MUKA (tak ada rumus kedua)
    asli = CD.SQL_SISA_UANG_MUKA.replace("ANY($2::uuid[])", "ANY(ARRAY(SELECT id FROM d))").replace("$3", "$9")
    assert s == asli.replace("unnest($2::uuid[]) AS u(x)",
                             "(SELECT cd0.id FROM customer_deposits cd0 WHERE cd0.tenant_id = $1 AND cd0.status <> 'draft') AS u(x)")


@pytest.mark.parametrize("status,sisa,harap", [
    ("draft", None, None), ("void", Decimal("0"), None), ("void", Decimal("5"), None),
    ("posted", Decimal("1"), "belum_diterapkan"), ("partial", Decimal("0.01"), "belum_diterapkan"),
    ("applied", Decimal("0"), None), ("posted", None, None),
])
def test_aksi_um(status, sisa, harap):
    assert U.aksi_um(status, sisa) == harap


@pytest.mark.parametrize("sd", sorted(FE_R288))
def test_status_text_dan_keadaan_lengkap(sd):
    assert U.status_text_um(sd) == FE_R288[sd]
    assert U.keadaan_um(sd) in U.KEADAAN_UM


@pytest.mark.parametrize("keadaan,amount,sisa,ok", [
    ("draf", 100, None, True), ("utuh", 100, Decimal("100"), True), ("utuh", 100, Decimal("50"), False),
    ("sebagian", 100, Decimal("50"), True), ("sebagian_dikembalikan", 100, Decimal("90"), True),
    ("terpakai", 100, Decimal("0"), True), ("terpakai", 100, Decimal("1"), False), ("batal", 100, Decimal("0"), True),
])
def test_keadaan_cocok_sisa(keadaan, amount, sisa, ok):
    assert U.keadaan_cocok_sisa(keadaan, amount, sisa) is ok


def test_daftar_summary_detail_satu_sumber():
    daftar = inspect.getsource(CD.list_customer_deposits)
    ringkas = inspect.getsource(CD.get_customer_deposits_summary)
    detail = inspect.getsource(CD.get_customer_deposit)
    for src in (daftar, ringkas):
        assert "_syarat_cari_um(search," in src and "_umd.sql_sisa_semua(" in src
    assert "_umd.sql_aksi(aksi)" in daftar and "_umd.sql_aksi(_umd.AKSI_BELUM_DITERAPKAN)" in ringkas
    for src in (daftar, detail):
        for f in ("_umd.aksi_um(", "_umd.keadaan_um(", "_umd.status_text_um("):
            assert f in src, f
    assert "with_totals is True" in daftar
    # remaining_state lama tetap (FE live membacanya)
    assert '"draft_belum_diposting"' in daftar and '"draft_belum_diposting"' in detail
