"""BUG-099: Riwayat barang -- kolom saldo = saldo BERJALAN dihitung saat baca (SUM OVER movement_date, created_at, id), urutan
tampilan DESC + id (deterministik bila created_at kembar). Snapshot quantity_balance tulis-waktu (basi oleh gerak mundur) tak dipakai."""
import uuid

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routers import items as RI

PID = str(uuid.uuid4())


class _Conn:
    def __init__(self):
        self.sql = []

    async def fetchrow(self, sql, *a):
        return {"id": PID, "item_code": "MKT-1L", "nama_produk": "Minyakita"}

    async def fetchval(self, sql, *a):
        return 2

    async def fetch(self, sql, *a):
        self.sql.append(sql)
        baris = lambda i, qin, qout, snap, saldo: {  # noqa: E731
            "id": f"r{i}", "movement_type": "SALE", "movement_date": None, "source_type": "INVOICE_FULFILLMENT", "source_id": None,
            "source_number": f"INV-{i}", "quantity_in": qin, "quantity_out": qout, "quantity_balance": snap, "saldo_berjalan": saldo,
            "unit_cost": 0, "total_cost": 0, "average_cost": None, "warehouse_id": None, "batch_id": None, "notes": None,
            "created_at": None, "created_by": None, "counterparty": None}
        return [baris(2, 0, 12, 173, 172), baris(1, 0, 2, 185, 184)]   # snapshot basi (173/185) vs saldo nyata (172/184)

    async def close(self):
        pass


def _klien(monkeypatch, c):
    async def conn():
        return c
    monkeypatch.setattr(RI, "get_db_connection", conn)
    app = FastAPI()

    @app.middleware("http")
    async def masuk(request, call_next):
        request.state.user = {"tenant_id": "t1", "user_id": "00000000-0000-0000-0000-000000000001"}
        return await call_next(request)
    app.include_router(RI.router, prefix="/api")
    return TestClient(app)


def test_riwayat_saldo_berjalan_bukan_snapshot(monkeypatch):
    c = _Conn()
    r = _klien(monkeypatch, c).get(f"/api/items/{PID}/history")
    assert r.status_code == 200, r.text
    gerak = r.json()["data"]   # data = daftar gerak (bentuk lama dipertahankan)
    assert [m["quantity_balance"] for m in gerak] == [172, 184]


def test_riwayat_sql_saldo_saat_baca_dan_urutan_deterministik(monkeypatch):
    c = _Conn()
    _klien(monkeypatch, c).get(f"/api/items/{PID}/history")
    sql = " ".join(c.sql[0].split())
    assert "OVER (ORDER BY il.movement_date, il.created_at, il.id) AS saldo_berjalan" in sql
    assert "ORDER BY il.movement_date DESC, il.created_at DESC, il.id DESC" in sql
