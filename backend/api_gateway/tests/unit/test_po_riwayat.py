"""Riwayat Pesanan Pembelian (22 Okt 2026): bentuk SAMA dgn /sales-orders/{id}/history; siklus PO beraktor + audit + tagihan bertaut."""
import asyncio
import inspect
import os
import uuid
from datetime import datetime, timezone
from decimal import Decimal

os.environ.setdefault("OPENAI_API_KEY", "sk-boneka-unit-test-tanpa-jaringan")

from app.routers import purchase_orders as PO  # noqa: E402
from app.services import po_riwayat as PR, so_riwayat as SR  # noqa: E402

T = "kaos-biru-konveksi"
PID, U1, U2 = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
W = lambda h: datetime(2026, 10, 22, h, tzinfo=timezone.utc)  # noqa: E731


class Kon:
    def __init__(self, ada=True, bills=()):
        self.ada, self.bills = ada, list(bills)
    async def fetchrow(self, sql, *a):
        if not self.ada: return None
        return {"id": PID, "po_number": "PO-2610-0001", "total_amount": Decimal("1110.00"), "status": "closed",
                "created_at": W(1), "created_by": U1, "sent_at": W(2), "sent_by": U2,
                "cancelled_at": None, "cancelled_by": None, "cancelled_reason": None, "closed_at": W(5), "closed_by": U1}
    async def fetch(self, sql, *a):
        return self.bills


def _jalan(monkeypatch, kon, izin=("purchase_order", "purchase_invoice")):
    tangkap = {}
    async def sel(conn, tid, k, ent, lihat, limit):
        tangkap.update(ev=k.ev, ent=ent, lihat=lihat); return [], len(k.ev)
    monkeypatch.setattr(PR, "_selesaikan", sel)
    async def boleh(m): return m in izin
    return asyncio.run(PR.riwayat_po(kon, T, PID, boleh)), tangkap


def test_tak_ada_none_untuk_404(monkeypatch):
    r, _ = _jalan(monkeypatch, Kon(ada=False))
    assert r is None


def test_siklus_beraktor_dan_tagihan_bertaut(monkeypatch):
    b = {"id": uuid.uuid4(), "invoice_number": "PB-2610-0003", "grand_total": Decimal("1110.00"), "created_at": W(4), "created_by": U2}
    r, t = _jalan(monkeypatch, Kon(bills=[b]))
    jenis = [(e["jenis"], e["aktor_id"]) for e in t["ev"]]
    assert ("PO_DIBUAT", str(U1)) in jenis and ("PO_DIKIRIM", str(U2)) in jenis and ("PO_DITUTUP", str(U1)) in jenis
    assert ("TAGIHAN_DIBUAT", str(U2)) in jenis and all(j != "PO_DIBATALKAN" for j, _ in jenis)       # kolom kosong -> tak ada kejadian
    assert t["ent"] == {"purchase_order": [PID]} and r["po_number"] == "PO-2610-0001" and r["omitted"] == []
    assert set(r) == {"purchase_order_id", "po_number", "events", "total", "omitted"}


def test_tanpa_izin_tagihan_disaring_dan_omitted(monkeypatch):
    b = {"id": uuid.uuid4(), "invoice_number": "PB-1", "grand_total": 1, "created_at": W(4), "created_by": U2}
    r, t = _jalan(monkeypatch, Kon(bills=[b]), izin=("purchase_order",))
    assert all(e["jenis"] != "TAGIHAN_DIBUAT" for e in t["ev"]) and r["omitted"] == ["purchase_invoice"]


def test_entitas_audit_dan_ringkas_terdaftar():
    assert SR.ENTITAS_AUDIT["purchase_order"] == "purchase_order" and SR.ENTITAS_AUDIT["purchase_orders"] == "purchase_order"
    assert SR.RINGKAS_AUDIT["PO_UPDATED"] and SR.RINGKAS_AUDIT["PO_RECEIVED"]


def test_router_mencatat_riwayat_dan_rute_history():
    assert "catat_riwayat(conn" in inspect.getsource(PO.update_purchase_order) and '"PO_UPDATED"' in inspect.getsource(PO.update_purchase_order)
    assert '"PO_RECEIVED"' in inspect.getsource(PO.receive_goods)
    assert any(r.path == "/{po_id}/history" and "GET" in r.methods for r in PO.router.routes)
