"""Buku Besar per akun (GET /api/ledger/{account_id}) — total & saldo dari SELURUH filter, bukan halaman.

Dulu: total_debit/total_credit/closing_balance = jumlah HALAMAN INI (LIMIT 50); running_balance halaman >1
mulai lagi dari opening; `offset` yang dikirim FE (useLedgerDetail) diabaikan -> "muat lagi" mengulang halaman 1.
Terukur 26 Sep (gerbang DB baca-saja, semua akun kaos+grapgrap): kode lama salah di 12 akun (>50 baris),
mis. grapgrap 83.617.294 vs benar 127.413.294; kode baru 0 beda di 174 akun + paginasi penuh utuh.
"""
import uuid
from datetime import date
from decimal import Decimal as D

import pytest

from app.routers import ledger as L

TENANT = "grapgrap-manado"
AKUN = uuid.uuid4()


class _Conn:
    """Akun DEBIT; agregat SELURUH filter: debit 1000, kredit 300, 120 baris; halaman = 2 baris."""

    def __init__(self, normal="DEBIT"):
        self.normal = normal
        self.q = []

    async def execute(self, q, *a):
        return "OK"

    async def fetchrow(self, q, *a):
        self.q.append(("row", q, a))
        if "FROM chart_of_accounts" in q:
            return {"id": AKUN, "account_code": "1-10201", "name": "BCA", "account_type": "asset",
                    "normal_balance": self.normal}
        if "COUNT(*) AS total_count" in q:
            return {"total_debit": D("1000"), "total_credit": D("300"), "total_count": 120}
        if "je.journal_date < $3" in q:
            return {"total_debit": D("50"), "total_credit": D("0")}
        raise AssertionError(q)

    async def fetch(self, q, *a):
        self.q.append(("fetch", q, a))
        # dua baris halaman; kumulatif_dc = jumlah (debit-kredit) SEJAK AWAL filter (bukan awal halaman)
        return [
            {"date": date(2026, 9, 3), "journal_number": "J1", "journal_id": uuid.uuid4(), "description": "a",
             "debit": D("10"), "credit": D("0"), "source_type": "MANUAL", "kumulatif_dc": D("510")},
            {"date": date(2026, 9, 4), "journal_number": "J2", "journal_id": uuid.uuid4(), "description": "b",
             "debit": D("0"), "credit": D("5"), "source_type": None, "kumulatif_dc": D("505")},
        ]


class _Pool:
    def __init__(self, c):
        self.c = c

    def acquire(self):
        pool = self

        class _Ctx:
            async def __aenter__(self):
                return pool.c

            async def __aexit__(self, *e):
                return False
        return _Ctx()


def _pasang(monkeypatch, c):
    async def gp():
        return _Pool(c)
    monkeypatch.setattr(L, "get_pool", gp)
    monkeypatch.setattr(L, "get_user_context", lambda r: {"tenant_id": TENANT})


@pytest.mark.asyncio
async def test_total_dan_closing_dari_seluruh_filter_bukan_halaman(monkeypatch):
    c = _Conn()
    _pasang(monkeypatch, c)
    d = (await L.get_account_ledger(None, AKUN, start_date=None, end_date=date(2026, 9, 26), page=1, limit=2,
                                    offset=None)).data
    assert d["total_debit"] == D("1000") and d["total_credit"] == D("300")
    assert d["closing_balance"] == D("700")          # 0 + 1000 - 300, BUKAN 0 + 10 - 5
    assert d["total_count"] == 120 and d["has_more"] is True and d["offset"] == 0 and d["limit"] == 2


@pytest.mark.asyncio
async def test_saldo_berjalan_dari_kumulatif_seluruh_filter_dan_offset_fe(monkeypatch):
    c = _Conn()
    _pasang(monkeypatch, c)
    d = (await L.get_account_ledger(None, AKUN, start_date=date(2026, 9, 1), end_date=None, page=1, limit=2,
                                    offset=100)).data
    # opening 50 (sebelum start) + kumulatif sejak awal filter
    assert [e.running_balance for e in d["entries"]] == [D("560"), D("555")]
    assert d["closing_balance"] == D("750") and d["offset"] == 100 and d["has_more"] is True
    (_, q, a), = [x for x in c.q if x[0] == "fetch"]
    assert a[-2:] == (2, 100)                        # LIMIT 2 OFFSET 100 (offset FE dipakai)
    assert "OVER (" in q and "je.journal_date, je.created_at, je.id, jl.id" in q   # urutan deterministik


@pytest.mark.asyncio
async def test_akun_normal_kredit(monkeypatch):
    c = _Conn(normal="CREDIT")
    _pasang(monkeypatch, c)
    d = (await L.get_account_ledger(None, AKUN, start_date=None, end_date=None, page=2, limit=2, offset=None)).data
    assert d["closing_balance"] == D("-700")          # kredit-normal: 0 + 300 - 1000
    assert [e.running_balance for e in d["entries"]] == [D("-510"), D("-505")]
    assert d["offset"] == 2                           # page 2 tanpa offset -> (2-1)*2


@pytest.mark.asyncio
async def test_transactions_meneruskan_offset_bukan_objek_query(monkeypatch):
    c = _Conn()
    _pasang(monkeypatch, c)
    d = (await L.get_account_transactions(None, AKUN, start_date=None, end_date=None, page=1, limit=2,
                                          offset=4)).data
    assert d["offset"] == 4
    # dipanggil langsung tanpa offset: default Query(...) tak boleh terbaca sebagai offset
    d2 = (await L.get_account_ledger(None, AKUN, start_date=None, end_date=None, page=3, limit=2)).data
    assert d2["offset"] == 4
