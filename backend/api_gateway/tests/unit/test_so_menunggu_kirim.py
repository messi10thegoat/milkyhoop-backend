"""MENUNGGU KIRIM nyata (27 Sep 2026): ringkasan SO pending_shipment_value/_count dari baris PERLU DIKIRIM bersisa qty,
bukan status SO. Bukti prod (baca-saja): grapgrap 21 SO confirmed Rp 56 jt, 30 baris, 0 baris perlu kirim -> dulu
kartu MENUNGGU KIRIM = MENUNGGU TAGIH (himpunan sama: tak ada yang dikirim, status tertahan di confirmed)."""
import uuid
from decimal import Decimal as D

import pytest

from app.routers import sales_orders as SO
from app.services import so_kirim as K

TENANT = "grapgrap-manado"
AKTIF_TIDAK = ("draft", "cancelled", "completed")


class _Conn:
    def __init__(self, baris_so, baris_kirim):
        self.baris_so, self.baris_kirim, self.q = baris_so, baris_kirim, []

    async def fetch(self, q, *a):
        self.q.append((q, a))
        if "FROM sales_orders so" in q:
            return self.baris_so
        if "invoice_fulfillment_items" in q:
            return self.baris_kirim
        raise AssertionError(q)


@pytest.mark.asyncio
async def test_sisa_per_baris_dan_jumlah_so():
    s1, s2, s3, b1, b2, b3, b4 = (uuid.uuid4() for _ in range(7))
    c = _Conn(
        [{"so_id": s1, "soi_id": b1, "quantity": D("10"), "line_total": D("1000")},   # terkirim 4 -> sisa 600
         {"so_id": s1, "soi_id": b2, "quantity": D("2"), "line_total": D("300")},     # belum -> 300
         {"so_id": s2, "soi_id": b3, "quantity": D("1"), "line_total": D("50")},      # terkirim penuh -> tidak
         {"so_id": s3, "soi_id": b4, "quantity": D("5"), "line_total": D("0")}],      # gratis bersisa -> dihitung SO
        [{"so_id": s1, "soi_id": b1, "terkirim": D("4")}, {"so_id": s2, "soi_id": b3, "terkirim": D("1")}],
    )
    r = await K.ringkasan_menunggu_kirim(c, TENANT, AKTIF_TIDAK)
    assert r == {"total": D("900.00"), "count": 2}


@pytest.mark.asyncio
async def test_tanpa_baris_perlu_kirim_nol():
    r = await K.ringkasan_menunggu_kirim(_Conn([], []), TENANT, AKTIF_TIDAK)
    assert r["total"] == 0 and r["count"] == 0


@pytest.mark.asyncio
async def test_sql_menyaring_baris_perlu_kirim_per_tenant():
    c = _Conn([], [])
    await K.ringkasan_menunggu_kirim(c, TENANT, AKTIF_TIDAK)
    q, a = c.q[0]
    sq = " ".join(q.split())
    assert a == (TENANT, list(AKTIF_TIDAK))
    # literal spek (ekspresi V318 so_memenuhi_selesai / detail requires_fulfillment), bukan konstanta modul
    assert "AND COALESCE(soi.perlu_kirim, p.track_inventory, false)" in sq
    assert "LEFT JOIN products p ON p.id = soi.item_id AND p.tenant_id = so.tenant_id" in sq
    assert "so.tenant_id = $1 AND so.status <> ALL($2::text[])" in sq
    assert "confirmed" not in sq and "partial_shipped" not in sq


class _Pool:
    def __init__(self, c):
        self.c = c

    def acquire(self):
        c = self.c

        class _A:
            async def __aenter__(self):
                return c

            async def __aexit__(self, *e):
                return False
        return _A()


@pytest.mark.asyncio
async def test_ringkasan_memakai_baris_bukan_status(monkeypatch):
    """Keadaan grapgrap: 21 SO confirmed Rp 56 jt, nol baris perlu kirim -> 0 / 0 (dulu Σ total SO per status)."""
    q_row = []

    class _C:
        async def fetchrow(self, q, *a):
            q_row.append(q)
            r = {k: 0 for k in ("total_orders", "draft_count", "partial_shipped_count", "shipped_count",
                                "partial_invoiced_count", "invoiced_count", "completed_count", "cancelled_count",
                                "total_value", "pending_invoice_value")}
            r.update(confirmed_count=21, pending_shipment_value=56000000)   # andai SQL status lama masih ada
            return r

    async def gp():
        return _Pool(_C())

    async def belum(conn, t):
        return {"total": 56000000.0, "count": 21}

    async def kirim(conn, t, st):
        return {"total": D("56000000"), "count": 21}   # unshipped (semua baris) — definisi lain, tak dipakai kartu

    async def menunggu(conn, t, st):
        assert t == TENANT and st == AKTIF_TIDAK
        return {"total": D("0"), "count": 0}

    async def sj(conn, t):
        return 0
    monkeypatch.setattr(SO, "get_pool", gp)
    monkeypatch.setattr(SO, "get_user_context", lambda r: {"tenant_id": TENANT})
    monkeypatch.setattr(SO.so_agregat, "uninvoiced", belum)
    monkeypatch.setattr(SO.so_kirim, "ringkasan_belum_dikirim", kirim)
    monkeypatch.setattr(SO.so_kirim, "ringkasan_menunggu_kirim", menunggu)
    monkeypatch.setattr(SO.so_kirim, "jumlah_surat_jalan", sj)
    d = (await SO.get_sales_order_summary(None)).data
    d = d if isinstance(d, dict) else d.model_dump()
    assert d["pending_shipment_value"] == 0 and d["pending_shipment_count"] == 0
    assert d["confirmed_count"] == 21 and d["uninvoiced_count"] == 21    # medan status & tagih tetap
    assert "pending_shipment_value" not in q_row[0]


def test_skema_kontrak():
    from app.schemas import sales_orders as S
    f = S.SalesOrderSummary.model_fields
    assert {"pending_shipment_value", "pending_shipment_count"} <= set(f)
    assert S.SalesOrderSummary.model_validate(
        {**{k: 0 for k in f if k != "pending_shipment_value"}, "pending_shipment_value": 1234.5}
    ).pending_shipment_value == 1234.5     # nilai NETO bisa bersen (dulu int)
