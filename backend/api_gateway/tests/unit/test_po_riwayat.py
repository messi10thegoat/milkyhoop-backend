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
    """Rute per kueri: tagihan PO / alokasi bayar keluar; anak lain kosong. `sql` mencatat teks kueri."""
    def __init__(self, ada=True, bills=(), bayar=()):
        self.ada, self.bills, self.bayar, self.sql = ada, list(bills), list(bayar), []
    async def fetchrow(self, sql, *a):
        if not self.ada: return None
        return {"id": PID, "po_number": "PO-2610-0001", "total_amount": Decimal("1110.00"), "status": "closed",
                "created_at": W(1), "created_by": U1, "sent_at": W(2), "sent_by": U2,
                "cancelled_at": None, "cancelled_by": None, "cancelled_reason": None, "closed_at": W(5), "closed_by": U1}
    async def fetchval(self, sql, *a):
        return None
    async def fetch(self, sql, *a):
        self.sql.append(" ".join(sql.split()))
        if "FROM bills b" in sql: return self.bills
        if "bill_payment_allocations" in sql: return self.bayar
        return []


def _bill(**kw):
    return {"id": uuid.uuid4(), "invoice_number": "PB-2610-0003", "amount": Decimal("1110.00"), "created_at": W(4), "created_by": U2,
            "posted_at": None, "posted_by": None, "voided_at": None, "voided_reason": None, "po_number": "PO-2610-0001", **kw}


SEMUA = ("purchase_order", "purchase_invoice", "send_payment", "vendor_deposit", "debit_note")


def _jalan(monkeypatch, kon, izin=SEMUA):
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
    b = _bill()
    r, t = _jalan(monkeypatch, Kon(bills=[b]))
    jenis = [(e["jenis"], e["aktor_id"]) for e in t["ev"]]
    assert ("PO_DIBUAT", str(U1)) in jenis and ("PO_DIKIRIM", str(U2)) in jenis and ("PO_DITUTUP", str(U1)) in jenis
    assert ("TAGIHAN_DIBUAT", str(U2)) in jenis and all(j != "PO_DIBATALKAN" for j, _ in jenis)       # kolom kosong -> tak ada kejadian
    assert t["ent"] == {"purchase_order": [PID], "bill": [b["id"]]} and r["po_number"] == "PO-2610-0001" and r["omitted"] == []
    assert set(r) == {"purchase_order_id", "po_number", "events", "total", "omitted"}


def test_bug112_posting_dan_pembayaran_tagihan_tampil(monkeypatch):
    """BUG-112 (d): PO-2611-0010 -- riwayat dulu hanya "Tagihan dibuat"; posting + pembayaran keluar kini ikut (bagian_tagihan bersama)."""
    b = _bill(posted_at=W(6), posted_by=U1)
    bp = {"id": uuid.uuid4(), "payment_number": "BP-2611-0009", "posted_at": W(7), "posted_by": U2, "voided_at": None, "voided_by": None,
          "void_reason": None, "bill_id": b["id"], "jumlah": Decimal("500000")}
    r, t = _jalan(monkeypatch, Kon(bills=[b], bayar=[bp]))
    ev = {e["jenis"]: e for e in t["ev"]}
    assert ev["TAGIHAN_DIPOSTING"]["aktor_id"] == str(U1)
    assert ev["PEMBAYARAN_DIBAYAR"]["ringkas"] == "Pembayaran BP-2611-0009 Rp 500.000 dialokasikan ke faktur pembelian PB-2610-0003"
    assert ev["PEMBAYARAN_DIBAYAR"]["dokumen"]["tipe"] == "payment_out"


def test_bug112_tagihan_bertaut_lewat_baris_ikut(monkeypatch):
    """Tagihan tanpa bills.purchase_order_id (data BUG-036) tertaut lewat bill_items.purchase_order_item_id -> tetap tampil."""
    kon = Kon(bills=[_bill(po_number=None)])
    _jalan(monkeypatch, kon)
    q = [x for x in kon.sql if "FROM bills b" in x][0]
    assert "b.purchase_order_id = $2 OR b.id IN" in q and "bi.purchase_order_item_id" in q and "i.po_id = $2" in q


def test_tanpa_izin_bayar_keluar_omitted_tanpa_pembayaran(monkeypatch):
    b = _bill(posted_at=W(6), posted_by=U1)
    bp = {"id": uuid.uuid4(), "payment_number": "BP-1", "posted_at": W(7), "posted_by": U2, "voided_at": None, "voided_by": None,
          "void_reason": None, "bill_id": b["id"], "jumlah": 1}
    r, t = _jalan(monkeypatch, Kon(bills=[b], bayar=[bp]), izin=("purchase_order", "purchase_invoice"))
    assert all(e["jenis"] != "PEMBAYARAN_DIBAYAR" for e in t["ev"]) and "send_payment" in r["omitted"]
    assert "TAGIHAN_DIPOSTING" in [e["jenis"] for e in t["ev"]]


def test_tanpa_izin_tagihan_disaring_dan_omitted(monkeypatch):
    r, t = _jalan(monkeypatch, Kon(bills=[_bill()]), izin=("purchase_order",))
    assert all(e["jenis"] != "TAGIHAN_DIBUAT" for e in t["ev"]) and "purchase_invoice" in r["omitted"]
    assert t["ent"]["bill"] == []


def test_po_dan_tagihan_satu_bagian_bersama():
    from app.services import tagihan_riwayat as TR
    assert PR.bagian_tagihan is TR.bagian_tagihan and "TAGIHAN_DIBUAT" not in inspect.getsource(PR)


def test_entitas_audit_dan_ringkas_terdaftar():
    assert SR.ENTITAS_AUDIT["purchase_order"] == "purchase_order" and SR.ENTITAS_AUDIT["purchase_orders"] == "purchase_order"
    assert SR.RINGKAS_AUDIT["PO_UPDATED"] and SR.RINGKAS_AUDIT["PO_RECEIVED"]


def test_router_mencatat_riwayat_dan_rute_history():
    # AUDIT-DOKUMEN (1 Nov 2026, OK MASTER): nama event dari SATU tabel services/audit_dokumen
    from app.services import audit_dokumen as AD
    assert AD.nama("purchase_order", "updated") == "PO_UPDATED" and AD.nama("purchase_order", "received") == "PO_RECEIVED"
    assert "catat_riwayat(conn" in inspect.getsource(PO.update_purchase_order)
    assert '_ad.nama("purchase_order", "updated")' in inspect.getsource(PO.update_purchase_order)
    assert '_ad.nama("purchase_order", "received")' in inspect.getsource(PO.receive_goods)
    assert any(r.path == "/{po_id}/history" and "GET" in r.methods for r in PO.router.routes)
