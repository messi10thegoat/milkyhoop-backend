"""BUG-028 kode pelanggan berikut = MAX seri (bukan kode terbaru) · BUG-020 saldo awal bukan arus kas / bukan transaksi hari ini (5 Nov 2026)."""
import asyncio
import inspect
from datetime import datetime
from types import SimpleNamespace

import pytest

from app.routers import customers as RC
from app.routers import dashboard as RD
from app.services import dashboard_angka as DA


def _k(kode, menit):
    import re
    m = re.match(r"^([A-Z]*)([0-9]+)$", kode)
    return {"awalan": m.group(1), "angka": m.group(2), "created_at": datetime(2026, 11, 5, 10, menit)}


# ---------------------------------------------------------------- BUG-028
@pytest.mark.parametrize("baris, harap", [
    ([], "C0001"),
    ([_k("C0001", 1)], "C0002"),
    ([_k("C0001", 1), _k("C0006", 2), _k("C0003", 3)], "C0007"),      # terbaru C0003, tapi C0006 dipakai -> MAX
    ([_k("C0009", 1), _k("PLG0002", 2), _k("PLG0005", 3)], "PLG0006"),  # awalan ikut kode berpola terbaru
    ([_k("C0004", 1), _k("12", 2)], "C0013"),                          # angka saja = seri bawaan C
    ([_k("C9999", 1)], "C10000"),
])
def test_kode_berikut_max_seri(baris, harap):
    assert RC.kode_berikut(baris) == harap


def test_sql_kode_berpola_tenant_tanpa_terbaru_saja_termasuk_terhapus():
    s = RC.SQL_KODE_BERPOLA
    assert "tenant_id = $1" in s and "regexp_match(nomor_member, '^([A-Z]*)([0-9]+)$')" in s and "WHERE m IS NOT NULL" in s
    assert "LIMIT 1" not in s and "deleted_at" not in s   # kode pelanggan terhapus tak dipakai ulang
    src = inspect.getsource(RC.get_next_customer_code)
    assert "kode_berikut(baris)" in src and "ORDER BY created_at DESC" not in src


def test_rute_next_code_kode_terbaru_tak_berpola_tak_jatuh_ke_c0001():
    """Dulu: kode terbaru "TOKO-A" -> "C0001" walau C0001 dipakai (409). Kini SQL hanya memberi kode berpola -> C0002."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    rows = [_k("C0001", 1)]   # "TOKO-A" (menit 2) tersaring oleh regexp_match di SQL

    class _C:
        async def fetch(self, sql, *a):
            assert sql == RC.SQL_KODE_BERPOLA and a == ("t1",)
            return rows

    class _P:
        def acquire(self):
            class _A:
                async def __aenter__(s):
                    return _C()

                async def __aexit__(s, *e):
                    return False
            return _A()

    async def pool():
        return _P()
    app = FastAPI()

    @app.middleware("http")
    async def masuk(request, call_next):
        request.state.user = {"tenant_id": "t1", "user_id": "00000000-0000-0000-0000-000000000001"}
        return await call_next(request)
    app.include_router(RC.router, prefix="/api/customers")
    mp = pytest.MonkeyPatch()
    mp.setattr(RC, "get_pool", pool)
    try:
        r = TestClient(app).get("/api/customers/next-code")
    finally:
        mp.undo()
    assert r.status_code == 200 and r.json() == {"success": True, "next_code": "C0002"}


# ---------------------------------------------------------------- BUG-020
SUMBER = ("OPENING", "OPENING_BALANCE", "BANK_OPENING_BALANCE", "VENDOR_OPENING_BALANCE")


def test_arus_kas_harian_mengecualikan_semua_jurnal_saldo_awal():
    assert DA.SUMBER_SALDO_AWAL == SUMBER
    s = DA.SQL_ARUS_KAS_HARIAN
    assert "je.source_type NOT IN ('OPENING', 'OPENING_BALANCE', 'BANK_OPENING_BALANCE', 'VENDOR_OPENING_BALANCE')" in s
    # laporan-fix-2: definisi aliran SATU (aliran_kas.KONDISI_ALIRAN, sadar-periode) menggantikan is_effective_journal retroaktif; pembalik saldo awal
    # (reversal_of_id) tetap tersingkir di sini kecuali atas asli periode lalu yang POSTED
    from app.services.report_engine.aliran_kas import KONDISI_ALIRAN
    assert KONDISI_ALIRAN in s and "je.reversal_of_id IS NOT NULL" in s


def test_saldo_kas_tetap_memuat_saldo_awal():
    """Uang tersedia (kas_per_akun) = saldo GL efektif, TIDAK memakai pengecualian arus."""
    assert "SUMBER_SALDO_AWAL" not in inspect.getsource(DA.kas_per_akun)
    assert "SUMBER_SALDO_AWAL" not in inspect.getsource(DA.saldo_kontrol)


def test_transaksi_hari_ini_tanpa_saldo_awal(monkeypatch):
    """P2 MASTER: today_trx_query (trx_masuk/keluar) juga mengecualikan jurnal saldo awal."""
    tanya = {}

    class _C:
        async def execute(self, *a):
            pass

        async def fetch(self, sql, *a):
            return []

        async def fetchrow(self, sql, *a):
            tanya["sql"], tanya["a"] = sql, a
            return {"trx_masuk": 0, "trx_keluar": 0}

        async def fetchval(self, sql, *a):
            return None

    class _P:
        def acquire(self):
            class _A:
                async def __aenter__(s):
                    return _C()

                async def __aexit__(s, *e):
                    return False
            return _A()

    async def pool():
        return _P()

    async def harian(*a):
        return {}

    async def hari(*a):
        return datetime(2026, 11, 5).date()
    monkeypatch.setattr(RD, "get_db_pool", pool)
    monkeypatch.setattr(RD.DA, "arus_kas_harian", harian)
    monkeypatch.setattr(RD, "tanggal_dokumen", hari)
    req = SimpleNamespace(state=SimpleNamespace(user={"tenant_id": "t1"}))
    asyncio.run(RD.get_cash_flow_trends(req, period="30d", start_date=None, end_date=None))
    assert "je.source_type <> ALL($3::text[])" in tanya["sql"] and tanya["a"][2] == list(SUMBER)
