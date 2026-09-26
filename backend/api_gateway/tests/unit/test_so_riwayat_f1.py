"""Riwayat SO + F1 short close (26 Sep 2026, arahan pemilik via MASTER).

Riwayat = turunan kolom dokumen + audit_logs (kejadian tanpa kolom), tenant eksplisit,
dokumen terkait disaring izin BACA (omitted). F1: SO 'confirmed' boleh ditutup beralasan;
faktur ditagih tapi pendapatan belum diakui (allocated - recognized > 0.005) -> DITOLAK.
cancel/confirm: UPDATE bersyarat -> 409 bila status berubah di antaranya.
"""
import inspect
import json
import os
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import UUID
from zoneinfo import ZoneInfo

os.environ.setdefault("OPENAI_API_KEY", "sk-boneka-unit-test-tanpa-jaringan")

import pytest  # noqa: E402
from fastapi import HTTPException  # noqa: E402

from app.services import so_riwayat as SR  # noqa: E402
from app.routers import sales_orders as SO  # noqa: E402
from app.routers import proformas as PF  # noqa: E402
from app.routers import customer_deposits as CD  # noqa: E402

T = "kaos-biru-konveksi"
SOID = UUID("10000000-0000-0000-0000-000000000001")
U1, U2 = "00000000-0000-0000-0000-0000000000a1", "00000000-0000-0000-0000-0000000000a2"
DP, PFID, INV, SJ, PAY = (UUID(f"2000000{i}-0000-0000-0000-000000000001") for i in range(5))


def _t(jam):
    return datetime(2026, 9, 26, jam, 0, tzinfo=timezone.utc)


class _RConn:
    """Tiruan untuk riwayat_so: jawab per tabel; catat SEMUA SQL + argumen."""

    def __init__(self, ada_so=True, audit=None):
        self.q = []
        self.ada_so = ada_so
        self.audit = audit or []

    async def fetchrow(self, sql, *a):
        self.q.append((sql, a))
        if "FROM sales_orders" in sql:
            return ({"id": SOID, "order_number": "SO-1", "created_at": _t(1), "created_by": UUID(U1),
                     "confirmed_at": _t(2), "confirmed_by": UUID(U2)} if self.ada_so else None)
        raise AssertionError(sql[:60])

    async def fetch(self, sql, *a):
        self.q.append((sql, a))
        if "FROM customer_deposits" in sql:
            return [{"id": DP, "deposit_number": "DP-1", "amount": 500000, "status": "posted",
                     "created_at": _t(3), "created_by": UUID(U1), "posted_at": _t(3), "posted_by": UUID(U1),
                     "voided_at": None, "voided_by": None, "voided_reason": None}]
        if "FROM customer_deposit_applications" in sql:
            return []
        if "FROM proformas" in sql:
            return [{"id": PFID, "proforma_number": "PF-1", "amount": 500000, "created_at": _t(2),
                     "created_by": UUID(U1), "issued_at": _t(2), "cancelled_at": None, "cancelled_reason": None}]
        if "FROM sales_invoices" in sql:
            return [{"id": INV, "invoice_number": "INV-1", "total_amount": 1000000, "created_at": _t(4),
                     "created_by": UUID(U2), "posted_at": _t(4), "posted_by": UUID(U2),
                     "voided_at": None, "voided_reason": None}]
        if "FROM invoice_fulfillments" in sql:
            return [{"id": SJ, "fulfillment_number": "SJ-1", "created_at": _t(5), "created_by": UUID(U2),
                     "posted_at": _t(5), "posted_by": UUID(U2), "voided_at": None, "voided_reason": None,
                     "invoice_number": "INV-1"}]
        if "FROM receive_payment_allocations" in sql:
            return [{"id": PAY, "payment_number": "RCV-1", "invoice_number": "INV-1", "amount_applied": 500000,
                     "posted_at": _t(6), "posted_by": UUID(U1), "voided_at": None, "voided_by": None,
                     "void_reason": None, "reversed_at": None, "reversed_by": None, "unapply_reason": None}]
        if "FROM audit_logs" in sql:
            pasang = set(zip(a[1], a[2]))
            return [r for r in self.audit if (r["entity_type"], str(r["entity_id"])) in pasang]
        if 'FROM "User"' in sql:
            return [{"id": U1, "nama": "Anton"}, {"id": U2, "nama": "Staf"}]
        raise AssertionError(sql[:60])


@pytest.fixture(autouse=True)
def _zona(monkeypatch):
    async def z(conn, tid):
        return ZoneInfo("Asia/Jakarta")
    monkeypatch.setattr(SR, "zona_tenant", z)


def _izin(*modul):
    async def boleh(m):
        return m in modul
    return boleh


SEMUA = _izin("sales_order", "customer_deposit", "proforma", "sales_invoice", "receive_payment")


@pytest.mark.asyncio
async def test_so_tak_ada_none():
    assert await SR.riwayat_so(_RConn(ada_so=False), T, SOID, SEMUA) is None


@pytest.mark.asyncio
async def test_lengkap_urut_terbaru_aktor_wib():
    d = await SR.riwayat_so(_RConn(), T, SOID, SEMUA)
    jenis = [e["jenis"] for e in d["events"]]
    for j in ("SO_DIBUAT", "SO_DIKONFIRMASI", "UANG_MUKA_DITERIMA", "PROFORMA_DIBUAT", "FAKTUR_DIBUAT",
              "SURAT_JALAN_DIBUAT", "PEMBAYARAN_DITERIMA"):
        assert j in jenis, j
    assert jenis[0] == "PEMBAYARAN_DITERIMA" and jenis[-1] == "SO_DIBUAT"  # terbaru dulu
    dibuat = d["events"][-1]
    assert dibuat["aktor"] == {"id": U1, "nama": "Anton"}
    assert dibuat["at"] == "2026-09-26T08:00:00+07:00"
    assert d["omitted"] == []


@pytest.mark.asyncio
async def test_tanpa_izin_faktur_dan_penerimaan_disaring_dan_dilaporkan():
    k = _RConn(audit=[{"id": "a", "createdAt": _t(7), "eventType": "SALES_INVOICE_VOIDED", "userId": U1,
                       "entity_type": "sales_invoices", "entity_id": INV, "entity_number": "INV-1", "metadata": {}}])
    d = await SR.riwayat_so(k, T, SOID, _izin("sales_order", "customer_deposit", "proforma"))
    jenis = {e["jenis"] for e in d["events"]}
    assert not jenis & {"FAKTUR_DIBUAT", "SURAT_JALAN_DIBUAT", "PEMBAYARAN_DITERIMA", "SALES_INVOICE_VOIDED"}
    assert d["omitted"] == ["receive_payment", "sales_invoice"]
    assert not any("FROM invoice_fulfillments" in s or "FROM receive_payment_allocations" in s for s, _ in k.q)


@pytest.mark.asyncio
async def test_setiap_kueri_berpredikat_tenant():
    k = _RConn(audit=[])
    await SR.riwayat_so(k, T, SOID, SEMUA)
    for sql, a in k.q:
        if 'FROM "User"' in sql:
            continue
        assert "tenant_id" in sql and T in a, sql[:80]
    # JOIN ke tabel induk juga bertenant (id dari server bukan alasan melepas pagar)
    for sql, _ in k.q:
        if "JOIN" in sql and "unnest" not in sql:
            assert "AND d.tenant_id = $1" in sql or "AND si.tenant_id = $1" in sql or "AND rp.tenant_id = $1" in sql, sql[:80]


@pytest.mark.asyncio
async def test_audit_beraktor_menggantikan_baris_kolom_tanpa_aktor():
    k = _RConn(audit=[{"id": "a", "createdAt": _t(2), "eventType": "PROFORMA_ISSUED", "userId": U2,
                       "entity_type": "proformas", "entity_id": PFID, "entity_number": "PF-1",
                       "metadata": json.dumps({"ringkas": "Proforma PF-1 diterbitkan"})},
                      {"id": "b", "createdAt": _t(8), "eventType": "SALES_ORDER_CANCELLED", "userId": None,
                       "entity_type": "sales_orders", "entity_id": SOID, "entity_number": "SO-1",
                       "metadata": {"ringkas": "Pesanan SO-1 dibatalkan: salah input", "user_id": U1}}])
    d = await SR.riwayat_so(k, T, SOID, SEMUA)
    terbit = [e for e in d["events"] if "diterbitkan" in e["ringkas"] and e["dokumen"]["tipe"] == "proforma"]
    assert len(terbit) == 1 and terbit[0]["aktor"]["nama"] == "Staf" and terbit[0]["sumber"] == "audit"
    batal = [e for e in d["events"] if e["jenis"] == "SALES_ORDER_CANCELLED"][0]
    assert batal["aktor"]["id"] == U1 and batal["ringkas"].endswith("salah input")


@pytest.mark.asyncio
async def test_catat_riwayat_satu_baris_bertenant_berringkas():
    ditulis = []

    class K:
        async def execute(self, sql, *a):
            ditulis.append((sql, a))

    await SR.catat_riwayat(K(), T, "sales_orders", SOID, "SO-1", "SALES_ORDER_CANCELLED", U1, "Batal", {"reason": "x"})
    sql, a = ditulis[0]
    assert "INSERT INTO audit_logs" in sql and T in a and str(SOID) in a and "SALES_ORDER_CANCELLED" in a
    meta = json.loads(a[-1])
    assert meta == {"reason": "x", "ringkas": "Batal", "user_id": U1}


# ---------- rute ----------

def _req(uid=U1):
    return SimpleNamespace(state=SimpleNamespace(user={"user_id": uid, "tenant_id": T, "role": "OWNER"}))


@pytest.mark.asyncio
async def test_rute_uuid_buruk_404_tanpa_db(monkeypatch):
    async def pool():
        raise AssertionError("DB tersentuh")
    monkeypatch.setattr(SO, "get_pool", pool)
    with pytest.raises(HTTPException) as e:
        await SO.get_sales_order_history(_req(), "bukan-uuid", 200)
    assert e.value.status_code == 404


# ---------- F1 / cancel / confirm dengan koneksi tiruan ----------

class _Acq:
    def __init__(self, c):
        self.c = c

    async def __aenter__(self):
        return self.c

    async def __aexit__(self, *a):
        return False


class _Pool:
    def __init__(self, c):
        self.c = c

    def acquire(self):
        return _Acq(self.c)


class _AConn:
    def __init__(self, status="confirmed", tertahan=(), baris=((5, 0),), update_ok=True):
        self.status, self.tertahan, self.baris, self.update_ok = status, list(tertahan), list(baris), update_ok
        self.tulis = []

    def transaction(self):
        return _Acq(self)

    async def execute(self, sql, *a):
        if "pg_advisory" in sql:
            return
        self.tulis.append((" ".join(sql.split()), a))

    async def fetchrow(self, sql, *a):
        if "FROM sales_orders" in sql:
            return {"id": SOID, "status": self.status, "order_number": "SO-1", "shipped_qty": 0, "invoiced_qty": 0}
        raise AssertionError(sql[:60])

    async def fetch(self, sql, *a):
        if "allocated_amount" in sql:
            assert "si.tenant_id = $2" in sql and "NOT IN ('draft', 'void')" in sql
            return [{"invoice_number": "INV-1", "description": "Kaos", "sisa": s} for s in self.tertahan]
        if "FROM sales_order_items" in sql:
            return [{"description": f"B{i}", "quantity": q, "on_invoices": o} for i, (q, o) in enumerate(self.baris)]
        raise AssertionError(sql[:60])

    async def fetchval(self, sql, *a):
        self.tulis.append((" ".join(sql.split()), a))
        return SOID if self.update_ok else None


@pytest.fixture
def aksi(monkeypatch):
    async def dps(conn, tid, sid):
        return []
    monkeypatch.setattr(CD, "linked_so_deposits", dps)

    async def tanpa_dp(*a, **k):
        return None
    monkeypatch.setattr(SO, "_tolak_bila_ada_uang_muka_aktif", tanpa_dp)

    def pasang(c):
        async def pool():
            return _Pool(c)
        monkeypatch.setattr(SO, "get_pool", pool)
        return c
    return pasang


def _audit(c):
    return [a for s, a in c.tulis if "INSERT INTO audit_logs" in s]


async def _tutup(alasan=None):
    return await SO.close_sales_order(_req(), str(SOID), SO.CloseSalesOrderRequest(reason=alasan))


@pytest.mark.asyncio
async def test_f1_confirmed_tanpa_alasan_ditolak_kode_lama(aksi):
    c = aksi(_AConn())
    with pytest.raises(HTTPException) as e:
        await _tutup()
    assert e.value.detail["code"] == "SO_NOT_FULLY_INVOICED" and _audit(c) == []


@pytest.mark.asyncio
async def test_f1_confirmed_beralasan_short_close(aksi):
    c = aksi(_AConn(baris=((5, 0), (3, 1))))
    r = await _tutup("pelanggan batal")
    d = r.data if hasattr(r, "data") else r["data"]
    assert d["status"] == "completed" and d["forced"] is True
    assert [x["quantity_cancelled"] for x in d["cancelled_lines"]] == [5, 2]
    [a] = _audit(c)
    assert "SALES_ORDER_FORCE_CLOSED" in a and json.loads(a[-1])["reason"] == "pelanggan batal"


@pytest.mark.asyncio
async def test_f1_pendapatan_tertahan_ditolak_meski_beralasan(aksi):
    c = aksi(_AConn(status="invoiced", tertahan=[100000], baris=((5, 5),)))
    with pytest.raises(HTTPException) as e:
        await _tutup("apa pun")
    assert e.value.detail["code"] == "SO_REVENUE_NOT_RECOGNIZED"
    assert "Kirim barangnya / akui pendapatan non-stok dulu." in e.value.detail["message"]
    assert e.value.detail["lines"][0]["unrecognized_amount"] == 100000
    assert not any("UPDATE sales_orders" in s for s, _ in c.tulis)


@pytest.mark.asyncio
async def test_f1_penuh_tanpa_sisa_tutup_biasa_tercatat(aksi):
    c = aksi(_AConn(status="invoiced", baris=((5, 5),)))
    await _tutup()
    [a] = _audit(c)
    assert "SALES_ORDER_CLOSED" in a


@pytest.mark.asyncio
async def test_f1_draft_tetap_ditolak(aksi):
    aksi(_AConn(status="draft"))
    with pytest.raises(HTTPException) as e:
        await _tutup("x")
    assert e.value.status_code == 400


@pytest.mark.asyncio
async def test_batal_bersyarat_409_tanpa_audit(aksi):
    c = aksi(_AConn(update_ok=False))
    with pytest.raises(HTTPException) as e:
        await SO.cancel_sales_order(_req(), str(SOID), SO.CancelSalesOrderRequest(reason="x"))
    assert e.value.status_code == 409 and _audit(c) == []
    upd = [s for s, _ in c.tulis if "UPDATE sales_orders" in s][0]
    assert "COALESCE(invoiced_qty, 0) = 0" in upd and "status NOT IN" in upd


@pytest.mark.asyncio
async def test_batal_tercatat_beralasan(aksi):
    c = aksi(_AConn())
    await SO.cancel_sales_order(_req(), str(SOID), SO.CancelSalesOrderRequest(reason="salah input"))
    [a] = _audit(c)
    assert "SALES_ORDER_CANCELLED" in a and json.loads(a[-1])["ringkas"].endswith("salah input")


@pytest.mark.asyncio
async def test_konfirmasi_bersyarat_409(aksi):
    c = aksi(_AConn(status="draft", update_ok=False))
    with pytest.raises(HTTPException) as e:
        await SO.confirm_sales_order(_req(), str(SOID))
    assert e.value.status_code == 409
    assert "AND status = 'draft'" in [s for s, _ in c.tulis if "UPDATE sales_orders" in s][0]


def _src(fn):
    return " ".join(inspect.getsource(fn).split())


def test_penulis_audit_di_tx_sesudah_update():
    for fn, upd in ((PF.issue_proforma, "UPDATE proformas SET status = 'issued'"),
                    (PF.cancel_proforma, "UPDATE proformas SET status = 'cancelled'")):
        s = _src(fn)
        assert s.index("async with conn.transaction()") < s.index(upd) < s.index("catat_riwayat(")
    s = _src(SO.update_sales_order)
    assert s.index("UPDATE sales_orders SET") < s.index("\"SALES_ORDER_UPDATED\"")


@pytest.mark.asyncio
async def test_waktu_sama_yang_belakangan_di_atas():
    d = await SR.riwayat_so(_RConn(), T, SOID, SEMUA)
    j = [e["jenis"] for e in d["events"]]
    assert j.index("UANG_MUKA_DITERIMA") < j.index("UANG_MUKA_DIBUAT")
    assert j.index("PROFORMA_DITERBITKAN") < j.index("PROFORMA_DIBUAT")
