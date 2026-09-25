"""Penerimaan Pembayaran /summary: HARI INI / MINGGU INI / BULAN INI dari server (25 Sep 2026).

Dulu kartu FE menjumlah baris TERMUAT dengan tanggal KLIEN. Kini: tanggal bisnis tenant
(tanggal_tenant, bukan UTC), minggu Senin–Minggu, bulan kalender; uang dari jurnal
(sumber sama dengan total_received), hanya 'posted'. Semantik SQL terhadap data nyata
(void, tenant lain, minggu lalu) dibuktikan probe prod baca-saja dua sisi.
"""
import re
from datetime import date, datetime, timezone

import pytest

from app.services import rp_periode as RP
from app.utils import tanggal_tenant as tt

TENANT = "kaos-biru-konveksi"


# ---------- batas periode (murni) ----------

@pytest.mark.parametrize("hari,minggu,bulan", [
    (date(2026, 9, 25), (date(2026, 9, 21), date(2026, 9, 27)), (date(2026, 9, 1), date(2026, 9, 30))),  # Jumat
    (date(2026, 9, 21), (date(2026, 9, 21), date(2026, 9, 27)), (date(2026, 9, 1), date(2026, 9, 30))),  # Senin
    (date(2026, 9, 27), (date(2026, 9, 21), date(2026, 9, 27)), (date(2026, 9, 1), date(2026, 9, 30))),  # Minggu
    (date(2026, 10, 1), (date(2026, 9, 28), date(2026, 10, 4)), (date(2026, 10, 1), date(2026, 10, 31))),  # lintas bulan
    (date(2026, 12, 31), (date(2026, 12, 28), date(2027, 1, 3)), (date(2026, 12, 1), date(2026, 12, 31))),
    (date(2028, 2, 10), (date(2028, 2, 7), date(2028, 2, 13)), (date(2028, 2, 1), date(2028, 2, 29))),  # kabisat
])
def test_batas_periode(hari, minggu, bulan):
    b = RP.batas_periode(hari)
    assert b["today"] == hari
    assert (b["week_start"], b["week_end"]) == minggu
    assert b["week_start"].weekday() == 0 and b["week_end"].weekday() == 6   # Senin–Minggu
    assert (b["month_start"], b["month_end"]) == bulan


def test_argumen_urut_sesuai_placeholder():
    b = RP.batas_periode(date(2026, 9, 25))
    a = RP.argumen_kueri(TENANT, b)
    assert a == (TENANT, date(2026, 9, 25), date(2026, 9, 21), date(2026, 9, 27),
                 date(2026, 9, 1), date(2026, 9, 30))
    assert {int(x) for x in re.findall(r"\$(\d+)", RP.SQL_RINGKASAN)} == set(range(1, len(a) + 1))


def test_sql_periode_memakai_parameter_bukan_jam_db():
    q = RP.SQL_RINGKASAN
    assert not re.search(r"CURRENT_DATE|now\(\)|date_trunc", q, re.I)
    assert "rp.payment_date = $2::date" in q
    assert "BETWEEN $3::date AND $4::date" in q and "BETWEEN $5::date AND $6::date" in q
    # uang periode hanya dari journal_amounts (posted + jurnal tak dibalik), bukan rp.total_amount
    assert "rp.total_amount" not in q
    # pagar di DALAM CTE uang (FILTER posted_count di luar CTE tak boleh menutupi hilangnya pagar)
    cte = q.split("WITH journal_amounts AS (", 1)[1].split("GROUP BY rp.id", 1)[0]
    for pagar in ("AND rp.status = 'posted'", "AND je.reversed_by_id IS NULL", "AND je.status = 'POSTED'",
                  "rp.tenant_id = $1", "coa.account_type = 'RECEIVABLE'", "jl.credit > 0"):
        assert pagar in cte, pagar
    luar = q.split("GROUP BY rp.id", 1)[1]
    assert "LEFT JOIN journal_amounts ja ON ja.payment_id = rp.id" in luar
    assert luar.rstrip().endswith("WHERE rp.tenant_id = $1")


# ---------- rute: tanggal bisnis disuntik ----------

@pytest.fixture
def jam(monkeypatch):
    def pasang(instant):
        class _DT(datetime):
            @classmethod
            def now(cls, tz=None):
                return instant if tz else instant.replace(tzinfo=None)

        monkeypatch.setattr(tt, "datetime", _DT)
        tt._cache.clear()

    yield pasang
    tt._cache.clear()


_BARIS = {"total": 5, "draft_count": 0, "posted_count": 4, "voided_count": 1,
          "total_received": 900000, "total_allocated": 800000, "total_unapplied": 100000,
          "amount_today": 150000, "count_today": 1, "amount_this_week": 400000, "count_this_week": 2,
          "amount_this_month": 700000, "count_this_month": 3}


class _Conn:
    def __init__(self):
        self.panggilan = []

    async def fetchval(self, q, *a):
        if 'FROM "Tenant"' in q:
            return "Asia/Jakarta"
        self.panggilan.append((q, a))
        return 0

    async def fetchrow(self, q, *a):
        pakai = {int(x) for x in re.findall(r"\$(\d+)", q)}
        assert pakai == set(range(1, len(a) + 1)), (sorted(pakai), len(a))
        self.panggilan.append((q, a))
        return _BARIS

    async def fetch(self, q, *a):
        pakai = {int(x) for x in re.findall(r"\$(\d+)", q)}
        assert pakai == set(range(1, len(a) + 1)), (sorted(pakai), len(a))
        self.panggilan.append((q, a))
        return _METODE

    async def execute(self, q, *a):
        return "OK"


_METODE = [
    {"metode": "bank_transfer", "count": 3, "amount": 800000, "count_today": 1, "amount_today": 150000,
     "count_this_week": 2, "amount_this_week": 400000, "count_this_month": 2, "amount_this_month": 600000},
    {"metode": "cash", "count": 1, "amount": 100000, "count_today": 0, "amount_today": 0,
     "count_this_week": 0, "amount_this_week": 0, "count_this_month": 1, "amount_this_month": 100000},
]


class _Pool:
    def __init__(self, conn):
        self.conn = conn

    def acquire(self):
        pool = self

        class _Ctx:
            async def __aenter__(self):
                return pool.conn

            async def __aexit__(self, *e):
                return False

        return _Ctx()


@pytest.mark.parametrize("instant,harap", [
    (datetime(2026, 9, 27, 18, 0, tzinfo=timezone.utc), date(2026, 9, 28)),   # Senin 01:00 WIB, UTC masih Minggu
    (datetime(2026, 9, 30, 20, 0, tzinfo=timezone.utc), date(2026, 10, 1)),   # 1 Okt 03:00 WIB, UTC masih Sep
    (datetime(2026, 9, 25, 10, 0, tzinfo=timezone.utc), date(2026, 9, 25)),   # kontrol siang
])
@pytest.mark.asyncio
async def test_summary_periode_tanggal_bisnis(jam, monkeypatch, instant, harap):
    from app.routers import receive_payments as R

    jam(instant)
    c = _Conn()

    async def _pool():
        return _Pool(c)

    monkeypatch.setattr(R, "get_pool", _pool)
    monkeypatch.setattr(R, "get_user_context", lambda r: {"tenant_id": TENANT})
    res = await R.get_receive_payments_summary(request=None)
    (q, a) = c.panggilan[0]
    assert q is RP.SQL_RINGKASAN
    assert a == RP.argumen_kueri(TENANT, RP.batas_periode(harap))
    d = res["data"]
    assert d["period"]["today"] == harap.isoformat()
    assert d["period"]["week_start"] == RP.batas_periode(harap)["week_start"].isoformat()
    assert (d["amount_today"], d["count_today"]) == (150000.0, 1)
    assert (d["amount_this_week"], d["count_this_week"]) == (400000.0, 2)
    assert (d["amount_this_month"], d["count_this_month"]) == (700000.0, 3)
    # medan lama tetap
    assert d["total_received"] == 900000.0 and d["voided_count"] == 1
    (q2, a2) = c.panggilan[1]
    assert q2 is RP.SQL_PER_METODE and a2 == a
    bm = d["by_method"]
    assert set(bm) == {"cash", "bank_transfer", "e_wallet"}
    assert bm["e_wallet"] == RP._KOSONG_METODE
    # invarian: Σ rincian == angka induk (fixture konsisten dengan _BARIS)
    for kunci, induk in (("amount", "total_received"), ("amount_today", "amount_today"),
                         ("amount_this_week", "amount_this_week"), ("amount_this_month", "amount_this_month")):
        assert sum(v[kunci] for v in bm.values()) == d[induk], kunci
    assert bm["cash"]["count_this_month"] == 1 and bm["bank_transfer"]["amount_today"] == 150000.0


def test_rincian_metode_nilai_luar_kontrak_apa_adanya():
    bm = RP.rincian_metode([{**_METODE[1], "metode": None}, {**_METODE[1], "metode": "giro"}], ("cash",))
    assert set(bm) == {"cash", "unknown", "giro"}
    assert bm["cash"]["amount"] == 0.0 and bm["unknown"]["amount"] == 100000.0


def test_sql_per_metode_berbagi_cte_uang():
    q = RP.SQL_PER_METODE
    assert q.startswith(RP._CTE_UANG) and RP.SQL_RINGKASAN.startswith(RP._CTE_UANG)
    luar = q.split("GROUP BY rp.id", 1)[1]
    assert "\n    JOIN journal_amounts ja ON ja.payment_id = rp.id" in luar      # JOIN, bukan LEFT
    assert "WHERE rp.tenant_id = $1" in luar and "GROUP BY rp.payment_method" in luar
    assert {int(x) for x in re.findall(r"\$(\d+)", q)} == set(range(1, 7))
    assert not re.search(r"CURRENT_DATE|now\(\)", q, re.I)


def test_summary_response_model_tak_membuang_medan():
    from app.schemas.receive_payments import ReceivePaymentSummaryResponse as S

    r = S(success=True, data={"amount_today": 1.0, "count_this_week": 2, "period": {"today": "2026-09-25"}})
    assert r.model_dump()["data"]["amount_today"] == 1.0
    assert r.model_dump()["data"]["period"]["today"] == "2026-09-25"
