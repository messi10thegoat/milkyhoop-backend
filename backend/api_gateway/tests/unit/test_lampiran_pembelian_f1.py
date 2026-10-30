"""LAMPIRAN-PEMBELIAN F1 (30 Okt 2026, putusan MASTER (ii)) -- GET /api/lampiran/{bill|payment_out}/{id}: keluarga FPB <-> PK, bentuk respons
SAMA dengan Penjualan (susun_dengan_izin), sumber.mode = "keluarga_pembelian".

Yang dijaga:
  * izin rute R purchase_invoice / R send_payment; rute lampiran PK (/bill-payments/{id}/attachments[/download]) yang SUDAH terpetakan
    R send_payment (Unit 1a) tetap jadi izin grup payment_out;
  * url tiap grup Pembelian = rute unduh modul yang SUDAH ADA;
  * SQL keluarga: tiap tabel berpemilik dipaku tenant $2; tabel tanpa tenant_id (bill_payment_allocations, bill_attachments) HANYA dibaca
    lewat id yang sudah tersaring tenant; grup payment_out = JOIN bill_payments_v2 (entity_type 'payment' dipakai bersama penerimaan);
  * FPB membaca DUA sumber (document_attachments 'bill' + bill_attachments);
  * jenis Penjualan tak berubah (JENIS_DOKUMEN == SQL_CALON_SO; jenis Pembelian tak masuk jalur keluarga SO).
Bukti di DB nyata (hub attach PK vs penerimaan, lintas tenant, ROLLBACK): scripts/ops/uji-nyata-lampiran-beli.py."""
import asyncio
import os
import re
import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

os.environ.setdefault("OPENAI_API_KEY", "sk-boneka-unit-test-tanpa-jaringan")

from app.middleware import permission_middleware as pm
from app.routers import lampiran_dokumen as LD
from app.routers import sales_order_lampiran as SL
from tests.unit.test_lampiran_so_keluarga import _pemenang

DOK = uuid.uuid4()
SQL = " ".join(LD.SQL_KELUARGA_BELI.split())


@pytest.mark.parametrize("jalur,harap", [
    (f"/api/lampiran/bill/{DOK}", ("purchase_invoice", "R")),
    (f"/api/lampiran/payment_out/{DOK}", ("send_payment", "R")),
    (f"/api/bill-payments/{DOK}/attachments", ("send_payment", "R")),
    (f"/api/bill-payments/{DOK}/attachments/{DOK}/download", ("send_payment", "R")),
])
def test_izin_rute_pembelian(jalur, harap):
    assert pm.izin_untuk_jalur(jalur) == harap


def test_rute_dimenangkan_handler_lampiran_dokumen():
    for j in ("bill", "payment_out"):
        r = _pemenang(f"/api/lampiran/{j}/{DOK}")
        assert r is not None and r.endpoint.__name__ == "lampiran_dokumen", j


@pytest.mark.parametrize("jenis,modul,akhir", [
    ("bill", "bills", "download_bill_attachment"),
    ("payment_out", "bill_payments", "download_payment_attachment"),
])
def test_url_grup_pembelian_ke_rute_unduh_yang_ada(jenis, modul, akhir):
    r = _pemenang(SL.url_unduh_lampiran(SL.JENIS_KELUARGA[jenis][0], uuid.uuid4(), uuid.uuid4()))
    assert r is not None and r.endpoint.__module__.rsplit(".", 1)[-1] == modul and r.endpoint.__name__ == akhir
    assert pm.izin_untuk_jalur(SL.jalur_izin_jenis(jenis)) is not None   # izin grup terpetakan (bukan OWNER-saja diam-diam)


def test_jenis_penjualan_tak_berubah_dan_pembelian_terpisah():
    assert set(LD.JENIS_DOKUMEN) == set(LD.SQL_CALON_SO)
    # F2a (30 Okt 2026): + purchase_order / vendor_credit -- daftar PERSIS (bukan "memuat"), tetap terpisah dari jenis Penjualan
    assert set(LD.JENIS_PEMBELIAN) == {"purchase_order", "bill", "payment_out", "vendor_credit"}
    assert not (set(LD.JENIS_PEMBELIAN) & set(LD.JENIS_DOKUMEN))
    assert LD.JENIS_PEMBELIAN == {"purchase_order": ("purchase_orders", "po_number"), "bill": ("bills", "invoice_number"),
                                  "payment_out": ("bill_payments_v2", "payment_number"), "vendor_credit": ("vendor_credits", "credit_number")}


_CTE = {"fb0", "pk0", "po0", "nd0", "fb", "pk", "po", "nd", "meta"}   # F2a: + po0/nd0/po/nd
_GLOBAL = {'"User"', "user_profiles"}


def test_sql_keluarga_beli_tiap_tabel_berpemilik_dipaku_tenant():
    tabel = re.findall(r"\b(?:FROM|JOIN)\s+(\"User\"|\w+)\s+(\w+)", SQL)
    salah = [f"{t} {a}" for t, a in tabel if t not in _CTE | _GLOBAL | {"bill_payment_allocations", "bill_attachments"}
             and not re.search(rf"\b{a}\.tenant_id\s*=\s*\$2", SQL)]
    assert salah == [] and {t for t, _ in tabel} >= {"bills", "bill_payments_v2", "document_attachments", "documents",
                                                     "bill_payment_allocations", "bill_attachments", "purchase_orders", "vendor_credits"}


def test_tabel_tanpa_tenant_hanya_lewat_id_tersaring():
    # alokasi: hanya payment_id/bill_id dari benih yang sudah dipaku tenant; hasilnya disaring ulang di fb/pk (bx/px.tenant_id = $2)
    assert "a0x.payment_id IN (SELECT id FROM pk0)" in SQL and "a1x.bill_id IN (SELECT id FROM fb0)" in SQL
    assert "bx.tenant_id = $2" in SQL and "px.tenant_id = $2" in SQL
    # bill_attachments: lewat JOIN bills bb bertenant
    assert "JOIN bills bb ON bb.id = m.id AND bb.tenant_id = $2 JOIN bill_attachments ba ON ba.bill_id = bb.id" in SQL


def test_grup_payment_out_hanya_id_bill_payments_v2_tenant():
    assert "SELECT 'payment_out', 'payment', p.id" in SQL and "FROM bill_payments_v2 p WHERE p.tenant_id = $2" in SQL
    assert "receive_payments" not in SQL
    assert "da.entity_type = m.et AND da.entity_id = m.id AND da.tenant_id = $2" in SQL


def test_benih_terikat_jenis():
    assert "b0x.id = $1 AND $3 = 'bill'" in SQL and "p0x.id = $1 AND $3 = 'payment_out'" in SQL


def test_fpb_dua_sumber():
    assert "'hub'::text AS sumber FROM meta m JOIN document_attachments da" in SQL
    assert "'tagihan_lama' FROM meta m JOIN bills bb" in SQL and SQL.rstrip().endswith("WHERE m.jenis = 'bill'")


class _Conn:
    def __init__(s, ada=True, rows=()):
        s.ada, s.rows, s.sql = ada, list(rows), []

    async def execute(s, *a): return None

    async def fetchrow(s, sql, *a):
        s.sql.append((sql, a))
        return {"nomor": "PAY-1"} if s.ada else None

    async def fetch(s, sql, *a):
        s.sql.append((sql, a))
        return s.rows

    def transaction(s):
        class T:
            async def __aenter__(t): return None
            async def __aexit__(t, *e): return False
        return T()


def _jalan(monkeypatch, conn, jenis="payment_out", tid="t1"):
    class CM:
        async def __aenter__(s): return conn
        async def __aexit__(s, *a): return False

    class P:
        def acquire(s): return CM()

    async def gp(): return P()
    monkeypatch.setattr(SL, "get_pool", gp)

    async def susun(request, rows): return {"success": True, "data": [], "jumlah": 0, "_rows": rows}
    monkeypatch.setattr(SL, "susun_dengan_izin", susun)
    req = SimpleNamespace(state=SimpleNamespace(user={"tenant_id": tid, "user_id": "u"}))
    return asyncio.run(LD.lampiran_dokumen(req, jenis, str(DOK)))


def test_handler_pembelian_pemilik_tenant_lalu_sql_beli(monkeypatch):
    c = _Conn()
    out = _jalan(monkeypatch, c)
    assert out["sumber"] == {"jenis": "payment_out", "id": str(DOK), "nomor": "PAY-1", "mode": "keluarga_pembelian", "sales_order": None}
    (s1, a1), (s2, a2) = c.sql
    assert "FROM bill_payments_v2 WHERE id = $1 AND tenant_id = $2" in s1 and a1 == (DOK, "t1")
    assert s2 == LD.SQL_KELUARGA_BELI and a2 == (DOK, "t1", "payment_out")


def test_handler_pembelian_dokumen_tenant_lain_404_tanpa_sql_keluarga(monkeypatch):
    c = _Conn(ada=False)
    with pytest.raises(HTTPException) as e:
        _jalan(monkeypatch, c, "bill")
    assert e.value.status_code == 404 and len(c.sql) == 1 and "FROM bills WHERE" in c.sql[0][0]


def test_jenis_asing_tetap_404():
    with pytest.raises(HTTPException) as e:
        asyncio.run(LD.lampiran_dokumen(SimpleNamespace(state=SimpleNamespace(user={"tenant_id": "t"})), "bill_payment", str(DOK)))
    assert e.value.status_code == 404
