"""Regresi 26 Sep 2026 (unit kosakata 4ab841ca): GET /api/items/{id}/history -> 500 SEMUA tenant.

Kueri riwayat barang memuat "{sql_daftar(KELUAR_JUAL_FAKTUR)}" di string BIASA (bukan f-string) -> placeholder
terkirim MENTAH ke Postgres ("syntax error at or near {"). Gerbang lolos karena tak ada tes yang MENJALANKAN
handler ini; probe unit itu hanya mengukur top-products.

(1) handler DIJALANKAN dengan koneksi tiruan: SQL yang sampai ke DB tanpa '{', daftar kosakata tersisip.
(2) penjaga SELURUH app: literal string biasa (bukan f-string/docstring/.format) yang memuat placeholder
    kosakata ledger / pemanggilan sql_* = MERAH. Kontrol merah pemindainya ikut diuji.
"""
import ast
import pathlib
import re
import uuid
from datetime import datetime
from types import SimpleNamespace

import pytest

import app.routers.items as IT
from app.services.kosakata_ledger import KELUAR_JUAL_FAKTUR, sql_daftar

APP = pathlib.Path(IT.__file__).resolve().parents[1]
POLA = re.compile(r"\{\s*(sql_[a-z_]+\(|KELUAR_JUAL|BATAL_JUAL|BUKAN_JUAL)")


class Conn:
    def __init__(self):
        self.sql = []

    async def fetchrow(self, sql, *a):
        self.sql.append(sql)
        return {"id": a[0], "item_code": "K-1", "nama_produk": "Kaos"}

    async def fetchval(self, sql, *a):
        self.sql.append(sql)
        return 1

    async def fetch(self, sql, *a):
        self.sql.append(sql)
        return [{"id": uuid.uuid4(), "movement_type": "OUT", "movement_date": datetime(2026, 9, 26),
                 "source_type": "INVOICE_FULFILLMENT", "source_id": uuid.uuid4(), "source_number": "SJ-1",
                 "quantity_in": 0, "quantity_out": 2, "quantity_balance": 8, "unit_cost": 1, "total_cost": 2,
                 "average_cost": 1, "warehouse_id": None, "batch_id": None, "notes": None,
                 "created_at": datetime(2026, 9, 26), "created_by": None, "counterparty": "Toko Merdeka"}]

    async def close(self):
        pass


@pytest.mark.asyncio
async def test_riwayat_barang_sql_terkirim_tanpa_placeholder_mentah(monkeypatch):
    c = Conn()

    async def _conn():
        return c
    monkeypatch.setattr(IT, "get_db_connection", _conn)
    monkeypatch.setattr(IT, "get_tenant_id", lambda r: "kaos-biru-konveksi")
    r = await IT.get_item_history(SimpleNamespace(), uuid.uuid4(), page=1, limit=5)
    utama = [s for s in c.sql if "FROM inventory_ledger il" in s]
    assert utama, "kueri riwayat tak dijalankan"
    for s in c.sql:
        assert "{" not in s and "}" not in s, s[:200]
    assert f"il.source_type IN {sql_daftar(KELUAR_JUAL_FAKTUR)}" in " ".join(utama[0].split())
    assert r["data"][0]["counterparty"] == "Toko Merdeka" and r["data"][0]["source_type"] == "INVOICE_FULFILLMENT"


def _placeholder_mentah(sumber: str):
    t = ast.parse(sumber)
    induk = {c: a for a in ast.walk(t) for c in ast.iter_child_nodes(a)}
    temuan = []
    for n in ast.walk(t):
        if not (isinstance(n, ast.Constant) and isinstance(n.value, str)):
            continue
        par = induk.get(n)
        if isinstance(par, (ast.JoinedStr, ast.Expr)):
            continue
        if isinstance(par, ast.Attribute) and par.attr == "format":
            continue
        if POLA.search(n.value):
            temuan.append((n.lineno, POLA.search(n.value).group(0)))
    return temuan


def test_pemindai_bisa_merah():
    buruk = 'q = """SELECT 1 WHERE x IN {sql_daftar(KELUAR_JUAL)}"""\nw = "... {KELUAR_JUAL_FAKTUR}"\n'
    assert len(_placeholder_mentah(buruk)) == 2
    baik = ('q = f"""SELECT 1 WHERE x IN {sql_daftar(KELUAR_JUAL)}"""\n'
            'def f():\n    """doc {sql_daftar(x)}"""\n'
            'z = "IN {sql_x()}".format()\n')
    assert _placeholder_mentah(baik) == []


def test_seluruh_app_tanpa_placeholder_kosakata_mentah():
    temuan = []
    for p in sorted(APP.rglob("*.py")):
        for baris, cuplik in _placeholder_mentah(p.read_text(encoding="utf-8")):
            temuan.append(f"{p.relative_to(APP)}:{baris}: {cuplik}")
    assert len(list(APP.rglob("*.py"))) > 100, "pemindai tak melihat app (jalur salah = nol palsu)"
    assert temuan == [], temuan
