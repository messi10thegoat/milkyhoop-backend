"""Pill == baris == kartu (permintaan pemilik, 5 Okt 2026): ?tugas= memakai pemilih YANG SAMA dengan kartu."""
import asyncio
import inspect
import uuid
from decimal import Decimal

import pytest

from app.services import dashboard_v2 as DV
from app.services import so_agregat as SA
from app.routers import proformas as PF
from app.routers import sales_orders as SO
from app.routers import sales_invoices as SI

T = "tenant-uji"


def test_tugas_so_terdaftar_dan_rute_menerimanya():
    assert {"menunggu_tagih", "selesai"} <= set(DV.TUGAS_SO)
    ann = str(inspect.signature(SO.list_sales_orders).parameters["tugas"].annotation)
    assert "menunggu_tagih" in ann and "selesai" in ann


def test_menunggu_tagih_satu_pemilih_tanpa_potongan(monkeypatch):
    n = SA.BATAS_BARIS + 7
    rows = [{"id": uuid.uuid4(), "order_number": f"SO-{i:03d}", "customer_id": None, "customer_name": "X",
             "order_date": __import__("datetime").date(2026, 10, 1), "status": "confirmed",
             "total_amount": Decimal("10"), "draft_invoice_amount": Decimal("0")} for i in range(n + 3)]

    class C:
        async def fetch(self, sql, *a):
            assert a[0] == T
            return rows

    async def sisa(conn, tid, rs):
        return {r["id"]: (Decimal("5") if i < n else Decimal("0")) for i, r in enumerate(rs)}
    monkeypatch.setattr(SA, "_belum_ditagih_per_so", sisa)
    ids = asyncio.run(SA.id_belum_ditagih(C(), T))
    kartu = asyncio.run(SA.uninvoiced(C(), T))
    assert len(ids) == kartu["count"] == n  # tugas TIDAK terpotong BATAS_BARIS
    assert kartu["truncated"] is True


def test_id_tugas_so_memakai_pemilih_so_agregat(monkeypatch):
    a, b = [uuid.uuid4()], [uuid.uuid4()]

    async def bt(c, t): return a
    async def sl(c, t): return b
    monkeypatch.setattr(SA, "id_belum_ditagih", bt)
    monkeypatch.setattr(SA, "id_selesai", sl)
    assert asyncio.run(DV.id_tugas(None, T, "menunggu_tagih", None)) == a
    assert asyncio.run(DV.id_tugas(None, T, "selesai", None)) == b


def test_selesai_kartu_dan_tugas_satu_status():
    src = inspect.getsource(SO.get_sales_order_summary)
    assert "COUNT(*) FILTER (WHERE status = $2) as completed_count" in src and "so_agregat.STATUS_SELESAI" in src
    assert "status = $2" in inspect.getsource(SA.id_selesai) and SA.STATUS_SELESAI == "completed"


def test_proforma_kartu_dan_tugas_satu_pemilih(monkeypatch):
    p = [{"id": uuid.uuid4(), "sales_order_id": uuid.uuid4(), "status": s, "amount": Decimal("100")}
         for s in ("issued", "issued", "issued", "draft", "cancelled")]

    async def tb(conn, tid, so_ids):
        return {p[0]["id"]: {"paid": Decimal("100")}, p[1]["id"]: {"paid": Decimal("40")}}
    monkeypatch.setattr(PF, "terbayar_proforma", tb)
    k = asyncio.run(PF.kelas_bayar_terbit(None, T, p))
    assert [k[r["id"]][0] for r in p[:3]] == ["lunas", "sebagian", "belum"] and len(k) == 3

    class C:
        async def fetch(self, sql, *a):
            assert a[0] == T
            return p
    ids = asyncio.run(PF.id_terbit_belum_lunas(C(), T))
    assert set(ids) == {p[1]["id"], p[2]["id"]}  # == issued_unpaid + issued_partially_paid kartu
    assert "kelas_bayar_terbit(conn, tid, rows)" in inspect.getsource(PF.get_proforma_summary)
    src = inspect.getsource(PF.list_proformas)
    assert 'Literal["terbit_belum_lunas"]' in src and "await id_terbit_belum_lunas(conn, ctx[\"tenant_id\"])" in src


def test_ringkasan_faktur_overdue_aturan_telat_dan_unpaid_terdeklarasi():
    src = inspect.getsource(SI.get_invoice_summary)
    assert "si3.status = 'overdue'" not in src
    assert src.count("invoice_id IS NOT NULL AND outstanding > 0") == 3
    from app.schemas.sales_invoices import InvoiceSummary
    assert "unpaid_count" in InvoiceSummary.model_fields
