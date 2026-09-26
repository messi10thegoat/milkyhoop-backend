"""Audit SO FE (26 Sep 2026) — empat tiket BE.

1. Uang muka: idempotency_key SAMA + isi BEDA -> 409 IDEMPOTENCY_KEY_REUSED (dulu success:true + DP LAMA:
   uang yang diketik diam-diam tak tercatat). Isi sama -> tetap balasan idempoten.
2. Detail SO: terkirim per baris dari Surat Jalan aktif (quantity_shipped MATI sejak V264).
3. Ringkasan SO: unshipped_value = Σ (qty - terkirim) x line_total/qty, definisi sama dengan detail.
4. GET /api/tenant/today = tanggal_dokumen zona tenant.
"""
import re
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal as D
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.routers import customer_deposits as CD
from app.routers import sales_orders as SO
from app.routers import tenant_profile as TP
from app.services import so_kirim as K
from app.utils import tanggal_tenant as tt

TENANT = "kaos-biru-konveksi"
AKUN, PELANGGAN, PESANAN = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()


# ------------------------------------------------------------------ 1. uang muka
def _tersimpan(**ubah):
    r = {"id": uuid.uuid4(), "deposit_number": "DEP-2609-0009", "amount": D("2000000.00"), "status": "draft",
         "customer_id": PELANGGAN, "customer_name": "Agung", "deposit_date": date(2026, 9, 26),
         "payment_method": "transfer", "account_id": AKUN, "bank_account_id": None, "quote_id": None,
         "sales_order_id": PESANAN, "proforma_id": None}
    r.update(ubah)
    return r


def _body(**ubah):
    d = dict(customer_id=str(PELANGGAN).upper(), customer_name="Agung", amount=2000000, deposit_date=date(2026, 9, 26),
             payment_method="transfer", account_id=str(AKUN), sales_order_id=str(PESANAN),
             idempotency_key="k-1", reference="ref beda", notes="catatan beda")
    d.update(ubah)
    return CD.CreateCustomerDepositRequest(**d)


class _ConnDP:
    def __init__(self, baris):
        self.baris = baris
        self.tulis = []

    def transaction(self):
        c = self

        class _T:
            async def __aenter__(self):
                return c

            async def __aexit__(self, *e):
                return False
        return _T()

    async def execute(self, q, *a):
        return "OK"

    async def fetchrow(self, q, *a):
        if "idempotency_key = $2" in q:
            assert a == (TENANT, "k-1")
            return self.baris
        raise AssertionError("tak boleh lanjut ke jalur buat: " + q[:80])

    async def fetchval(self, q, *a):
        self.tulis.append(q)
        raise AssertionError("tak boleh menulis")


class _Pool:
    def __init__(self, c):
        self.c = c

    def acquire(self):
        p = self

        class _A:
            async def __aenter__(self):
                return p.c

            async def __aexit__(self, *e):
                return False
        return _A()


def _pasang_dp(monkeypatch, baris):
    c = _ConnDP(baris)

    async def gp():
        return _Pool(c)

    async def pre(*a, **k):
        return None
    monkeypatch.setattr(CD, "get_pool", gp)
    monkeypatch.setattr(CD, "get_user_context", lambda r: {"tenant_id": TENANT, "user_id": "u-1"})
    monkeypatch.setattr(CD, "_ensure_role_preconditions", pre)
    return c


@pytest.mark.asyncio
async def test_dp_kunci_sama_isi_sama_tetap_idempoten(monkeypatch):
    lama = _tersimpan()
    _pasang_dp(monkeypatch, lama)
    r = await CD.create_customer_deposit(None, _body())       # reference/notes beda + UUID huruf besar = maksud sama
    assert r["success"] is True and r["data"]["deposit_number"] == "DEP-2609-0009"


@pytest.mark.asyncio
@pytest.mark.parametrize("ubah,medan", [
    ({"amount": 1500000}, ["amount"]),
    ({"deposit_date": date(2026, 9, 25)}, ["deposit_date"]),
    ({"payment_method": "cash"}, ["payment_method"]),
    ({"account_id": str(uuid.uuid4())}, ["account_id"]),
    ({"customer_id": str(uuid.uuid4())}, ["customer_id"]),
    ({"sales_order_id": None}, ["sales_order_id"]),
    ({"proforma_id": str(uuid.uuid4())}, ["proforma_id"]),
    ({"amount": 1, "payment_method": "cash"}, ["amount", "payment_method"]),
])
async def test_dp_kunci_sama_isi_beda_409(monkeypatch, ubah, medan):
    lama = _tersimpan()
    _pasang_dp(monkeypatch, lama)
    with pytest.raises(HTTPException) as e:
        await CD.create_customer_deposit(None, _body(**ubah))
    assert e.value.status_code == 409
    d = e.value.detail
    assert d["code"] == "IDEMPOTENCY_KEY_REUSED" and d["fields"] == medan
    assert d["deposit_number"] == "DEP-2609-0009" and d["deposit_id"] == str(lama["id"])


def test_dp_tanpa_pelanggan_nama_jadi_pembeda():
    lama = _tersimpan(customer_id=None, customer_name="Toko A")
    assert CD._beda_maksud_dp(lama, _body(customer_id=None, customer_name=" Toko A ")) == []
    assert CD._beda_maksud_dp(lama, _body(customer_id=None, customer_name="Toko B")) == ["customer_name"]


def test_dp_jalur_balapan_unique_violation_juga_membandingkan():
    src = open(CD.__file__).read()
    # dua titik balasan idempoten (pra-cek + tangkapan UniqueViolation) sama-sama lewat pembanding
    assert src.count("_tolak_bila_maksud_beda(existing, body)") == 1
    assert src.count("_tolak_bila_maksud_beda(winner, body)") == 1
    assert src.count("quote_id, sales_order_id, proforma_id\n                        FROM customer_deposits") == 2


# ------------------------------------------------------------------ 2+3. terkirim / belum dikirim
def test_nilai_belum_dikirim_neto_proporsional():
    assert K.nilai_belum_dikirim(D("10"), D("900000"), D("4")) == D("540000.00")
    assert K.nilai_belum_dikirim(D("3"), D("100000"), D("1")) == D("66666.67")      # HALF_UP
    assert K.nilai_belum_dikirim(D("5"), D("500"), D("7")) == D("0")                # lebih kirim -> 0, bukan negatif
    assert K.nilai_belum_dikirim(D("0"), D("500"), None) == D("0")
    assert K.belum_dikirim(D("5"), None) == D("5")


class _ConnKirim:
    def __init__(self, baris_so, baris_kirim):
        self.baris_so, self.baris_kirim, self.q = baris_so, baris_kirim, []

    async def fetch(self, q, *a):
        self.q.append((q, a))
        if "FROM sales_orders so JOIN sales_order_items" in q:
            return self.baris_so
        if "invoice_fulfillment_items" in q:
            return self.baris_kirim
        raise AssertionError(q)


@pytest.mark.asyncio
async def test_terkirim_per_baris_sumber_dan_tanpa_tautan():
    so, b1, b2 = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    c = _ConnKirim([], [{"so_id": so, "soi_id": b1, "terkirim": D("4")},
                        {"so_id": so, "soi_id": None, "terkirim": D("2")}])
    per, tanpa = await K.terkirim_per_baris(c, TENANT, [so])
    assert per == {b1: D("4")} and tanpa == {so: D("2")}
    q, a = c.q[0]
    assert a == (TENANT, [so])
    assert "si.tenant_id = $1" in q and "f.tenant_id = si.tenant_id" in q
    # filter fulfillment AKTIF identik dengan V264 recompute_so_shipped (sumber header shipped_qty)
    assert "f.voided_at IS NULL AND f.status <> 'voided'" in q


@pytest.mark.asyncio
async def test_ringkasan_belum_dikirim_per_so():
    s1, s2, b1, b2, b3 = (uuid.uuid4() for _ in range(5))
    c = _ConnKirim(
        [{"so_id": s1, "soi_id": b1, "quantity": D("10"), "line_total": D("1000")},
         {"so_id": s1, "soi_id": b2, "quantity": D("2"), "line_total": D("300")},
         {"so_id": s2, "soi_id": b3, "quantity": D("1"), "line_total": D("50")}],
        [{"so_id": s1, "soi_id": b1, "terkirim": D("4")}, {"so_id": s2, "soi_id": b3, "terkirim": D("1")}],
    )
    r = await K.ringkasan_belum_dikirim(c, TENANT, ("draft", "cancelled", "completed"))
    assert r == {"total": D("900.00"), "count": 1}          # 600 + 300; s2 terkirim penuh -> tak dihitung
    q, a = c.q[0]
    assert "so.tenant_id = $1" in q and a == (TENANT, ["draft", "cancelled", "completed"])


def test_router_menyambung_medan_baru():
    src = open(SO.__file__).read()
    assert "so_kirim.terkirim_per_baris(conn, ctx[\"tenant_id\"], [order[\"id\"]])" in src
    assert "fulfilled_qty=float(terkirim.get(item[\"id\"], 0))" in src
    assert "so_kirim.ringkasan_belum_dikirim(conn, ctx[\"tenant_id\"], so_agregat.AKTIF_TIDAK)" in src
    from app.schemas import sales_orders as S
    for m in ("fulfilled_qty", "unfulfilled_qty", "unfulfilled_value"):
        assert m in S.SalesOrderItemResponse.model_fields
    assert {"unshipped_value", "unshipped_count"} <= set(S.SalesOrderSummary.model_fields)
    assert "fulfilled_qty_unlinked" in S.SalesOrderDetail.model_fields


@pytest.mark.asyncio
async def test_ringkasan_so_mengirim_unshipped(monkeypatch):
    class _C:
        async def fetchrow(self, q, *a):
            return {k: 0 for k in ("total_orders", "draft_count", "confirmed_count", "partial_shipped_count",
                                   "shipped_count", "partial_invoiced_count", "invoiced_count", "completed_count",
                                   "cancelled_count", "total_value", "pending_shipment_value", "pending_invoice_value")}

    async def gp():
        return _Pool(_C())

    async def belum(conn, t):
        return {"total": 0.0, "count": 0}

    async def kirim(conn, t, st):
        assert t == TENANT and st == ("draft", "cancelled", "completed")
        return {"total": D("900.00"), "count": 1}
    monkeypatch.setattr(SO, "get_pool", gp)
    monkeypatch.setattr(SO, "get_user_context", lambda r: {"tenant_id": TENANT})
    monkeypatch.setattr(SO.so_agregat, "uninvoiced", belum)
    monkeypatch.setattr(SO.so_kirim, "ringkasan_belum_dikirim", kirim)

    async def sj(conn, t):
        assert t == TENANT
        return 0
    monkeypatch.setattr(SO.so_kirim, "jumlah_surat_jalan", sj)
    d = (await SO.get_sales_order_summary(None)).data
    d = d if isinstance(d, dict) else d.model_dump()
    assert d["unshipped_value"] == 900.0 and d["unshipped_count"] == 1 and d["fulfillment_count"] == 0


@pytest.mark.asyncio
async def test_jumlah_surat_jalan_aktif_per_tenant():
    class _C:
        async def fetchval(self, q, *a):
            assert a == (TENANT,) and "f.tenant_id = $1" in q
            assert "f.voided_at IS NULL AND f.status <> 'voided'" in q
            return 3
    assert await K.jumlah_surat_jalan(_C(), TENANT) == 3


# ------------------------------------------------------------------ 4. hari ini tenant
@pytest.mark.asyncio
@pytest.mark.parametrize("utc,zona,harap", [
    (datetime(2026, 9, 30, 17, 30, tzinfo=timezone.utc), "Asia/Jakarta", "2026-10-01"),   # 00.30 WIB tgl 1
    (datetime(2026, 9, 30, 16, 30, tzinfo=timezone.utc), "Asia/Jakarta", "2026-09-30"),
    (datetime(2026, 9, 30, 16, 30, tzinfo=timezone.utc), "Asia/Makassar", "2026-10-01"),  # 00.30 WITA
])
async def test_tenant_today_zona_tenant(monkeypatch, utc, zona, harap):
    class _Jam(datetime):
        @classmethod
        def now(cls, tz=None):
            return utc
    monkeypatch.setattr(tt, "datetime", _Jam)
    tt._cache.clear()

    class _C:
        async def fetchval(self, q, *a):
            assert '"Tenant"' in q and a == (TENANT,)
            return zona

    async def gp():
        return _Pool(_C())
    import app.services.db_pool as dbp
    monkeypatch.setattr(dbp, "get_db_pool", gp)
    req = SimpleNamespace(state=SimpleNamespace(user={"tenant_id": TENANT}), headers={})
    r = await TP.get_tenant_today(req)
    tt._cache.clear()
    assert r == {"success": True, "data": {"date": harap, "timezone": zona}}


def test_tenant_today_baca_terbuka_hanya_rute_itu():
    from app.middleware import permission_middleware as PM
    pola = [re.compile(p) for p in PM.READ_DEFAULT_OPEN_ALLOWLIST]
    assert any(p.match("/api/tenant/today") for p in pola)
    assert not any(p.match("/api/tenant/todayx") for p in pola)
    assert not any(p.match("/api/tenant/today/rahasia") for p in pola)
