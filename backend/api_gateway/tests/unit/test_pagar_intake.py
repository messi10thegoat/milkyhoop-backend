"""Jalur legacy chat DOCUMENT_INTAKE melewati pagar SAMA dengan jurnal manual (5 Okt 2026, MASTER)."""
import asyncio
import inspect
import pathlib
import uuid

import pytest
from fastapi import HTTPException

from app.services import pagar_akun_modul as PM
from app.services import kernel_document_executor as KDE
from app.routers import journals as J

T = "tenant-uji"
DP, AR, INV, BIASA = (uuid.uuid4() for _ in range(4))


class Conn:
    async def fetch(self, sql, *a):
        if "account_roles" in sql:
            return [{"account_code": "2-10500", "name": "Uang Muka Pelanggan"}] if DP in a[2] else []
        if "account_type IN ('RECEIVABLE', 'PAYABLE')" in sql:
            return [{"id": AR, "account_code": "1-10300", "name": "Piutang Usaha", "account_type": "RECEIVABLE"}] if AR in a[0] else []
        if "inventory_account_id" in sql:
            return [{"coa_id": INV}]
        if "SELECT account_code, name FROM chart_of_accounts" in sql:
            return [{"account_code": "1-10600", "name": "Persediaan"}]
        raise AssertionError(sql[:80])


def _j(c):
    return asyncio.run(c)


@pytest.mark.parametrize("akun,kata", [(DP, "Uang Muka Pelanggan"), (AR, "RECEIVABLE")])
def test_intake_tanpa_persediaan_tetap_menolak_ar_ap_dan_uang_muka(akun, kata):
    with pytest.raises(HTTPException) as e:
        _j(PM.pagar_akun_modul(Conn(), T, [BIASA, akun], persediaan=False))
    assert kata in e.value.detail


def test_persediaan_hanya_untuk_jurnal_manual():
    _j(PM.pagar_akun_modul(Conn(), T, [BIASA, INV], persediaan=False))  # intake: lolos (menulis inventory_movements sendiri)
    with pytest.raises(HTTPException) as e:
        _j(PM.pagar_akun_modul(Conn(), T, [BIASA, INV]))
    assert "Persediaan/HPP" in e.value.detail


def test_legacy_memanggil_pagar_sebelum_jurnal_dan_gagal_lewat_mark_failed():
    src = inspect.getsource(KDE.KernelDocumentExecutor._execute_legacy)
    i = src.index("await pagar_akun_modul(")
    assert i < src.index("INSERT INTO journal_entries")
    assert "persediaan=False" in src[i:i + 200]
    assert "return await self._mark_failed(conn, doc_uuid, tenant_id, str(e.detail))" in src


def test_jurnal_manual_memakai_fungsi_yang_sama():
    src = inspect.getsource(J.validate_no_derived_layer_accounts)
    assert "await pagar_akun_modul(conn, tenant_id, account_ids)" in src
    assert "RECEIVABLE', 'PAYABLE'" not in src


def test_satu_fungsi_bukan_salinan():
    app = pathlib.Path(J.__file__).resolve().parents[1]
    kue = "account_type IN ('RECEIVABLE', 'PAYABLE')"
    pemilik = [str(p.relative_to(app)) for p in app.rglob("*.py") if kue in p.read_text(errors="ignore")]
    assert pemilik == ["services/pagar_akun_modul.py"], pemilik
