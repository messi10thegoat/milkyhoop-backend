"""REKON-LAMA-PAGAR (10 Nov 2026, TESTER #12 T5; putusan pemilik Bank Feed P2/P3): menyelesaikan rekonsiliasi lama TAK PERNAH menulis jurnal
penutup selisih. Penyesuaian ditolak; selisih dihitung ULANG (kolom difference sesi bisa NULL) dan wajib 0 persis (dulu toleransi 100)."""
import inspect
from decimal import Decimal

import pytest

from app.routers import bank_reconciliation as BR


@pytest.mark.parametrize("selisih,penyesuaian,kode", [
    ([Decimal("0"), Decimal("0")], [], None),
    ([Decimal("1172500"), Decimal("1172500")], [], "SELISIH_REKONSILIASI"),
    ([Decimal("50"), Decimal("0")], [], "SELISIH_REKONSILIASI"),          # dulu lolos (toleransi 100)
    ([Decimal("0"), Decimal("0")], [object()], "REKON_PENYESUAIAN_DITOLAK"),
    ([None, None], [], "SELISIH_REKONSILIASI"),
])
def test_blok_selesai(selisih, penyesuaian, kode):
    b = BR.blok_selesai(selisih, penyesuaian)
    assert (b and b["code"]) == kode


def test_kalimat_rupiah():
    assert BR.blok_selesai([Decimal("150000")], [])["message"].startswith("Selisih Rp 150.000 harus nol")


def test_selesai_tanpa_jurnal_dan_selisih_dihitung_ulang():
    src = inspect.getsource(BR.complete_session)
    assert "INSERT INTO journal_entries" not in src and "RECONCILIATION_ADJUSTMENT" not in src and "reconciliation_adjustments" not in src
    assert "blok_selesai(" in src and "SQL_TERCOCOK_SESI" in src and "status_code=409" in src
    assert "s.tenant_id = $2" in BR.SQL_TERCOCOK_SESI


def test_penanda():
    assert BR.PENANDA_REKON_LAMA_PAGAR == "rekon-lama-pagar-w20261110"
