"""BUG-058 (6 Nov 2026, Law 12): ubah draf faktur pembelian meninggalkan jejak BILL_UPDATED (medan + total sebelum -> sesudah) di transaksi
yang sama; riwayat "dibuat" memakai nominal SAAT DIBUAT (total_sebelum ubah pertama), bukan nilai sekarang."""
import asyncio
import json
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from decimal import Decimal as D
from uuid import UUID

os.environ.setdefault("OPENAI_API_KEY", "sk-boneka-unit-test-tanpa-jaringan")

from app.services import audit_dokumen as AD  # noqa: E402
from app.services import bills_service as BS  # noqa: E402
from app.services import tagihan_rencana as TR  # noqa: E402
from app.services import tagihan_riwayat as TRW  # noqa: E402

BILL = UUID("80000000-0000-0000-0000-000000000058")


class _K:
    def __init__(self):
        self.exec = []

    async def fetchrow(self, sql, *a):
        return {"id": BILL, "status_v2": "draft", "vendor_id": None, "tax_rate": 0, "invoice_number": "BILL-T", "amount": D("14880000")}

    async def fetchval(self, sql, *a):
        return 0

    async def fetch(self, sql, *a):
        return []

    async def execute(self, sql, *a):
        self.exec.append((sql, a))

    def transaction(self):
        @asynccontextmanager
        async def tx():
            yield
        return tx()


def test_event_bill_updated_terdaftar_tampil_di_riwayat():
    ev = AD.EVENT[("bill", "updated")]
    assert ev.nama == "BILL_UPDATED" and ev.entitas == "bills" and ev.riwayat is True


def test_update_bill_v2_mencatat_jejak_ubah_dengan_total(monkeypatch):
    async def nol(*a, **k):
        return []

    async def hitung(conn, tid, req):
        return {"header": {f: D("16516800") if f == "grand_total" else 0 for f in ("subtotal", "item_discount_total", "invoice_discount_total",
                                                                                   "cash_discount_total", "dpp", "tax_amount", "grand_total")},
                "baris": [{"item_calc": {"discount_amount": 0, "total": 1, "subtotal": 1}, "dpp": 1, "ppn": 0}]}

    async def taut(conn, tid, bid, items):
        return [None] * len(items), []

    async def timpa(*a):
        return None

    monkeypatch.setattr(TR, "blok_ubah", nol)
    monkeypatch.setattr(TR, "hitung", hitung)
    monkeypatch.setattr(TR, "tautan_po", taut)
    monkeypatch.setattr(BS, "_timpa_nama_dari_master", timpa)
    k = _K()

    class _Pool:
        @asynccontextmanager
        async def acquire(self):
            yield k

    item = {"product_id": None, "product_name": "x", "qty": 40, "price": 233100}
    r = asyncio.run(BS.BillsService(_Pool()).update_bill_v2("t", BILL, {"items": [item], "ref_no": "INV/1"}, "u-1"))
    assert r["success"], r
    audit = [a for s, a in k.exec if "INSERT INTO audit_logs" in s]
    assert len(audit) == 1
    uid, ev, ent, eid, nomor, tid, src, meta = audit[0]
    m = json.loads(meta)
    assert (ev, ent, eid, nomor, uid) == ("BILL_UPDATED", "bills", str(BILL), "BILL-T", "u-1")
    assert m["medan"] == ["items", "ref_no"] and m["total_sebelum"] == "14880000" and m["total_sesudah"] == "16516800"
    assert "barang, no. faktur vendor" in m["ringkas"] and "14.880.000" in m["ringkas"] and "16.516.800" in m["ringkas"]


class _KR:
    def __init__(self, awal):
        self.awal = awal

    async def fetchrow(self, sql, *a):
        return {"id": BILL, "invoice_number": "BILL-T", "amount": D("16516800"), "created_at": datetime(2026, 11, 6, tzinfo=timezone.utc),
                "created_by": None, "posted_at": None, "posted_by": None, "voided_at": None, "voided_reason": None, "po_number": "PO-1"}

    async def fetchval(self, sql, *a):
        return self.awal if "total_sebelum" in sql else None

    async def fetch(self, sql, *a):
        return []


def _dibuat(monkeypatch, awal):
    async def selesai(conn, tid, k, ids, lihat, limit):
        return [e for e in k.__dict__.get("ev", k.__dict__.get("baris", []))], 0
    async def boleh(m):
        return False
    captured = {}
    orig = TRW._Kumpul.tambah

    def tambah(self, waktu, jenis, teks, *a, **kw):
        if jenis == "TAGIHAN_DIBUAT":
            captured["teks"] = teks
        return orig(self, waktu, jenis, teks, *a, **kw)

    monkeypatch.setattr(TRW._Kumpul, "tambah", tambah)
    monkeypatch.setattr(TRW, "_selesaikan", selesai)
    asyncio.run(TRW.riwayat_tagihan(_KR(awal), "t", BILL, boleh))
    return captured["teks"]


def test_riwayat_dibuat_memakai_nominal_saat_dibuat(monkeypatch):
    assert "Rp14.880.000" in _dibuat(monkeypatch, "14880000.00").replace(" ", "")


def test_riwayat_dibuat_tanpa_ubah_memakai_nilai_sekarang(monkeypatch):
    assert "Rp16.516.800" in _dibuat(monkeypatch, None).replace(" ", "")
