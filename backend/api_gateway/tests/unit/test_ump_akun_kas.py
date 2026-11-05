"""BUG-043 (putusan pemilik LANGSUNG 6 Nov 2026): uang muka pembelian tanpa rekening/akun kas yang dipilih. Dulu SELALU peran Kas
(CASH_GENERAL) walau tenant punya beberapa akun kas -> jurnal ke kas yang salah. Kini >1 akun kas aktif -> AKUN_KAS_WAJIB (posting);
tepat 1 -> akun itu; 0 -> peran Kas. SATU penentu (akun_kas) untuk posting, pratinjau, dan Simpan & posting."""
import asyncio
import inspect
import uuid

import pytest

from app.routers import vendor_deposits as RV
from app.services import uang_muka_vendor_rencana as R

COA_A, COA_B, BA_A, BA_B, COA_PERAN, COA_REK, BA_REK = (uuid.uuid4() for _ in range(7))


class _C:
    def __init__(self, kas):
        self.kas, self.sql = kas, []

    async def fetch(self, sql, *a):
        self.sql.append(sql)
        assert sql == R.SQL_AKUN_KAS_AKTIF and a == ("t1",)
        return self.kas[:2]

    async def fetchrow(self, sql, *a):
        if "FROM bank_accounts WHERE id = $1 AND tenant_id = $2" in sql:
            return {"id": BA_REK, "coa_id": COA_REK} if a[0] == BA_REK else None
        raise AssertionError(sql[:80])

    async def fetchval(self, sql, *a):
        if "FROM bank_accounts WHERE coa_id = $1" in sql:
            return None
        raise AssertionError(sql[:80])


@pytest.fixture(autouse=True)
def _peran(monkeypatch):
    async def akun(conn, tid, peran, bloks):
        return COA_PERAN
    monkeypatch.setattr(R, "_akun", akun)


DUA = [{"id": BA_A, "coa_id": COA_A}, {"id": BA_B, "coa_id": COA_B}]


def _kas(kas, rek=None, posting=True):
    bloks = []
    hasil = asyncio.run(R.akun_kas(_C(kas), "t1", rek, None, bloks, wajib=False, posting=posting))
    return hasil, bloks


def test_dua_akun_kas_tanpa_pilihan_posting_ditolak_bermedan():
    (coa, ba), bloks = _kas(DUA)
    assert (coa, ba) == (None, None)
    assert bloks == [{"code": "AKUN_KAS_WAJIB", "message": "Pilih akun kas yang dipakai untuk membayar.", "status": 422,
                      "field": "bank_account_id"}]


def test_dua_akun_kas_draf_boleh_tanpa_akun():
    (coa, ba), bloks = _kas(DUA, posting=False)
    assert (coa, ba, bloks) == (None, None, [])


def test_tepat_satu_akun_kas_dipakai_bukan_peran():
    (coa, ba), bloks = _kas(DUA[:1])
    assert (coa, ba, bloks) == (COA_A, BA_A, [])


def test_nol_akun_kas_tetap_peran_kas():
    (coa, ba), bloks = _kas([])
    assert (coa, bloks) == (COA_PERAN, [])


def test_rekening_dipilih_tak_menghitung_akun_kas():
    c = _C(DUA)
    bloks = []
    assert asyncio.run(R.akun_kas(c, "t1", BA_REK, None, bloks, wajib=False)) == (COA_REK, BA_REK) and c.sql == [] and bloks == []


def test_sql_akun_kas_berpagar_tenant_aktif():
    s = R.SQL_AKUN_KAS_AKTIF
    assert "tenant_id = $1" in s and "account_type IN ('cash', 'petty_cash')" in s and "COALESCE(is_active, true)" in s and "LIMIT 2" in s


def test_posting_memakai_penentu_yang_sama_tanpa_fallback_peran_sendiri():
    src = inspect.getsource(RV._posting_inti)
    assert 'cash_account = rencana["akun_kas"]' in src and "CASH_GENERAL" not in src
    assert '"akun_kas": coa_kas' in inspect.getsource(R.rencana_posting)
    buat = inspect.getsource(R)
    assert 'posting=bool(getattr(body, "simpan_dan_posting", False))' in buat
