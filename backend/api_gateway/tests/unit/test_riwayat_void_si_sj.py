"""Riwayat SO: void faktur + Surat Jalan tercatat BERAKTOR (26 Sep 2026, permintaan BACKEND3 via MASTER).

sales_invoices dan invoice_fulfillments tak menyimpan voided_by -> riwayat SO dulu menampilkan
"Faktur ... dibatalkan" / "Surat Jalan ... dibatalkan" tanpa aktor. void_invoice kini menulis
audit_logs lewat catat_riwayat() DI TRANSAKSI YANG SAMA dengan void (Law 12): satu baris
SALES_INVOICE_VOIDED + satu FULFILLMENT_VOIDED per Surat Jalan yang dibatalkan kaskade.
Pembaca (so_riwayat.PADANAN) mengganti baris-kolom tanpa aktor dengan baris audit beraktor.

Penyambungan diperiksa lewat AST handler (helper benar + handler tak memanggil = tetap hijau
bila hanya helper yang diuji); pembacaan diperiksa ujung-ke-ujung: baris yang DITULIS
catat_riwayat dengan argumen handler dibaca balik oleh riwayat_so.
"""
import ast
import json
import os
from datetime import datetime, timezone
from uuid import UUID
from zoneinfo import ZoneInfo

os.environ.setdefault("OPENAI_API_KEY", "sk-boneka-unit-test-tanpa-jaringan")

import pytest  # noqa: E402

import app.routers.sales_invoices as si  # noqa: E402
from app.services import so_riwayat as SR  # noqa: E402

T = "kaos-biru-konveksi"
SOID = UUID("10000000-0000-0000-0000-000000000001")
INV = UUID("20000003-0000-0000-0000-000000000001")
SJ = UUID("20000004-0000-0000-0000-000000000001")
U1, U2 = "00000000-0000-0000-0000-0000000000a1", "00000000-0000-0000-0000-0000000000a2"


def _void():
    tree = ast.parse(open(si.__file__, encoding="utf-8").read())
    for n in ast.walk(tree):
        if isinstance(n, ast.AsyncFunctionDef) and n.name == "void_invoice":
            return n
    raise AssertionError("void_invoice tak ditemukan")


def _tx(fn):
    for n in ast.walk(fn):
        if isinstance(n, ast.AsyncWith) and "conn.transaction" in ast.unparse(n.items[0].context_expr):
            return n
    raise AssertionError("void_invoice tanpa async with conn.transaction()")


def _panggil_riwayat(node, event):
    return [c for c in ast.walk(node)
            if isinstance(c, ast.Call) and getattr(c.func, "id", None) == "catat_riwayat"
            and any(isinstance(a, ast.Constant) and a.value == event for a in c.args)]


def _sql_execute(node, frasa):
    """baris (lineno) conn.execute/fetchval yang SQL-nya memuat frasa."""
    out = []
    for c in ast.walk(node):
        if isinstance(c, ast.Call) and getattr(c.func, "attr", None) in ("execute", "fetchval") and c.args:
            if isinstance(c.args[0], ast.Constant) and frasa in " ".join(str(c.args[0].value).split()):
                out.append(c.lineno)
    return out


def test_void_faktur_dicatat_sekali_di_tx_sesudah_update():
    tx = _tx(_void())
    p = _panggil_riwayat(tx, "SALES_INVOICE_VOIDED")
    assert len(p) == 1, "void_invoice wajib mencatat SALES_INVOICE_VOIDED tepat sekali, DI DALAM transaksi"
    upd = _sql_execute(tx, "UPDATE sales_invoices SET status = 'void'")
    assert upd and upd[0] < p[0].lineno, "catat sesudah UPDATE status void (bukan sebelum: void bisa gagal)"
    a = [ast.unparse(x) for x in p[0].args]
    assert a[1] == "ctx['tenant_id']" and a[2] == "'sales_invoices'" and a[3] == "invoice_id", a
    assert a[4] == "invoice['invoice_number']" and "user_id" in a[6], a


def test_void_surat_jalan_dicatat_per_sj_di_loop_kaskade():
    tx = _tx(_void())
    loop = [n for n in ast.walk(tx) if isinstance(n, ast.For) and ast.unparse(n.iter) == "fulfillments"
            and _sql_execute(n, "UPDATE invoice_fulfillments SET status='voided'")]
    assert len(loop) == 1, "loop kaskade void Surat Jalan tak ditemukan"
    p = _panggil_riwayat(loop[0], "FULFILLMENT_VOIDED")
    assert len(p) == 1, "FULFILLMENT_VOIDED wajib dicatat SEKALI PER Surat Jalan (di dalam loop kaskade)"
    assert _sql_execute(loop[0], "UPDATE invoice_fulfillments SET status='voided'")[0] < p[0].lineno
    a = [ast.unparse(x) for x in p[0].args]
    assert a[2] == "'invoice_fulfillments'" and a[3] == "f['id']" and a[4] == "f['fulfillment_number']", a
    assert "user_id" in a[6], a
    assert len(_panggil_riwayat(_void(), "FULFILLMENT_VOIDED")) == 1


def test_query_kaskade_membawa_fulfillment_number():
    s = " ".join(ast.unparse(_void()).split())
    assert "SELECT id, fulfillment_number, fulfillment_date, journal_id" in s


class _Tulis:
    def __init__(self):
        self.rows = []

    async def execute(self, sql, *a):
        assert "INSERT INTO audit_logs" in sql
        uid, ev, et, eid, en, tid, src, meta = a
        self.rows.append({"id": str(len(self.rows)), "createdAt": datetime(2026, 9, 26, 9, tzinfo=timezone.utc),
                          "eventType": ev, "userId": uid, "entity_type": et, "entity_id": UUID(eid),
                          "entity_number": en, "tenant_id": tid, "metadata": json.loads(meta)})


class _Baca:
    """riwayat_so atas SO dengan 1 faktur + 1 SJ yang SUDAH void (kolom tanpa aktor)."""

    def __init__(self, audit):
        self.audit = audit

    async def fetchrow(self, sql, *a):
        return {"id": SOID, "order_number": "SO-1", "created_at": datetime(2026, 9, 26, 1, tzinfo=timezone.utc),
                "created_by": UUID(U1), "confirmed_at": None, "confirmed_by": None}

    async def fetch(self, sql, *a):
        t = datetime(2026, 9, 26, 9, tzinfo=timezone.utc)
        if "FROM sales_invoices" in sql:
            return [{"id": INV, "invoice_number": "INV-1", "total_amount": 1, "created_at": t, "created_by": UUID(U2),
                     "posted_at": t, "posted_by": UUID(U2), "voided_at": t, "voided_reason": "salah harga"}]
        if "FROM invoice_fulfillments" in sql:
            return [{"id": SJ, "fulfillment_number": "SJ-1", "created_at": t, "created_by": UUID(U2),
                     "posted_at": t, "posted_by": UUID(U2), "voided_at": t, "voided_reason": "salah harga",
                     "invoice_number": "INV-1"}]
        if "FROM audit_logs" in sql:
            pasang = set(zip(a[1], a[2]))
            return [r for r in self.audit if (r["entity_type"], str(r["entity_id"])) in pasang and r["tenant_id"] == a[0]]
        if 'FROM "User"' in sql:
            return [{"id": U1, "nama": "Anton"}, {"id": U2, "nama": "Staf"}]
        return []


@pytest.fixture(autouse=True)
def _zona(monkeypatch):
    async def z(conn, tid):
        return ZoneInfo("Asia/Jakarta")
    monkeypatch.setattr(SR, "zona_tenant", z)


async def _semua(m):
    return True


@pytest.mark.asyncio
async def test_ujung_ke_ujung_void_tampil_sekali_beraktor():
    w = _Tulis()
    # argumen PERSIS seperti handler (entity_type/event/nomor)
    await SR.catat_riwayat(w, T, "invoice_fulfillments", SJ, "SJ-1", "FULFILLMENT_VOIDED", U1,
                           "Surat Jalan SJ-1 dibatalkan: salah harga", {"reason": "salah harga"})
    await SR.catat_riwayat(w, T, "sales_invoices", INV, "INV-1", "SALES_INVOICE_VOIDED", U1,
                           "Faktur INV-1 dibatalkan (void): salah harga", {"reason": "salah harga"})
    d = await SR.riwayat_so(_Baca(w.rows), T, SOID, _semua)
    batal = [e for e in d["events"] if "dibatalkan" in e["ringkas"]]
    assert sorted(e["jenis"] for e in batal) == ["FULFILLMENT_VOIDED", "SALES_INVOICE_VOIDED"]
    assert all(e["aktor"] == {"id": U1, "nama": "Anton"} and e["sumber"] == "audit" for e in batal)


@pytest.mark.asyncio
async def test_tanpa_audit_baris_kolom_tetap_tampil_tanpa_aktor():
    d = await SR.riwayat_so(_Baca([]), T, SOID, _semua)
    batal = {e["jenis"]: e for e in d["events"] if "dibatalkan" in e["ringkas"]}
    assert set(batal) == {"FAKTUR_DIBATALKAN", "SURAT_JALAN_DIBATALKAN"}
    assert all(e["aktor"] is None for e in batal.values())
