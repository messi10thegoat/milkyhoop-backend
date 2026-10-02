"""Penawaran -> SO: satu penawaran = paling banyak SATU SO (2 Okt 2026, MASTER: klik ganda dulu = 2 SO).
Diuji nyata di salinan DB: dua permintaan BERSAMAAN -> 1 SO, keduanya menjawab SO yang sama. Di sini: kunci diambil
SEBELUM membaca, baris dikunci FOR UPDATE, dan penawaran yang sudah dikonversi mengembalikan SO lama tanpa INSERT."""
import asyncio
import uuid

from app.routers import quotes as Q

QID = "11111111-2222-3333-4444-555555555555"
SO_ID = uuid.UUID("99999999-8888-7777-6666-555555555555")


class _Conn:
    def __init__(self, quote):
        self.quote, self.log = quote, []

    def transaction(self):
        class _T:
            async def __aenter__(s): return None
            async def __aexit__(s, *a): return False
        return _T()

    async def execute(self, sql, *a):
        self.log.append(("execute", sql, a))
        return "OK"

    async def fetchrow(self, sql, *a):
        self.log.append(("fetchrow", sql, a))
        if "FROM quotes" in sql:
            return self.quote
        if "FROM sales_orders" in sql:
            return {"id": SO_ID, "order_number": "SO-2610-0007"}
        return None

    async def fetch(self, sql, *a):
        self.log.append(("fetch", sql, a))
        return []

    async def fetchval(self, sql, *a):
        self.log.append(("fetchval", sql, a))
        return None


def _jalan(monkeypatch, quote):
    conn = _Conn(quote)

    class _P:
        def acquire(self):
            class _A:
                async def __aenter__(s): return conn
                async def __aexit__(s, *a): return False
            return _A()

    async def gp(): return _P()
    monkeypatch.setattr(Q, "get_pool", gp)
    monkeypatch.setattr(Q, "get_user_context", lambda r: {"tenant_id": "t1", "user_id": None})
    hasil = asyncio.run(Q.convert_to_sales_order(object(), QID, None))
    return conn, hasil


def test_kunci_sebelum_baca_dan_for_update(monkeypatch):
    conn, _ = _jalan(monkeypatch, {"id": uuid.UUID(QID), "status": "converted",
                                   "converted_to_type": "sales_order", "converted_to_id": SO_ID})
    pertama = conn.log[0]
    assert pertama[0] == "execute" and "pg_advisory_xact_lock" in pertama[1] and pertama[2] == (f"QUOTE:t1:{QID}",)
    baca = next(x for x in conn.log if x[0] == "fetchrow" and "FROM quotes" in x[1])
    assert "FOR UPDATE" in baca[1]


def test_sudah_dikonversi_mengembalikan_so_lama_tanpa_insert(monkeypatch):
    conn, hasil = _jalan(monkeypatch, {"id": uuid.UUID(QID), "status": "converted",
                                       "converted_to_type": "sales_order", "converted_to_id": SO_ID})
    assert hasil.data["sales_order_id"] == str(SO_ID) and hasil.data["already_converted"] is True
    assert not any("INSERT" in x[1] for x in conn.log)
    assert not any("generate_sales_order_number" in x[1] for x in conn.log)
