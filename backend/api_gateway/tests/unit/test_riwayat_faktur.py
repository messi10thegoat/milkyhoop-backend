"""Riwayat FAKTUR bentuk riwayat SO (30 Sep 2026): GET /sales-invoices/{id}/history?format=events.

Pemetaan faktur/Surat Jalan/pembayaran = _bagian_faktur yang SAMA dengan riwayat SO (paritas prod 538 riwayat
SO x 2317 kejadian = 0 beda); tambahan faktur: uang muka diterapkan, nota kredit, SO asal; dokumen terkait
disaring izin BACA -> omitted. Tanpa format = bentuk LAMA (klien lama tak berubah)."""
import inspect
import json
import os
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import UUID
from zoneinfo import ZoneInfo

os.environ.setdefault("OPENAI_API_KEY", "sk-boneka-unit-test-tanpa-jaringan")

import pytest  # noqa: E402

from app.services import so_riwayat as SR  # noqa: E402
from app.routers import sales_invoices as SI  # noqa: E402

T = "kaos-biru-konveksi"
INV, SOID, SJ, PAY, DP, CN = (UUID(f"a000000{i}-0000-0000-0000-000000000001") for i in range(6))
U1 = "00000000-0000-0000-0000-0000000000a1"


def _t(j):
    return datetime(2026, 9, 30, j, 0, tzinfo=timezone.utc)


class _C:
    def __init__(self, ada=True):
        self.ada, self.q = ada, []

    async def fetchrow(self, sql, *a):
        self.q.append((sql, a))
        assert "si.tenant_id = $2" in sql and a[1] == T
        return ({"id": INV, "invoice_number": "INV-9", "total_amount": 300000, "created_at": _t(1), "created_by": UUID(U1),
                 "posted_at": _t(2), "posted_by": UUID(U1), "voided_at": None, "voided_reason": None,
                 "sales_order_id": SOID, "order_number": "SO-9"} if self.ada else None)

    async def fetch(self, sql, *a):
        self.q.append((sql, a))
        if "FROM invoice_fulfillments" in sql:
            return [{"id": SJ, "fulfillment_number": "SJ-9", "created_at": _t(3), "created_by": UUID(U1), "posted_at": _t(3),
                     "posted_by": UUID(U1), "voided_at": None, "voided_reason": None, "invoice_number": "INV-9"}]
        if "FROM receive_payment_allocations" in sql:
            return [{"id": PAY, "payment_number": "RCV-9", "invoice_number": "INV-9", "amount_applied": 100000,
                     "posted_at": _t(5), "posted_by": UUID(U1), "voided_at": None, "voided_by": None, "void_reason": None,
                     "reversed_at": None, "reversed_by": None, "unapply_reason": None}]
        if "FROM customer_deposit_applications" in sql:
            assert "a.invoice_id = $2" in sql and "a.tenant_id = $1" in sql and a[0] == T
            return [{"deposit_id": DP, "deposit_number": "DEP-9", "amount_applied": 50000, "created_at": _t(4),
                     "created_by": UUID(U1), "reversed_at": None}]
        if "FROM credit_notes" in sql:
            assert "original_invoice_id = $2" in sql and "WHERE tenant_id = $1" in sql and a[0] == T
            return [{"id": CN, "credit_note_number": "CN-9", "total_amount": 20000, "created_at": _t(6), "created_by": UUID(U1),
                     "posted_at": _t(7), "posted_by": UUID(U1), "voided_at": None, "voided_by": None, "voided_reason": None}]
        if "FROM audit_logs" in sql:
            assert a[0] == T
            return []
        if 'FROM "User"' in sql:
            return [{"id": U1, "nama": "Anton"}]
        raise AssertionError(sql[:60])


@pytest.fixture(autouse=True)
def _zona(monkeypatch):
    async def z(conn, tid):
        return ZoneInfo("Asia/Jakarta")
    monkeypatch.setattr(SR, "zona_tenant", z)


def _izin(tolak=()):
    async def b(m):
        return m not in tolak
    return b


@pytest.mark.asyncio
async def test_lengkap_urut_terbaru_dulu():
    r = await SR.riwayat_faktur(_C(), T, INV, _izin(), 200)
    assert [e["jenis"] for e in r["events"]] == ["NOTA_KREDIT_DITERBITKAN", "NOTA_KREDIT_DIBUAT", "PEMBAYARAN_DITERIMA",
                                                 "UANG_MUKA_DITERAPKAN", "SURAT_JALAN_DIBUAT", "FAKTUR_DITERBITKAN", "FAKTUR_DIBUAT"]
    dibuat = r["events"][-1]
    assert dibuat["ringkas"] == "Faktur INV-9 Rp300.000 dibuat dari pesanan SO-9"
    assert dibuat["aktor"] == {"id": U1, "nama": "Anton"} and dibuat["at"].endswith("+07:00")
    assert r["sales_order"] == {"id": str(SOID), "order_number": "SO-9"} and r["omitted"] == [] and r["total"] == 7
    dp = [e for e in r["events"] if e["jenis"] == "UANG_MUKA_DITERAPKAN"][0]
    assert dp["dokumen"] == {"tipe": "customer_deposit", "id": str(DP), "nomor": "DEP-9"} and "Rp50.000" in dp["ringkas"]


@pytest.mark.asyncio
async def test_izin_baca_menyaring_dan_dilaporkan():
    c = _C()
    r = await SR.riwayat_faktur(c, T, INV, _izin(("credit_note", "customer_deposit", "sales_order")), 200)
    jenis = [e["jenis"] for e in r["events"]]
    assert not any(j.startswith(("NOTA_KREDIT", "UANG_MUKA")) for j in jenis)
    assert r["omitted"] == ["credit_note", "customer_deposit", "sales_order"]
    assert r["sales_order"] is None and r["events"][-1]["ringkas"] == "Faktur INV-9 Rp300.000 dibuat"
    assert not any("FROM credit_notes" in s or "customer_deposit_applications" in s for s, _ in c.q)


@pytest.mark.asyncio
async def test_faktur_tak_ada_none():
    assert await SR.riwayat_faktur(_C(ada=False), T, INV, _izin(), 200) is None


@pytest.mark.asyncio
async def test_setiap_kueri_bertenant():
    c = _C()
    await SR.riwayat_faktur(c, T, INV, _izin(), 200)
    for sql, a in c.q:
        if 'FROM "User"' in sql:
            continue
        assert T in a, sql[:60]


def test_satu_pemetaan_bersama_riwayat_so():
    for fn in (SR.riwayat_so, SR.riwayat_faktur):
        s = inspect.getsource(fn)
        assert "_bagian_faktur(" in s and "_selesaikan(" in s
        assert "FROM invoice_fulfillments" not in s and "FROM receive_payment_allocations a\n" not in s


def test_modul_so_tak_berubah():
    assert "credit_note" not in SR.MODUL and SR.MODUL_FAKTUR["credit_note"] == "credit_note"


def _req():
    return SimpleNamespace(state=SimpleNamespace(user={"user_id": U1, "tenant_id": T, "role": "OWNER"}))


class _Acq:
    def __init__(self, c):
        self.c = c

    async def __aenter__(self):
        return self.c

    async def __aexit__(self, *e):
        return False


class _Pool:
    def __init__(self, c):
        self.c = c

    def acquire(self):
        return _Acq(self.c)


@pytest.mark.asyncio
async def test_rute_format_events_memakai_riwayat_faktur(monkeypatch):
    dipanggil = {}

    async def rf(conn, tid, iid, boleh, limit):
        dipanggil["a"] = (tid, iid, limit)
        return {"invoice_id": str(iid), "events": [], "total": 0, "omitted": []}
    monkeypatch.setattr(SI, "riwayat_faktur", rf)

    async def pool():
        return _Pool(object())
    monkeypatch.setattr(SI, "get_pool", pool)
    r = await SI.get_invoice_history(_req(), INV, 50, "events")
    assert r["success"] and dipanggil["a"] == (T, INV, 50) and r["data"]["events"] == []


@pytest.mark.asyncio
async def test_rute_format_events_404(monkeypatch):
    from fastapi import HTTPException

    async def rf(*a, **k):
        return None
    monkeypatch.setattr(SI, "riwayat_faktur", rf)

    async def pool():
        return _Pool(object())
    monkeypatch.setattr(SI, "get_pool", pool)
    with pytest.raises(HTTPException) as e:
        await SI.get_invoice_history(_req(), INV, 50, "events")
    assert e.value.status_code == 404


def test_rute_tanpa_format_tetap_bentuk_lama():
    s = inspect.getsource(SI.get_invoice_history)
    lama = s[s.index('if format == "events":'):]
    assert "metadata->>'entity_type' = 'SALES_INVOICE'" in lama and '"changes": changes' in lama


def test_riwayat_so_teks_faktur_dibuat_dari_pesanan():
    """Paritas prod menangkap 'dibuat dibuat dari pesanan' saat bagian faktur dipisah; penjaga murah teksnya."""
    s = inspect.getsource(SR.riwayat_so)
    assert '_bagian_faktur(conn, tenant_id, k, fakturs, lihat, entitas_audit, " dari pesanan")' in s
    assert 'dibuat{akhiran_dibuat}' in inspect.getsource(SR._bagian_faktur)
