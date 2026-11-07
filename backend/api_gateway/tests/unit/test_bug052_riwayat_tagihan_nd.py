"""BUG-052: Riwayat faktur pembelian memuat nota debit ATAS faktur ini (vendor_credits.original_bill_id) -- dibuat/diposting/
dibatalkan -- cermin so_riwayat (nota kredit original_invoice_id). Dulu hanya via vendor_credit_applications, padahal ND retur dari
faktur memotong hutang langsung tanpa baris applications (diukur nyawit: VC-2611-0001..0005 = 0 baris applications)."""
import asyncio
from datetime import datetime, timezone
from decimal import Decimal as D

from app.services import tagihan_riwayat as TRW

BILL, VC = "11111111-1111-4111-8111-111111111111", "22222222-2222-4222-8222-222222222222"
T = lambda h: datetime(2026, 11, h, tzinfo=timezone.utc)  # noqa: E731


class _K:
    def __init__(self):
        self.sql = []

    async def fetchrow(self, sql, *a):
        return {"id": BILL, "invoice_number": "BILL-2611-0002", "amount": D("1000000"), "created_at": T(5), "created_by": None,
                "posted_at": None, "posted_by": None, "voided_at": None, "voided_reason": None, "po_number": None}

    async def fetchval(self, sql, *a):
        return None

    async def fetch(self, sql, *a):
        self.sql.append(sql)
        if "FROM vendor_credits WHERE" in sql and "original_bill_id" in sql:
            return [{"id": VC, "credit_number": "VC-2611-0001", "total_amount": D("463536"), "created_at": T(6), "created_by": None,
                     "posted_at": T(6), "posted_by": None, "voided_at": None, "voided_by": None, "voided_reason": None}]
        return []


def _jalan(monkeypatch, izin=True):
    tangkap = {}

    async def selesai(conn, tid, k, ids, lihat, limit):
        tangkap["ids"] = ids
        return k.ev, len(k.ev)

    async def boleh(m):
        return izin
    monkeypatch.setattr(TRW, "_selesaikan", selesai)
    r = asyncio.run(TRW.riwayat_tagihan(_K(), "t", BILL, boleh))
    return r, tangkap


def test_riwayat_tagihan_memuat_nota_debit_atas_faktur_ini(monkeypatch):
    r, tangkap = _jalan(monkeypatch)
    jenis = [e["jenis"] for e in r["events"]]
    assert "NOTA_DEBIT_DIBUAT" in jenis and "NOTA_DEBIT_DIPOSTING" in jenis
    teks = [e["ringkas"] for e in r["events"] if e["jenis"] == "NOTA_DEBIT_DIBUAT"][0]
    assert "VC-2611-0001" in teks and "463.536" in teks
    assert tangkap["ids"]["debit_note"] == [VC]   # jejak audit ND ikut digabung (pola so_riwayat)


def test_tanpa_izin_debit_note_tak_tampil(monkeypatch):
    r, _ = _jalan(monkeypatch, izin=False)
    assert not [e for e in r["events"] if e["jenis"].startswith("NOTA_DEBIT")]
