"""BUG-141 (8 Nov 2026, sebelum Selasa): GET /bank-accounts/{id}/transactions?tanpa_batal=true -- dulu parameter DIABAIKAN (FE ui_v3 menyembunyikan
baris asli VOIDED, pembalik "Void transfer" tetap -> Saldo tak terbaca). Kini SATU definisi batal dengan ROW (q) /transactions/recent
(KR.SARING_TANPA_BATAL atas jurnal baris): KEDUA sisi (asli reversed_by_id + pembalik reversal_of_id/REVERSAL) disembunyikan untuk semua sumber
(transfer, manual, biaya, uang muka, bayar tagihan), di COUNT dan daftar (saldo berjalan dihitung sesudah saring: pasangan net nol)."""
import asyncio
from uuid import uuid4

from starlette.requests import Request

from app.routers import bank_accounts as RB
from app.services import kasbank_ringkas as KR


def test_predikat_baris_satu_definisi_dengan_saringan_q():
    p = KR.sql_bt_tanpa_batal("bt", "$7")
    assert p == ("NOT EXISTS (SELECT 1 FROM journal_entries je WHERE je.id = bt.journal_id AND je.tenant_id = $7 AND NOT (TRUE"
                 + KR.SARING_TANPA_BATAL + "))")


def _panggil(monkeypatch, **kw):
    tangkap = []

    class K:
        async def fetchrow(self, sql, *a):
            return {"id": a[0], "coa_id": uuid4()}

        async def fetchval(self, sql, *a):
            tangkap.append(("count", sql, a))
            return 0

        async def fetch(self, sql, *a):
            tangkap.append(("daftar", sql, a))
            return []

    class P:
        def acquire(self):
            class Cm:
                async def __aenter__(s):
                    return K()

                async def __aexit__(s, *a):
                    return False
            return Cm()

    async def pool():
        return P()
    monkeypatch.setattr(RB, "get_pool", pool)
    r = Request({"type": "http", "method": "GET", "path": "/", "headers": []})
    r.state.user = {"tenant_id": "t1", "user_id": "00000000-0000-0000-0000-000000000001"}
    arg = dict(transaction_type=None, date_from=None, date_to=None, is_reconciled=None, skip=0, limit=50)
    arg.update(kw)
    hasil = asyncio.run(RB.get_bank_transactions(r, uuid4(), **arg))
    return hasil, {j: (s, a) for j, s, a in tangkap}


def test_tanpa_batal_saring_count_dan_daftar_dengan_tenant(monkeypatch):
    hasil, q = _panggil(monkeypatch, tanpa_batal=True)
    assert hasil == {"items": [], "total": 0, "has_more": False}
    for j in ("count", "daftar"):
        sql, a = q[j]
        assert KR.sql_bt_tanpa_batal("bt", "$2") in sql, j
        assert a[1] == "t1", j


def test_tanpa_batal_bersama_saringan_lain_nomor_parameter_berurut(monkeypatch):
    from datetime import date
    _, q = _panggil(monkeypatch, tanpa_batal=True, date_from=date(2026, 11, 1))
    sql, a = q["count"]
    assert "bt.transaction_date >= $2" in sql and KR.sql_bt_tanpa_batal("bt", "$3") in sql and a[1:] == (date(2026, 11, 1), "t1")


def test_bawaan_perilaku_lama_tanpa_saringan(monkeypatch):
    _, q = _panggil(monkeypatch)
    for j in ("count", "daftar"):
        assert "NOT EXISTS (SELECT 1 FROM journal_entries je" not in q[j][0], j
    _, q2 = _panggil(monkeypatch, tanpa_batal=False)
    assert q2["count"] == q["count"]
