"""LAMPIRAN Kas & Bank + Kasbon + Penerimaan/Pembayaran keluar (9 Nov 2026; kontrak backend/docs/KONTRAK-lampiran-kasbank-kasbon-bayar-
20261109.md): pabrik lampiran_induk yang SAMA. V493 (disetujui LANGSUNG pemilik) menambah tiga entity_type ke chk_da_entity.
Diuji: spesifikasi + SQL bersaring tenant, pagar pay-group kasbon (unggah/daftar/unduh/lepas), pemisahan entity 'payment' dua tabel,
subset rute penerimaan/pembayaran keluar (GET lama tetap), hub, izin, berkas V493."""
import asyncio
import re
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.routers import documents as DOC
from app.routers import lampiran_induk as LI

APP = Path(LI.__file__).resolve().parents[1]
MIG = APP.parents[1] / "migrations"
T = "kaos-biru-konveksi"
DOC_ID = uuid.uuid4()


def test_spesifikasi_lima_modul():
    harap = {
        "bank_transaction": ("bank-transactions", "bank_transactions", ("unggah", "daftar", "unduh", "lepas")),
        "bank_transfer": ("bank-transfers", "bank_transfers", ("unggah", "daftar", "unduh", "lepas")),
        "employee_advance": ("employee-advances", "employee_advances", ("unggah", "daftar", "unduh", "lepas")),
    }
    for s in (LI.SPEC_MUTASI_BANK, LI.SPEC_TRANSFER_BANK, LI.SPEC_KASBON):
        assert (s.modul_url, s.tabel, s.rute) == harap[s.entitas]
    assert LI.SPEC_KASBON.pra_cek_dok is LI._kasbon_dalam_pay_group
    for s in (LI.SPEC_PENERIMAAN, LI.SPEC_BAYAR_KELUAR):
        assert s.entitas == "payment" and s.rute == ("unggah", "lepas")


def test_sql_bersaring_tenant_dan_nomor_mutasi():
    for s in (LI.SPEC_MUTASI_BANK, LI.SPEC_TRANSFER_BANK, LI.SPEC_KASBON, LI.SPEC_PENERIMAAN, LI.SPEC_BAYAR_KELUAR):
        q = LI.sql_spec(s)
        assert "tenant_id = $2" in q["induk"] and "ind.tenant_id = $2" in q["daftar"] and "ind.tenant_id = $3" in q["lepas"]
    induk = LI.sql_spec(LI.SPEC_MUTASI_BANK)["induk"]
    assert "COALESCE(transaction_number, (SELECT je.journal_number FROM journal_entries je" in induk
    assert "je.tenant_id = bank_transactions.tenant_id" in induk  # nomor jurnal mutasi manual, tenant eksplisit


def test_payment_dua_tabel_terpisah_lewat_join_induk():
    rp, bp = LI.sql_spec(LI.SPEC_PENERIMAAN), LI.sql_spec(LI.SPEC_BAYAR_KELUAR)
    assert "JOIN receive_payments ind" in rp["daftar"] and "JOIN bill_payments_v2 ind" in bp["daftar"]
    assert "USING receive_payments ind" in rp["lepas"] and "USING bill_payments_v2 ind" in bp["lepas"]
    assert "FROM receive_payments WHERE" in rp["induk"] and "FROM bill_payments_v2 WHERE" in bp["induk"]


def test_subset_rute_tanpa_bayangan_get_lama():
    def jalur(r):
        return sorted((m, x.path) for x in r.routes for m in x.methods)
    assert jalur(LI.router_penerimaan) == [("DELETE", "/{doc_id}/attachments/{attachment_id}"), ("POST", "/{doc_id}/attachments")]
    assert jalur(LI.router_bayar_keluar) == jalur(LI.router_penerimaan)
    assert len(LI.router_kasbon.routes) == 4 and len(LI.router_mutasi_bank.routes) == 4
    assert len(LI.router_nota_kredit.routes) == 4  # modul lama tak berubah


# ---------------------------------------------------------------- pagar pay-group kasbon
class Kon:
    def __init__(self, induk=True, karyawan=uuid.uuid4()):
        self.induk, self.karyawan, self.log = induk, karyawan, []

    def transaction(self):
        kon = self

        class Tx:
            async def __aenter__(self):
                return kon

            async def __aexit__(self, *e):
                return False
        return Tx()

    async def execute(self, sql, *a):
        self.log.append(("x", sql, a))

    async def fetchrow(self, sql, *a):
        self.log.append(("r", sql, a))
        return {"id": DOC_ID, "nomor": "KB-2611-0001"} if self.induk and "FROM employee_advances" in sql else None

    async def fetchval(self, sql, *a):
        self.log.append(("v", sql, a))
        if "SELECT employee_id FROM employee_advances" in sql:
            assert a == (DOC_ID, T)
            return self.karyawan
        return 0

    async def fetch(self, sql, *a):
        return []


class Pool:
    def __init__(self, k):
        self.k = k

    def acquire(self):
        k = self.k

        class Cm:
            async def __aenter__(self):
                return k

            async def __aexit__(self, *e):
                return False
        return Cm()


def _rute(router, nama):
    return next(r.endpoint for r in router.routes if r.endpoint.__name__ == nama)


def _req():
    return SimpleNamespace(state=SimpleNamespace(user={"tenant_id": T, "user_id": str(uuid.uuid4())}), headers={})


@pytest.mark.parametrize("boleh,harap", [(False, 404), (True, 200)])
def test_kasbon_pay_group_daftar(monkeypatch, boleh, harap):
    k = Kon()

    async def pool():
        return Pool(k)

    async def lingkup(conn, tid, uid, emp):
        assert (tid, emp) == (T, k.karyawan) and uid
        return boleh
    monkeypatch.setattr(LI, "get_pool", pool)
    monkeypatch.setattr("app.services.pay_group_access.employee_in_scope", lingkup)
    daftar = _rute(LI.router_kasbon, "daftar_lampiran_employee_advance")
    if harap == 404:
        with pytest.raises(HTTPException) as e:
            asyncio.run(daftar(_req(), DOC_ID))
        assert (e.value.status_code, e.value.detail) == (404, "Kasbon tidak ditemukan.")
    else:
        assert asyncio.run(daftar(_req(), DOC_ID))["success"] is True


def test_kasbon_pay_group_juga_unduh(monkeypatch):
    k = Kon()

    async def pool():
        return Pool(k)

    async def lingkup(*a):
        return False
    monkeypatch.setattr(LI, "get_pool", pool)
    monkeypatch.setattr("app.services.pay_group_access.employee_in_scope", lingkup)
    unduh = _rute(LI.router_kasbon, "unduh_lampiran_employee_advance")
    with pytest.raises(HTTPException) as e:
        asyncio.run(unduh(_req(), DOC_ID, uuid.uuid4()))
    assert e.value.status_code == 404 and not any("FROM document_attachments" in x[1] for x in k.log)


# ---------------------------------------------------------------- hub, izin, migrasi
def test_entitas_baru_pabrik_saja_bukan_hub():
    """Preseden V466 (goods_receipt, vendor_deposit): entitas lampiran pabrik TIDAK masuk hub -- hub wajib berpasangan dengan trigger
    lepas-tautan V308 (test_lampiran_yatim); pabrik menyaring lewat JOIN induk sehingga tautan yatim tak pernah tampil."""
    for e in ("bank_transaction", "bank_transfer", "employee_advance", "goods_receipt", "vendor_deposit"):
        assert e not in DOC._ENTITAS_HUB and e not in DOC._ENTITAS_BACA


@pytest.mark.parametrize("jalur,metode,harap", [
    (f"/api/bank-transactions/{DOC_ID}/attachments", "POST", ("kas_bank", "U")),
    (f"/api/bank-transactions/{DOC_ID}/attachments", "GET", ("kas_bank", "R")),
    (f"/api/bank-transactions/{DOC_ID}/attachments/{DOC_ID}", "DELETE", ("kas_bank", "D")),
    (f"/api/bank-transfers/{DOC_ID}/attachments", "POST", ("kas_bank", "U")),
    (f"/api/bank-transfers/{DOC_ID}/attachments/{DOC_ID}/download", "GET", ("kas_bank", "R")),
    (f"/api/employee-advances/{DOC_ID}/attachments", "POST", ("payroll", "U")),
    (f"/api/employee-advances/{DOC_ID}/attachments/{DOC_ID}", "DELETE", ("payroll", "D")),
    (f"/api/receive-payments/{DOC_ID}/attachments", "POST", ("receive_payment", "U")),
    (f"/api/bill-payments/{DOC_ID}/attachments/{DOC_ID}", "DELETE", ("send_payment", "D")),
])
def test_izin_lampiran(jalur, metode, harap):
    from app.middleware.permission_middleware import PermissionMiddleware
    assert PermissionMiddleware(app=None)._find_permission(jalur, metode) == harap


def test_v493_berkutip_dan_rollback_sama_v466():
    nilai = lambda teks: re.findall(r"'([a-z_]+)'", teks.split("CHECK (entity_type IN (")[1].split("));")[0])  # noqa: E731
    v466 = nilai((MIG / "V466__chk_da_entity_grn_ump.sql").read_text())
    v493 = nilai((MIG / "V493__chk_da_entity_kasbank_kasbon.sql").read_text())
    rb = (MIG / "V493__chk_da_entity_kasbank_kasbon_ROLLBACK.sql").read_text()
    assert len(v466) == 27 and v493 == v466 + ["bank_transaction", "bank_transfer", "employee_advance"]
    assert nilai(rb) == v466 and "RAISE EXCEPTION" in rb


def test_main_memasang_lima_router():
    src = (APP / "main.py").read_text()
    for r, pfx in (("router_mutasi_bank", "bank-transactions"), ("router_transfer_bank", "bank-transfers"), ("router_kasbon", "employee-advances"),
                   ("router_penerimaan", "receive-payments"), ("router_bayar_keluar", "bill-payments")):
        assert f'include_router(lampiran_induk.{r}, prefix="/api/{pfx}"' in src
